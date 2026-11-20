from collections.abc import Iterable
from functools import wraps
from typing import Any

from harnessed.kernel import PluginSpec, ServiceKey


def kernel_recipe(
    fn=None,
    /,
    *,
    needs: Iterable[ServiceKey[Any]] = (),
    name: str | None = None,
    slot: str | None = None,
):
    """Build a raw mount specification for exercising the kernel state machine."""

    def decorate(factory):
        @wraps(factory)
        def configure(*args, **kwargs):
            dependencies = tuple(dict.fromkeys(needs))
            return PluginSpec(name or factory.__name__, factory, dependencies, args, kwargs, slot)

        return configure

    return decorate(fn) if fn is not None else decorate
