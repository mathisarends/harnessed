import inspect
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated

from pydantic import AfterValidator, ConfigDict, Field
from pydantic.dataclasses import dataclass

from harnessed.core.compaction import (
    CompactionPolicy,
    CompactionPrompt,
    CompactionSettings,
    ContextState,
    Summarizer,
)
from harnessed.core.errors import ConfigError
from harnessed.core.model import Model
from harnessed.core.plugin import Plugin
from harnessed.core.tokens import TokenCounter
from harnessed.kernel import PluginScope
from harnessed.models import PydanticAIModel

_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


@dataclass(frozen=True, config=_CONFIG)
class _AtRatio:
    at: Annotated[float, Field(gt=0, le=1)]

    def __call__(self, state: ContextState) -> bool:
        if state.ratio is None:
            raise ConfigError("Ratio compaction requires a known or configured context window")
        return state.ratio >= self.at


@dataclass(frozen=True, config=_CONFIG)
class _EveryTurns:
    every: Annotated[int, Field(ge=1)]

    def __call__(self, state: ContextState) -> bool:
        return state.turns_since_compaction >= self.every


def at_ratio(at: float = 0.8) -> Callable[[ContextState], bool]:
    """Compact when input plus reserved output reaches this window ratio."""
    return _AtRatio(at)


def every_turns(every: int) -> Callable[[ContextState], bool]:
    """Compact after this many newly completed turns."""
    return _EveryTurns(every)


def _sync_trigger(value: Callable[[ContextState], bool]) -> Callable[[ContextState], bool]:
    if inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(type(value).__call__):
        raise ValueError("Compaction triggers must be synchronous")
    return value


type _Trigger = Annotated[Callable[[ContextState], bool], AfterValidator(_sync_trigger)]
_DEFAULT_TRIGGER = _AtRatio(0.8)


class CompactionConfig(CompactionSettings):
    trigger: _Trigger = _DEFAULT_TRIGGER


class Compaction(Plugin[CompactionConfig], slot="compaction"):
    """Summarize older history and retain complete steps across turn boundaries.

    Trigger ratios include reserved output; target ratios measure input only.
    Targets are best effort. max_summary_tokens is a strict text ceiling.
    prompt replaces the complete system prompt; llm selects its language model.
    A custom summarizer owns both and cannot be combined with either option.
    """

    def __init__(
        self,
        *,
        trigger: Callable[[ContextState], bool] = _DEFAULT_TRIGGER,
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
        super().__init__(
            needs=[Model, TokenCounter],
            config=CompactionConfig(
                trigger=trigger,
                keep_last_steps=keep_last_steps,
                target_ratio=target_ratio,
                target_tokens=target_tokens,
                max_summary_tokens=max_summary_tokens,
            ),
        )
        self.summarizer = summarizer
        self.llm = llm
        self.prompt = prompt

    async def setup(self, scope: PluginScope) -> None:
        backend = PydanticAIModel(self.llm) if isinstance(self.llm, str) else self.llm
        if isinstance(backend, AbstractAsyncContextManager):
            await scope.enter(backend)
        scope.provide(
            CompactionPolicy,
            CompactionPolicy(
                self.config, self.config.trigger, self.summarizer, backend, self.prompt
            ),
        )
