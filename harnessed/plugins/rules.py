from __future__ import annotations

import inspect
import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass as event_dataclass
from dataclasses import field, replace
from enum import StrEnum
from typing import Annotated, Any, Literal, Self, override

from pydantic import AfterValidator, ConfigDict, Field, StringConstraints, TypeAdapter
from pydantic.dataclasses import dataclass

from harnessed.events import DurableEvent
from harnessed.events.base import Identifier
from harnessed.kernel import Next, PluginScope, PluginState, hook, needs
from harnessed.models import ModelRequest, ToolCall, ToolResult, UserMessage, user
from harnessed.models.messages import to_dict
from harnessed.prompts.plugin_instructions import PluginInstructions
from harnessed.runtime.hooks import PrepareRequest, ReviewToolResult
from harnessed.runtime.inbox import SessionInbox
from harnessed.runtime.plugin import Plugin
from harnessed.runtime.tokens import TokenCounter, count_content
from harnessed.sessions.log import SessionLog
from harnessed.tools import ToolInfo, ToolReference, ToolSelector
from harnessed.tools.references import tool_selector


class RuleEventKind(StrEnum):
    TRIGGERED = "rule.triggered"


class RuleAction(StrEnum):
    NOTE = "note"
    CONTEXT = "context"
    STEER = "steer"


