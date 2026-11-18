from dataclasses import replace

from harnessed.models import ToolArgumentsMode

from .messages import ReasoningDeltaEvent, TextDeltaEvent
from .tools import ToolArgumentsDeltaEvent


def coalesce_deltas[E](previous: E, event: E) -> E | None:
    """Fold adjacent deltas of the same message into one, or return None.

    Text and reasoning fragments concatenate. Argument APPEND fragments of the
    same call concatenate and keep the newer name and call ID; a REPLACE snapshot
    supersedes any earlier fragment of its call. Everything else stays separate.
    """
    if (isinstance(previous, TextDeltaEvent) and isinstance(event, TextDeltaEvent)) or (
        isinstance(previous, ReasoningDeltaEvent) and isinstance(event, ReasoningDeltaEvent)
    ):
        if previous.message_id == event.message_id:
            return replace(event, text=previous.text + event.text)
    elif isinstance(previous, ToolArgumentsDeltaEvent) and isinstance(
        event, ToolArgumentsDeltaEvent
    ):
        before, after = previous.delta, event.delta
        if previous.message_id != event.message_id or before.index != after.index:
            return None
        if after.mode is ToolArgumentsMode.REPLACE:
            return event
        if before.mode is ToolArgumentsMode.APPEND:
            merged = replace(after, arguments=before.arguments + after.arguments)
            return replace(event, delta=merged)
    return None
