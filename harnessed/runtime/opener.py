from time import monotonic
from uuid import uuid4

from harnessed.children.contracts import ChildSessionRequest
from harnessed.children.reports import ChildReports
from harnessed.errors import ConfigError
from harnessed.events import TurnStartedEvent
from harnessed.kernel import Scope
from harnessed.models.assets import AssetStore, MemoryAssets
from harnessed.output.contracts import OutputTypes
from harnessed.sessions import CommitHandler, SessionCheckpoint, SessionContext, SessionState
from harnessed.sessions.files import MemoryFiles, SessionFiles
from harnessed.sessions.log import SessionLog
from harnessed.sessions.values import PluginValues
from harnessed.tools import ToolRegistry
from harnessed.tracing import bind_tracer

from .agent_loop import AgentLoop
from .composition import PluginValidation, validate_plugins
from .hooks import SessionOpened
from .plugin import Service
from .session import Session


async def open_session(
    scope: Scope,
    state: SessionState | SessionCheckpoint | None,
    *,
    loop: AgentLoop,
    plugin_validation: PluginValidation,
    on_commit: CommitHandler | None = None,
    id: str | None = None,
) -> Session:
    """Open fresh runtime resources around an application-owned conversation value."""
    if state is not None and not isinstance(state, SessionState | SessionCheckpoint):
        raise ConfigError("state must be a SessionState, a SessionCheckpoint or None")
    if id is not None and (not isinstance(id, str) or not id.strip()):
        raise ConfigError("Conversation IDs must be non-empty strings")
    if on_commit is not None and not callable(on_commit):
        raise ConfigError("on_commit must be an async callable")
    if isinstance(state, SessionCheckpoint):
        if id is not None and id != state.session_id:
            raise ConfigError("id must match the checkpoint's session_id")
        checkpoint: SessionCheckpoint | None = state
        selected: SessionState | None = state.state
        session_id = state.session_id
    else:
        checkpoint = None
        selected = state
        session_id = uuid4().hex if id is None else id
    log = SessionLog(
        session_id,
        scope=scope,
        state=selected,
        on_commit=on_commit,
        assets=(
            MemoryAssets((*checkpoint.state.assets, *checkpoint.assets))
            if checkpoint is not None
            else None
        ),
        files=(
            MemoryFiles((*checkpoint.state.files, *checkpoint.files))
            if checkpoint is not None
            else None
        ),
    )
    scope.provide(AssetStore, log.assets)
    scope.provide(SessionFiles, log.files)
    scope.effect(log.close_resources)
    scope.effect(log.finish_subscriptions)
    if ChildSessionRequest in scope:
        await log.append(scope[ChildSessionRequest].link)
        await log.flush()
    if checkpoint is not None:
        await log.restore(checkpoint)
        _restore_output_schemas(scope[OutputTypes], log)
        await log.repair_interrupted()
    elif selected is not None:
        await log.repair_interrupted()
    runtime = loop.create(scope, log)
    try:
        await scope.install(
            [
                Service(SessionLog, log),
                Service(SessionContext, runtime.plugin_context),
                Service(PluginValues, PluginValues(log)),
            ]
        )
    except BaseException:
        await runtime.aclose()
        raise
    scope.effect(runtime.aclose)
    if ChildSessionRequest in scope:
        reporter = scope[ChildSessionRequest].reporter
        if reporter is not None:
            await scope.plugin(ChildReports(reporter))
    await scope.settle()
    validate_plugins(scope, plugin_validation)
    scope[ToolRegistry].validate_references()
    with bind_tracer(scope):
        await scope.dispatch(SessionOpened(runtime.plugin_context, at=monotonic()))
    runtime.start()
    return runtime


def _restore_output_schemas(outputs: OutputTypes, log: SessionLog) -> None:
    schemas = [item.output_schema for item in log.inbox.pending]
    start = log.last(TurnStartedEvent)
    if log.open_turn is not None and start is not None:
        schemas.append(start.output_schema)
    for schema in schemas:
        if schema is not None:
            outputs.resolve(schema)