@event_dataclass(frozen=True)
class RuleTriggeredEvent(DurableEvent):
    __hash__ = None

    type: Literal[RuleEventKind.TRIGGERED] = field(default=RuleEventKind.TRIGGERED, kw_only=True)
    rule: str
    call_id: Identifier
    occurrence: int
    actions: tuple[RuleAction, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", tuple(self.actions))


def _synchronous(callback):
    if inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(
        type(callback).__call__
    ):
        raise ValueError("Rule conditions must be synchronous")
    return callback


type RuleText = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
# Selectors hold arbitrary callables; normalization rejects plain tool names.
type RuleTool = Annotated[Any, AfterValidator(tool_selector)]
type ExitCondition = Annotated[Callable[[int], bool], AfterValidator(_synchronous)]
type ResultCondition = Annotated[
    Callable[[ToolCall, ToolResult], bool], AfterValidator(_synchronous)
]
_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
_BOOL = TypeAdapter(bool, config=ConfigDict(strict=True, hide_input_in_errors=True))


def _condition(callback, *args) -> bool:
    result = callback(*args)
    if inspect.iscoroutine(result):
        result.close()
    return _BOOL.validate_python(result)


@dataclass(frozen=True, config=_CONFIG)
class Rule:
    __hash__ = None

    tools: tuple[RuleTool, ...] = ()

    command_contains: str | None = None

    exit_code: int | ExitCondition | None = None

    repeated: Annotated[int, Field(ge=1)] = 1

    result_tokens_over: Annotated[int, Field(ge=0)] | None = None

    predicate: ResultCondition | None = None

    actions: tuple[tuple[RuleAction, RuleText], ...] = ()

    def note(self, text: str) -> Rule:
        return self._action(RuleAction.NOTE, text)

    def context(self, text: str) -> Rule:
        """Keep a named context fact while the rule condition remains active."""
        return self._action(RuleAction.CONTEXT, text)

    def steer(self, text: str) -> Rule:
        return self._action(RuleAction.STEER, text)

    def matches(self, call: ToolCall, result: ToolResult, counter: TokenCounter) -> bool:
        if self.command_contains is not None and self.command_contains not in call.command:
            return False
        if self.exit_code is not None:
            if result.exit_code is None:
                return False
            if callable(self.exit_code):
                if not _condition(self.exit_code, result.exit_code):
                    return False
            elif result.exit_code != self.exit_code:
                return False
        if (
            self.result_tokens_over is not None
            and count_content(counter, result.content) <= self.result_tokens_over
        ):
            return False
        return self.predicate is None or _condition(self.predicate, call, result)

    def selects(self, tool: ToolInfo | None) -> bool:
        """Whether the rule applies to a call of this registration; no tools selects all."""
        if not self.tools:
            return True
        return tool is not None and any(tool.matches(selector) for selector in self.tools)

    def _action(self, kind: RuleAction, text: str) -> Rule:
        return replace(self, actions=(*self.actions, (kind, text)))


def when(
    *,
    tool: ToolSelector | Iterable[ToolSelector] | None = None,
    command_contains: str | None = None,
    exit_code: int | Callable[[int], bool] | None = None,
    repeated: int = 1,
    result_tokens_over: int | None = None,
    predicate: Callable[[ToolCall, ToolResult], bool] | None = None,
) -> Rule:
    """Start a rule; `tool` takes action enum members or decorated `@tool` methods."""
    if tool is None:
        tools = ()
    elif isinstance(tool, str | ToolReference) or callable(tool):
        tools = (tool,)
    else:
        tools = tuple(tool)
    return Rule(tools, command_contains, exit_code, repeated, result_tokens_over, predicate)


class Rules(Plugin):
    session = needs(SessionLog)
    counter = needs(TokenCounter)
    inbox = needs(SessionInbox)
    context = needs(PluginInstructions)

    events = (RuleTriggeredEvent,)
    _counts = PluginState[Counter[tuple[int, str]]](Counter)
    _last_counts = PluginState[dict[int, int]](dict)

    def __init__(self, rules: Iterable[Rule], *, name: str = "rules") -> None:
        super().__init__(name=name)
        self.rules = tuple(rules)
        if any(not isinstance(rule, Rule) or not rule.actions for rule in self.rules):
            raise ValueError("Each rule needs at least one note, context or steer action")

    def create_instance(self) -> Self:
        """Retain the validated immutable rule tuple, including one-shot source iterables."""
        instance = super().create_instance()
        instance.rules = self.rules
        return instance

    def _count(
        self,
        index: int,
        rule: Rule,
        call: ToolCall,
        *,
        tool: ToolInfo | None,
        result: ToolResult,
        matched: bool | None = None,
    ) -> tuple[tuple[int, str] | None, int]:
        if not rule.selects(tool):
            return None, 0
        arguments = call.command or json.dumps(to_dict(call.arguments), sort_keys=True)
        key = (index, json.dumps([call.name, arguments], ensure_ascii=False))
        if matched is None:
            matched = rule.matches(call, result, self.counter)
        self._counts[key] = self._counts[key] + 1 if matched else 0
        self._last_counts[index] = self._counts[key]
        return key, self._counts[key]

    @hook(ReviewToolResult)
    async def review(
        self,
        event: ReviewToolResult,
        next_: Next[ToolResult],
    ) -> ToolResult:
        result = await next_()
        matches = list(result.rule_matches)
        for index, rule in enumerate(self.rules):
            key, count = self._count(index, rule, event.call, tool=event.tool, result=event.result)
            if key is not None:
                rule_id = f"{self.name}:{index}"
                if any(name == rule_id for name, _ in matches):
                    raise ValueError(f"Duplicate rules plugin name: {self.name!r}")
                matches.append((rule_id, count > 0))
            if count == 0 or (rule.repeated > 1 and count != rule.repeated):
                if (
                    key is not None
                    and count == 0
                    and any(kind is RuleAction.CONTEXT for kind, _ in rule.actions)
                ):
                    await self.context.remove(f"rule:{self.name}:{index}")
                continue
            await self.session.append(
                RuleTriggeredEvent(
                    f"{self.name}:{index}",
                    event.call.id,
                    count,
                    tuple(kind for kind, _ in rule.actions),
                )
            )
            for kind, text in rule.actions:
                if kind is RuleAction.NOTE:
                    result = result.with_note(text)
                elif kind is RuleAction.CONTEXT:
                    await self.context.set(f"rule:{self.name}:{index}", text)
                else:
                    await self.inbox.steer(user(text))
        return replace(result, rule_matches=tuple(matches))

    @override
    async def teardown(self, scope: PluginScope) -> None:
        for index, rule in enumerate(self.rules):
            if any(kind is RuleAction.CONTEXT for kind, _ in rule.actions):
                await self.context.remove(f"rule:{self.name}:{index}")

    @hook(PrepareRequest)
    async def enrich(
        self,
        _event: PrepareRequest,
        next_: Next[ModelRequest],
    ) -> ModelRequest:
        request = await next_()
        for index, rule in enumerate(self.rules):
            name = f"rule:{self.name}:{index}"
            if any(kind is RuleAction.CONTEXT for kind, _ in rule.actions):
                text = self.context.values.get(name)
                if text is not None:
                    request = request.append_message(UserMessage(text, id=name))
        return request
