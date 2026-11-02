from typing import assert_type

from harnessed.events import (
    CompactionFinishedEvent,
    CompactionStartedEvent,
    ModelAttemptFinishedEvent,
    ModelAttemptStartedEvent,
    ModelRetryScheduledEvent,
    SessionEvent,
)
from harnessed.kernel import Bail, Context, Emit, Inject, Next, PluginBase, Waterfall, hook


class Notice(Emit):
    pass


class Other(Emit):
    pass


class Question(Bail[int]):
    pass


class Transform(Waterfall[str]):
    pass


class OtherQuestion(Bail[int]):
    pass


class OtherTransform(Waterfall[str]):
    pass


type TerminalEvent = (
    SessionEvent
    | Notice
    | CompactionStartedEvent
    | CompactionFinishedEvent
    | ModelAttemptStartedEvent
    | ModelRetryScheduledEvent
    | ModelAttemptFinishedEvent
)


class TerminalEvents(PluginBase):
    @hook(
        Notice,
        CompactionStartedEvent,
        CompactionFinishedEvent,
        ModelAttemptStartedEvent,
        ModelRetryScheduledEvent,
        ModelAttemptFinishedEvent,
    )
    def forward(self, event: TerminalEvent) -> None:
        pass


@hook(Notice, Other)
async def observe_many(event: Notice | Other, dependency: Inject[int]) -> None:
    assert_type(dependency, int)


@hook(Question, OtherQuestion)
def answer_many(event: Question | OtherQuestion) -> int | None:
    return 42


@hook(Transform, OtherTransform)
async def transform_many(event: Transform | OtherTransform, next_: Next[str]) -> str:
    return await next_()


@hook(Notice)
async def observe(event: Notice, dependency: Inject[int]) -> None:
    assert_type(dependency, int)


@hook(Question)
async def answer(event: Question) -> int | None:
    return None


@hook(Transform)
async def transform(event: Transform, next_: Next[str]) -> str:
    return await next_()


class Hooks(PluginBase, needs=(str,)):
    @hook(Notice, Other)
    def observe_many(self, event: Notice | Other, value: Inject[str]) -> None:
        pass

    @hook(Notice)
    def observe(self, event: Notice) -> None:
        pass

    @hook(Question)
    async def answer(self, event: Question) -> int | None:
        return 42

    @hook(Transform)
    async def transform(self, event: Transform, next_: Next[str], value: Inject[str]) -> str:
        return value + await next_()


def wrong_event(event: Other) -> None:
    pass


def wrong_observer_result(event: Notice) -> int:
    return 42


async def wrong_answer(event: Question) -> str:
    return "wrong"


async def wrong_continuation(event: Transform, next_: Next[int]) -> str:
    return str(await next_())


async def wrong_transform_result(event: Transform, next_: Next[str]) -> int:
    return 42


def wrong_shared_observer_result(event: Notice | Other) -> int:
    return 42


async def wrong_shared_answer(event: Question | OtherQuestion) -> str:
    return "wrong"


async def wrong_shared_continuation(event: Transform | OtherTransform, next_: Next[int]) -> str:
    return str(await next_())


async def wrong_shared_transform_result(event: Transform | OtherTransform, next_: Next[str]) -> int:
    return 42


async def consumer(ctx: Context) -> None:
    ctx.on(Notice, lambda event: None)
    ctx.on(Question, answer)
    ctx.on(Transform, transform)

    @ctx.hook(Transform)
    async def owned(event: Transform, next_: Next[str]) -> str:
        return await next_()

    async def terminal() -> str:
        return "end"

    assert_type(await transform(Transform(), terminal), str)
    assert_type(await owned(Transform(), terminal), str)
    assert_type(await Hooks().transform(Transform(), terminal, "prefix"), str)
    assert_type(await observe_many(Other(), 42), None)
    assert_type(answer_many(OtherQuestion()), int | None)
    assert_type(await transform_many(OtherTransform(), terminal), str)
    assert_type(Hooks().observe_many(Other(), "value"), None)
    assert_type(TerminalEvents().forward(Notice()), None)

    hook(Notice, Other)(wrong_event)  # ty: ignore[no-matching-overload]
    hook(Other, Notice)(wrong_event)  # ty: ignore[no-matching-overload]
    hook(Notice, Other, Question)(observe_many)  # ty: ignore[no-matching-overload]
    hook(Notice, Other)(wrong_shared_observer_result)  # ty: ignore[no-matching-overload]
    hook(Question, OtherQuestion)(wrong_shared_answer)  # ty: ignore[no-matching-overload]
    hook(Transform, OtherTransform)(wrong_shared_continuation)  # ty: ignore[no-matching-overload]
    hook(Transform, OtherTransform)(wrong_shared_transform_result)  # ty: ignore[no-matching-overload]

    hook(Notice)(wrong_event)  # ty: ignore[no-matching-overload]
    hook(Notice)(wrong_observer_result)  # ty: ignore[no-matching-overload]
    hook(Question)(wrong_answer)  # ty: ignore[no-matching-overload]
    hook(Transform)(wrong_continuation)  # ty: ignore[no-matching-overload]
    hook(Transform)(wrong_transform_result)  # ty: ignore[no-matching-overload]
    ctx.hook(Notice)(wrong_event)  # ty: ignore[no-matching-overload]
    ctx.hook(Transform)(wrong_transform_result)  # ty: ignore[no-matching-overload]
    ctx.on(Notice, wrong_observer_result)  # ty: ignore[no-matching-overload]
    ctx.on(Transform, wrong_continuation)  # ty: ignore[no-matching-overload]
    ctx.add_listener(Question, wrong_answer)  # ty: ignore[invalid-argument-type]


class InvalidHooks(PluginBase):
    @hook(Notice, Other)  # ty: ignore[no-matching-overload]
    def wrong_shared_event(self, event: Notice) -> None:
        pass

    @hook(Notice)  # ty: ignore[no-matching-overload]
    async def wrong_event(self, event: Other) -> None:
        pass

    @hook(Transform)  # ty: ignore[no-matching-overload]
    async def wrong_result(self, event: Transform, next_: Next[str]) -> int:
        return 42
