from uuid import uuid4

from harnessed.core.assets import AssetStore, _MemoryAssets
from harnessed.core.children import ChildSessionRequest
from harnessed.core.composition import PluginValidation, validate_plugins
from harnessed.core.errors import ConfigError
from harnessed.core.events.turn import TurnStartedEvent
from harnessed.core.outputs import OutputTypes
from harnessed.core.plugin import Service
from harnessed.core.prompt.context import DynamicContext
from harnessed.core.runtime.agent_loop import AgentFactory
from harnessed.core.runtime.child_reports import ChildReports
from harnessed.core.runtime.context import SessionContext
from harnessed.core.runtime.session import Session
from harnessed.core.session.files import MemoryFiles, SessionFiles
from harnessed.core.session.session import SessionLog
from harnessed.core.session.state import SessionState
from harnessed.core.session.store import SessionPersistence, SessionStore
from harnessed.core.tools import ToolRegistry
from harnessed.kernel import Scope


async def open_session(
    scope: Scope,
    state: SessionState | None,
    *,
    plugin_validation: PluginValidation,
    store: SessionStore | None = None,
    id: str | None = None,
) -> Session:
    """Open fresh runtime resources around an application-owned conversation value."""
    if state is not None and not isinstance(state, SessionState):
        raise ConfigError("state must be a SessionState or None")
    if id is not None and (not isinstance(id, str) or not id.strip()):
        raise ConfigError("Conversation IDs must be non-empty strings")
    if id is not None and store is None and ChildSessionRequest not in scope:
        raise ConfigError("Opening a conversation by ID requires an explicit store")
    session_id = uuid4().hex if id is None else id
    stored = await store.load_session(session_id) if store is not None else None
    if ChildSessionRequest in scope and (stored is not None or state is not None):
        raise ConfigError("A delegation cannot reopen an existing child session")
    if stored is not None and state is not None:
        raise ConfigError("An existing stored conversation cannot also receive state")
    checkpoint = stored.checkpoint if stored is not None else None
    selected = checkpoint.state if checkpoint is not None else state
    persistence = (
        SessionPersistence(store, session_id, stored.revision if stored is not None else None)
        if store is not None
        else None
    )
    log = SessionLog(
        session_id,
        scope=scope,
        state=selected,
        persistence=persistence,
        assets=(
            _MemoryAssets((*checkpoint.state.assets, *checkpoint.assets))
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
    runtime = scope[AgentFactory].create(scope, log)
    try:
        await scope.install(
            [
                Service(SessionLog, log),
                Service(SessionContext, runtime.plugin_context),
                Service(DynamicContext, DynamicContext(log)),
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
