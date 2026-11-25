from time import monotonic
from uuid import uuid4

from harnessed.children import ChildSessionRequest
from harnessed.children.reports import ChildReports
from harnessed.errors import ConfigError, SessionFormatError
from harnessed.events import TurnStartedEvent
from harnessed.kernel import PluginSpec, Scope
from harnessed.models.assets import AssetStore, MemoryAssets
from harnessed.output.contracts import OutputTypes
from harnessed.sessions import SessionContext, SessionRecovery, SessionState, SessionStore
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
    state: SessionState | SessionRecovery | None,
    *,
    loop: AgentLoop,
    pending: tuple[PluginSpec, ...],
    plugin_validation: PluginValidation,
    store: SessionStore | None = None,
    commit_timeout_seconds: float | None = None,
    id: str | None = None,
) -> Session:
    """Open fresh runtime resources around an application-owned conversation value."""
    _check_options(state, id, store=store, commit_timeout_seconds=commit_timeout_seconds)
    if store is not None and id is not None and ChildSessionRequest not in scope:
        state = await _read_stored(store, id, state)
    if isinstance(state, SessionRecovery):
        if id is not None and id != state.session_id:
            raise ConfigError("id must match the recovery value's session_id")
        recovery: SessionRecovery | None = state
        selected: SessionState | None = state.state
        session_id = state.session_id
    else:
        recovery = None
        selected = state
        session_id = uuid4().hex if id is None else id
    log = SessionLog(
        session_id,
        scope=scope,
        state=selected,
        store=store,
        commit_timeout_seconds=commit_timeout_seconds,
        assets=(
            MemoryAssets((*recovery.state.assets, *recovery.assets))
            if recovery is not None
            else None
        ),
        files=(
            MemoryFiles((*recovery.state.files, *recovery.files)) if recovery is not None else None
        ),
    )
    scope.provide(AssetStore, log.assets)
    scope.provide(SessionFiles, log.files)
    scope.effect(log.close_resources)
    scope.effect(log.finish_subscriptions)
    if ChildSessionRequest in scope:
        await log.append(scope[ChildSessionRequest].link)
        await log.flush()
    if recovery is not None:
        await log.restore(recovery)
        _restore_output_schemas(scope[OutputTypes], log)
    runtime = loop.create(scope, log)
    try:
        await scope.install(
            [
                Service(SessionLog, log),
                Service(SessionContext, runtime.plugin_context),
                Service(PluginValues, PluginValues(log)),
                *pending,
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
    if selected is not None:
        # Repair once every plugin is active, so all of them observe the records.
        await log.repair_interrupted()
    with bind_tracer(scope):
        await scope.dispatch(SessionOpened(runtime.plugin_context, at=monotonic()))
    runtime.start()
    return runtime


def _check_options(
    state: object,
    id: str | None,
    *,
    store: SessionStore | None,
    commit_timeout_seconds: float | None,
) -> None:
    if state is not None and not isinstance(state, SessionState | SessionRecovery):
        raise ConfigError("state must be a SessionState, a SessionRecovery or None")
    if id is not None and (not isinstance(id, str) or not id.strip()):
        raise ConfigError("Conversation IDs must be non-empty strings")
    if store is not None and not isinstance(store, SessionStore):
        raise ConfigError("store must implement SessionStore: async read() and write()")
    if store is not None and isinstance(state, SessionRecovery):
        raise ConfigError("A store reopens sessions by id; pass id= instead of a SessionRecovery")
    if commit_timeout_seconds is not None and not commit_timeout_seconds > 0:
        raise ConfigError("commit_timeout_seconds must be positive or None")


async def _read_stored(
    store: SessionStore, session_id: str, state: SessionState | SessionRecovery | None
) -> SessionState | SessionRecovery | None:
    """Reopen what the store holds for ``session_id``; a new session keeps ``state``."""
    stored = await store.read(session_id)
    if stored is None:
        return state
    if state is not None:
        raise ConfigError(f"Session {session_id!r} is already stored; open it without state=")
    recovery = stored.recovery()
    if recovery.session_id != session_id:
        raise SessionFormatError(f"The store returned another session for {session_id!r}")
    return recovery


def _restore_output_schemas(outputs: OutputTypes, log: SessionLog) -> None:
    schemas = [item.output_schema for item in log.inbox.pending]
    start = log.last(TurnStartedEvent)
    if log.open_turn is not None and start is not None:
        schemas.append(start.output_schema)
    for schema in schemas:
        if schema is not None:
            outputs.resolve(schema)
