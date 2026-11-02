from uuid import uuid4

from harnessed.core.assets import AssetStore, _MemoryAssets
from harnessed.core.composition import PluginValidation, validate_plugins
from harnessed.core.errors import ConfigError
from harnessed.core.event_registry import EventRegistry
from harnessed.core.outputs import OutputTypes
from harnessed.core.plugin import Service
from harnessed.core.prompt.context import DynamicContext
from harnessed.core.runtime.agent_loop import AgentFactory
from harnessed.core.runtime.session import Session
from harnessed.core.session.checkpoint import SessionCheckpoint
from harnessed.core.session.events import TurnStartedEvent
from harnessed.core.session.files import MemoryFiles, SessionFiles
from harnessed.core.session.session import SessionLog
from harnessed.core.session.state import SessionState
from harnessed.core.session.store import SessionPersistence, SessionStore
from harnessed.core.tools import ToolRegistry
from harnessed.kernel import Context


async def open_session(
    ctx: Context,
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
    if id is not None and store is None:
        raise ConfigError("Opening a conversation by ID requires an explicit store")
    session_id = uuid4().hex if id is None else id
    stored = await store.load_session(session_id) if store is not None else None
    if stored is not None and state is not None:
        raise ConfigError("An existing stored conversation cannot also receive state")
    checkpoint = (
        SessionCheckpoint.from_json(stored.payload, registry=ctx[EventRegistry])
        if stored is not None
        else None
    )
    selected = checkpoint.state if checkpoint is not None else state
    persistence = (
        SessionPersistence(store, session_id, stored.revision if stored is not None else None)
        if store is not None
        else None
    )
    log = SessionLog(
        session_id,
        ctx=ctx,
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
    ctx.provide(AssetStore, log.assets)
    ctx.provide(SessionFiles, log.files)
    ctx.effect(log.close_resources)
    ctx.effect(log.finish_subscriptions)
    if checkpoint is not None:
        await log.restore(checkpoint)
        _restore_output_schemas(ctx[OutputTypes], log)
        await log.repair_interrupted()
    runtime = ctx[AgentFactory].create(ctx, log)
    try:
        await ctx.install(
            [
                Service(SessionLog, log),
                Service(Session, runtime),
                Service(DynamicContext, runtime.context),
            ]
        )
    except BaseException:
        await runtime.aclose()
        raise
    ctx.effect(runtime.aclose)
    await ctx.settle()
    validate_plugins(ctx, plugin_validation)
    ctx[ToolRegistry].validate_references()
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
