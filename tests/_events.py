import re
from dataclasses import fields
from typing import get_args, get_origin

from harnessed.events import LogEvent, StreamEvent


def builtin_event_types() -> tuple[type, ...]:
    kinds = {
        get_origin(kind) or kind
        for union in (LogEvent, StreamEvent)
        for kind in get_args(get_args(union.__value__)[0])
    }
    return tuple(sorted(kinds, key=lambda kind: kind.__name__))


def default_tag(event_type: type) -> str:
    return str(next(item.default for item in fields(event_type) if item.name == "type"))


def tag_from_class_name(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", ".", name.removesuffix("Event")).lower()
