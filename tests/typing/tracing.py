from typing import assert_type

from harnessed.kernel import PluginScope, Scope
from harnessed.tracing import Span, Tracer, current, span


def instrument(scope: PluginScope, owner: Scope) -> None:
    assert_type(current(), Tracer)
    with span("library.work", scope=scope, operation="read") as active:
        assert_type(active, Span)
        active.set_attribute("library.ready", True)
        with span("library.nested", scope=owner):
            pass
    with span("library.ambient"):
        pass
