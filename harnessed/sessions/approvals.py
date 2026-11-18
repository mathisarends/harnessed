from typing import NamedTuple

from harnessed.events import DurableEvent
from harnessed.runtime.approval_policy import Approval, ApprovalState, approval_transition


class ApprovalProjection:
    """Index approval records by request, tool call and persistent grant.

    Grants and unresolved requests survive a turn boundary; resolved one-off
    decisions and the per-call index are released with the turn.
    """

    def __init__(self) -> None:
        self._records: dict[str, Approval] = {}
        self._by_call: dict[str, dict[str, Approval]] = {}
        self._grants: dict[_GrantKey, dict[str, Approval]] = {}
        self._state: ApprovalState | None = ApprovalState(())

    @property
    def state(self) -> ApprovalState:
        if self._state is None:
            self._state = ApprovalState.from_records(self._records.values())
        return self._state

    def record(self, request_id: str) -> Approval | None:
        return self._records.get(request_id)

    def grant(self, tool: str, *, scope: str) -> Approval | None:
        grants = self._grants.get(_GrantKey(scope, tool), {})
        return next(reversed(grants.values()), None)

    def latest(self, call_id: str) -> Approval | None:
        return next(reversed(self._by_call.get(call_id, {}).values()), None)

    def transition(self, event: DurableEvent) -> Approval | None:
        """Validate the approval change this event makes without recording it."""
        return approval_transition(self._records, event)

    def apply(self, approval: Approval) -> None:
        self._records[approval.request_id] = approval
        self._index_grant(approval)
        self._by_call.setdefault(approval.request.call.id, {})[approval.request_id] = approval
        self._state = None

    def release_turn(self) -> None:
        grants = {key for values in self._grants.values() for key in values}
        self._records = {
            key: record
            for key, record in self._records.items()
            if key in grants or record.resolution is None
        }
        self._by_call.clear()
        self._state = None

    def _index_grant(self, record: Approval) -> None:
        key = _GrantKey(record.request.scope, record.request.call.name)
        grants = self._grants.setdefault(key, {})
        if record.is_grant:
            grants.clear()
            grants[record.request_id] = record
        else:
            grants.pop(record.request_id, None)


class _GrantKey(NamedTuple):
    scope: str
    tool: str
