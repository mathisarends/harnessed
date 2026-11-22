import inspect
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated

from pydantic import AfterValidator, ConfigDict, Field
from pydantic.dataclasses import dataclass

from harnessed.errors import ConfigError
from harnessed.kernel import PluginScope
from harnessed.models import Model
from harnessed.models.values import VALUE_CONFIG

from .budget import ContextBudget
from .compaction import (
    CompactionPolicy,
    CompactionPrompt,
    CompactionSettings,
    Summarizer,
)
from .meter import ContextState

_DEFAULT_RATIO = 0.8


@dataclass(frozen=True, config=VALUE_CONFIG)
class _AtRatio:
    at: Annotated[float, Field(gt=0, le=1)]
    fallback_tokens: Annotated[int, Field(ge=1)] | None = None

    def __call__(self, state: ContextState) -> bool:
        if state.ratio is None:
            if self.fallback_tokens is not None:
                return state.input_tokens + state.reserve_tokens >= self.fallback_tokens
            raise ConfigError("Ratio compaction requires a known or configured context window")
        return state.ratio >= self.at


@dataclass(frozen=True, config=VALUE_CONFIG)
class _EveryTurns:
    every: Annotated[int, Field(ge=1)]

    def __call__(self, state: ContextState) -> bool:
        return state.turns_since_compaction >= self.every


def at_ratio(at: float = _DEFAULT_RATIO) -> Callable[[ContextState], bool]:
    """Compact when input plus reserved output reaches this window ratio."""
    return _AtRatio(at)


def every_turns(every: int) -> Callable[[ContextState], bool]:
    """Compact after this many newly completed turns."""
    return _EveryTurns(every)


def _sync_trigger(value: Callable[[ContextState], bool]) -> Callable[[ContextState], bool]:
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(type(value).__call__):
        raise ValueError("Compaction triggers must be synchronous")
    return value


def _select_trigger(
    trigger: Callable[[ContextState], bool] | None,
    at: float | None,
    fallback_tokens: int | None,
) -> Callable[[ContextState], bool]:
    if trigger is None:
        return _AtRatio(_DEFAULT_RATIO if at is None else at, fallback_tokens)
    if at is not None or fallback_tokens is not None:
        raise ConfigError("Choose a custom trigger or a ratio")
    return trigger


type _Trigger = Annotated[Callable[[ContextState], bool], AfterValidator(_sync_trigger)]
_DEFAULT_TRIGGER = _AtRatio(_DEFAULT_RATIO)


class CompactionConfig(CompactionSettings):
    trigger: _Trigger = _DEFAULT_TRIGGER


class Compaction:
    """Summarize older history and retain complete steps across turn boundaries.

    Trigger ratios include reserved output; target ratios measure input only.
    Targets are best effort. max_summary_tokens is a strict text ceiling.
    prompt replaces the complete system prompt; llm selects its language model.
    A custom summarizer owns both and cannot be combined with either option.
    """

    def __init__(
        self,
        *,
        trigger: Callable[[ContextState], bool] | None = None,
        at: float | None = None,
        fallback_tokens: int | None = None,
        llm: Model | str | None = None,
        prompt: CompactionPrompt | None = None,
        summarizer: Summarizer | None = None,
        keep_last_steps: int = 2,
        target_ratio: float | None = None,
        target_tokens: int | None = None,
        max_summary_tokens: int | None = None,
    ) -> None:
        if summarizer is not None:
            if not isinstance(summarizer, Summarizer):
                raise ConfigError("summarizer must implement Summarizer; select models with llm=")
            if llm is not None or prompt is not None:
                raise ConfigError("summarizer is mutually exclusive with llm and prompt")
        if llm is not None and not isinstance(llm, str | Model):
            raise ConfigError(
                "llm must be a Harnessed Model or name; wrap native models with PydanticAIModel"
            )
        if (
            prompt is not None
            and not callable(prompt)
            and (not isinstance(prompt, str) or not prompt.strip())
        ):
            raise ConfigError("Compaction prompt must be a non-empty string or factory")
        self.config = CompactionConfig(
            trigger=_select_trigger(trigger, at, fallback_tokens),
            keep_last_steps=keep_last_steps,
            target_ratio=target_ratio,
            target_tokens=target_tokens,
            max_summary_tokens=max_summary_tokens,
        )
        self.summarizer = summarizer
        self.llm = llm
        self.prompt = prompt

    async def install(
        self, scope: PluginScope, *, resolve_model: Callable[[Model | str | None], Model | None]
    ) -> None:
        backend = resolve_model(self.llm)
        if isinstance(backend, AbstractAsyncContextManager):
            await scope.enter(backend)
        scope.provide(
            CompactionPolicy,
            CompactionPolicy(
                self.config, self.config.trigger, self.summarizer, backend, self.prompt
            ),
        )


@dataclass(frozen=True, config=ConfigDict(strict=True, arbitrary_types_allowed=True))
class Context:
    """Bound model input and individual tool results. Summarize history when configured."""

    max_tokens: Annotated[int, Field(ge=1)] | None = None
    max_tool_output_chars: Annotated[int, Field(ge=256)] = 32_000
    compaction: Compaction | None = None
    reserve_tokens: Annotated[int, Field(ge=0)] = 0

    async def install(
        self, scope: PluginScope, *, resolve_model: Callable[[Model | str | None], Model | None]
    ) -> None:
        scope.provide(ContextBudget, ContextBudget(self.max_tokens, self.reserve_tokens))
        if self.compaction is not None:
            await self.compaction.install(scope, resolve_model=resolve_model)
