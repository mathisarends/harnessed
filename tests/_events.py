import re
from dataclasses import fields
from typing import TypeAliasType, get_args, get_origin

from harnessed.events import LogEvent
from harnessed.events.stream import StreamEvent


def builtin_event_types() -> tuple[type, ...]:
    kinds = set(_event_types(get_args(LogEvent.__value__)[0]))
    kinds.update(_event_types(get_args(StreamEvent.__value__)[0]))
    return tuple(sorted(kinds, key=lambda kind: kind.__name__))


def default_tag(event_type: type) -> str:
    return str(next(item.default for item in fields(event_type) if item.name == "type"))


def tag_from_class_name(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", ".", name.removesuffix("Event")).lower()


def _event_types(union: object) -> list[type]:
    kinds = []
    for member in get_args(union):
        kind = get_origin(member) or member
        if isinstance(kind, TypeAliasType):
            kinds.extend(_event_types(kind.__value__))
        else:
            kinds.append(kind)
    return kinds
