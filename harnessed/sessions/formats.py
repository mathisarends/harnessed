from .upcasting import Document, DocumentFormat


def _checkpoint_v5(document: Document) -> Document:
    """Split a released full checkpoint at its original journal offset."""
    snapshot = document.copy()
    del snapshot["version"]
    offset = snapshot.pop("offset")
    events = snapshot.pop("events", [])
    if type(offset) is not int or not isinstance(events, list):
        raise ValueError("Invalid checkpoint offset or events")
    if "sequence" in snapshot or not all(isinstance(event, dict) for event in events):
        raise ValueError("Invalid checkpoint fields")
    if events and events[0].get("type") != "turn.started":
        raise ValueError("A checkpoint suffix must begin with its open turn")
    snapshot["sequence"] = offset
    snapshot["version"] = 1
    records = [_checkpoint_record(event) for event in events]
    _restore_final_control(records, snapshot)
    entries = [
        {"sequence": sequence, "record": record, "version": 1}
        for sequence, record in enumerate(records, start=offset)
    ]
    return {"snapshot": snapshot, "journal": entries}


def _restore_final_control(records: list[Document], snapshot: Document) -> None:
    # A v5 reader applied control *after* replay. In v6 the snapshot precedes
    # replay. Preserve the authoritative final state even when it was supplied
    # separately from the last recorded transition.
    final = snapshot["control"]
    if not isinstance(final, dict):
        raise ValueError("Expected a checkpoint control event")
    for record in reversed(records):
        if record.get("type") == "session.control.changed":
            control = {"type": "session.control.changed", "keep_inbox": False, **final}
            if {"keep_inbox": False, **record} != control:
                records.append(control)
            break


def _checkpoint_record(event: Document) -> Document:
    match event.get("type"):
        case "checkpoint.extension":
            return {**event, "type": "journal.extension"}
        case "journal.extension" | "journal.request":
            raise ValueError("Unexpected journal record in a version 5 checkpoint")
        case "model.requested":
            request = event["request"]
            if not isinstance(request, dict):
                raise ValueError("Expected a recorded model request")
            # Full checkpoints carried the original messages inline. Keep them
            # inline in the new slots; this also preserves provider replay data.
            messages = request["messages"]
            if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
                raise ValueError("Expected inline checkpoint messages")
            header = {**event, "request": {**request, "messages": []}}
            return {"type": "journal.request", "event": header, "messages": messages}
        case _:
            return event


def _snapshot_children(document: Document) -> None:
    document["state"] = STATE.upgrade(document["state"])


def _recovery_children(document: Document) -> None:
    document["snapshot"] = SNAPSHOT.upgrade(document["snapshot"])
    entries = document.get("journal", [])
    if not isinstance(entries, list):
        raise ValueError("Expected a journal array")
    document["journal"] = [ENTRY.upgrade(entry) for entry in entries]


STATE = DocumentFormat("conversation state", 4)
ENTRY = DocumentFormat("journal entry", 1)
SNAPSHOT = DocumentFormat("session snapshot", 1, children=_snapshot_children)
RECOVERY = DocumentFormat("session recovery", 5, (_checkpoint_v5,), children=_recovery_children)
