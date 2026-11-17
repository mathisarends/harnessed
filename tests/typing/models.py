from contextlib import AbstractAsyncContextManager
from typing import assert_type

from harnessed import Agent, Session
from harnessed.hooks import Next, StreamModel
from harnessed.kernel import Scope, hook
from harnessed.models import (
    GenerationSettings,
    Model,
    ModelInfo,
    ModelRequest,
    ModelStream,
    PydanticAIModel,
)
from harnessed.services import SessionContext
from harnessed.testing import ScriptedModel


async def generation_settings_consumer(session: Session) -> None:
    settings = GenerationSettings(max_tokens=4096, temperature=0.2)
    model: Model = ScriptedModel([], settings=settings)
    assert_type(model.settings, GenerationSettings)
    assert_type(model.info, ModelInfo)
    assert_type(ModelRequest((), settings=settings).settings.max_tokens, int | None)
    Agent(model=model)
    PydanticAIModel("anthropic:fixture", settings=settings)
    await session.run("Short", settings=GenerationSettings(max_tokens=512))
    GenerationSettings(max_tokens="4096")  # ty: ignore[invalid-argument-type]
    ModelRequest((), model="different")  # ty: ignore[unknown-argument]
    ModelRequest((), max_tokens=10)  # ty: ignore[unknown-argument]
    PydanticAIModel("anthropic:fixture", settings={})  # ty: ignore[invalid-argument-type]
    await session.run("Short", settings={})  # ty: ignore[invalid-argument-type]


@hook(StreamModel)
async def delegate_stream(
    event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
) -> AbstractAsyncContextManager[ModelStream]:
    assert_type(event.session, SessionContext)
    assert_type(event.request, ModelRequest)
    assert_type(event.model, Model)
    assert_type(event.request_id, str)
    manager = await next_()
    assert_type(manager, AbstractAsyncContextManager[ModelStream])
    return manager


def wrong_stream_result(
    event: StreamModel, next_: Next[AbstractAsyncContextManager[ModelStream]]
) -> str:
    return "not a stream context manager"


async def stream_hook_consumer(scope: Scope, event: StreamModel) -> None:
    scope.on(StreamModel, delegate_stream)
    manager = await scope.dispatch(
        StreamModel(event.session, event.request, event.model, request_id=event.request_id),
        default=lambda: event.model.stream(event.request),
    )
    assert_type(manager, AbstractAsyncContextManager[ModelStream])
    hook(StreamModel)(wrong_stream_result)  # ty: ignore[no-matching-overload]
