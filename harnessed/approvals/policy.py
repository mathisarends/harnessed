from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Annotated, Self

from pydantic import ConfigDict, Field, TypeAdapter

from harnessed.events import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalRevokedEvent,
    ApprovalStatus,
    DurableEvent,
)
from harnessed.kernel import PluginScope
from harnessed.models import Decision, ToolCall
from harnessed.runtime.plugin import PluginConfig
from harnessed.tools import PreparedToolCall, ToolRegistry
from harnessed.tools.references import ToolReference, ToolSelector, tool_selector

from .responders import ApprovalCallback, Approver, CallbackApprover

_APPROVAL_TIMEOUT = TypeAdapter(
    Annotated[float, Field(gt=0)],
    config=ConfigDict(
        title="approval_timeout", strict=True, allow_inf_nan=False, hide_input_in_errors=True
    ),
)


def validate_approval_timeout(value: float) -> None:
    _APPROVAL_TIMEOUT.validate_python(value)


@dataclass(frozen=True)
class Approval:
    request: ApprovalRequestedEvent
    resolution: ApprovalResolvedEvent | None = None
    revoked: bool = False

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def pending(self) -> bool:
        return self.resolution is None

    @property
    def decision(self) -> Decision | None:
        return self.resolution.decision if self.resolution is not None else None

    @property
    def is_grant(self) -> bool:
        return (
            not self.revoked
            and self.resolution is not None
            and self.resolution.remember
            and self.request.grant_id is None
        )


@dataclass(frozen=True, init=False)
class ApprovalState:
    records: tuple[Approval, ...]

    @classmethod
    def from_records(cls, records: Iterable[Approval]) -> Self:
        snapshot = object.__new__(cls)
        object.__setattr__(snapshot, "records", tuple(records))
        return snapshot

    def __init__(self, events: Iterable[DurableEvent]) -> None:
        records: dict[str, Approval] = {}
        for event in events:
            record = approval_transition(records, event)
            if record is not None:
                records[record.request_id] = record
        object.__setattr__(self, "records", tuple(records.values()))

    @property
    def pending(self) -> tuple[Approval, ...]:
        return tuple(record for record in self.records if record.pending)

    @property
    def grants(self) -> tuple[Approval, ...]:
        return tuple(record for record in self.records if record.is_grant)

    def grant_for(self, tool: str, *, scope: str) -> Approval | None:
        return next(
            (
                record
                for record in reversed(self.grants)
                if record.request.call.name == tool and record.request.scope == scope
            ),
            None,
        )


def approval_transition(records, event: DurableEvent) -> Approval | None:
    match event:
        case ApprovalRequestedEvent():
            if event.request_id in records:
                raise ValueError("Duplicate approval request_id")
            if event.grant_id is not None:
                grant = records.get(event.grant_id)
                if (
                    grant is None
                    or not grant.is_grant
                    or grant.request.scope != event.scope
                    or grant.request.identity != event.identity
                    or (event.identity is None and grant.request.call.name != event.call.name)
                ):
                    raise ValueError(
                        "Cached approval must reference an active grant for its tool and scope"
                    )
            return Approval(event)
        case ApprovalResolvedEvent():
            record = records.get(event.request_id)
            if record is None or not record.pending:
                raise ValueError("Approval resolution requires a pending request")
            if record.request.grant_id is not None and event.status == ApprovalStatus.ANSWERED:
                grant = records[record.request.grant_id]
                if not grant.is_grant or not event.remember:
                    raise ValueError("Cached approval grant is no longer active")
            return replace(record, resolution=event)
        case ApprovalRevokedEvent():
            record = records.get(event.request_id)
            if record is None or not record.is_grant:
                raise ValueError("Approval revocation requires an active remembered grant")
            return replace(record, revoked=True)
    return None


class ApprovalsConfig(PluginConfig):
    timeout: Annotated[float, Field(gt=0)] = 300.0


DEFAULT_APPROVALS = ApprovalsConfig()


class Approvals:
    """Authorize prepared actions. Explicit defaults apply to every unmatched tool."""

    def __init__(
        self,
        *,
        default: Decision | None = None,
        allow: Iterable[ToolSelector] | None = None,
        ask: Iterable[ToolSelector] = (),
        deny: Iterable[ToolSelector] = (),
        approver: Approver | ApprovalCallback | None = None,
        policy: Callable[[ToolCall], Decision | None] | None = None,
        timeout: float = DEFAULT_APPROVALS.timeout,
    ) -> None:
        if default is not None and not isinstance(default, Decision):
            raise TypeError("default must be a Decision")
        if default is None:
            default = Decision.ALLOW if allow is None else Decision.DENY
        self.default = default
        self.config = ApprovalsConfig(timeout=timeout)
        self.approver = (
            approver
            if approver is None or isinstance(approver, Approver)
            else CallbackApprover(approver)
        )
        self.policy = policy
        self.rules: list[tuple[StrEnum | ToolReference, Decision]] = []
        for items, decision in (
            (() if allow is None else allow, Decision.ALLOW),
            (ask, Decision.ASK),
            (deny, Decision.DENY),
        ):
            if isinstance(items, str):
                raise TypeError(
                    "Approval rules require a collection of action enum members or decorated tools"
                )
            for item in items:
                self._add(tool_selector(item), decision)

    def _add(self, selector: StrEnum | ToolReference, decision: Decision) -> None:
        for existing, configured in self.rules:
            if type(existing) is type(selector) and existing == selector:
                if configured is not decision:
                    raise ValueError("Conflicting approval rules for the same action")
                return
        self.rules.append((selector, decision))

    def install(self, scope: PluginScope) -> None:
        scope.provide(ApprovalsConfig, self.config)
        scope.provide(Approvals, self)
        if self.approver is not None:
            scope.provide(Approver, self.approver)
        for selector, _ in self.rules:
            if isinstance(selector, ToolReference):
                scope[ToolRegistry].reference(selector, owner=scope)

    def decide(self, plan: PreparedToolCall) -> Decision:
        decisions = {
            decision
            for selector, decision in self.rules
            if plan.tool is not None and plan.tool.matches(selector)
        }
        if len(decisions) > 1:
            raise ValueError("Conflicting approval rules match this registration")
        if decisions:
            return decisions.pop()
        if self.policy is not None:
            decision = self.policy(plan.call)
            if decision is not None:
                if not isinstance(decision, Decision):
                    raise TypeError("Approval policies must return Decision or None")
                return decision
        return self.default
