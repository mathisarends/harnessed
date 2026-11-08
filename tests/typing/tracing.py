from typing import assert_type

from harnessed.kernel import PluginScope, Scope
from harnessed.tracing import Span, Tracer, current, record, span, traced


def instrument(scope: PluginScope, owner: Scope) -> None:
    assert_type(current(), Tracer)
    with span("library.work", scope=scope, operation="read") as active:
        assert_type(active, Span)
        active.set_attribute("library.ready", True)
        with span("library.nested", scope=owner):
            pass
    with span("library.ambient"):
        pass


@traced
def echo[T](value: T, *, label: str = "") -> T:
    record(label=label)
    return value


@traced
async def fetch(value: int, *, suffix: str = "") -> str:
    record(value=value)
    return str(value) + suffix


async def check_types(value: int, text: str) -> None:
    assert_type(echo(value, label="number"), int)
    assert_type(echo(text), str)
    assert_type(await fetch(2, suffix="!"), str)
    assert_type(record(ready=True), None)
