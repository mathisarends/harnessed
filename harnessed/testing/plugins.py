from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from typing import cast

from harnessed.kernel import Scope
from harnessed.runtime.plugin import Plugin
from harnessed.tools import ToolRegistry


@asynccontextmanager
async def mount_plugin[T: Plugin](
    recipe: T, *, services: Iterable[Plugin] = ()
) -> AsyncGenerator[T]:
    """Activate a recipe with explicit fake services and guarantee owned cleanup.

    No agent, model, prompt rendering or event codec is required. The yielded
    instance belongs to this mount and becomes invalid on context-manager exit.
    """
    services = tuple(services)
    if not isinstance(recipe, Plugin) or any(not isinstance(value, Plugin) for value in services):
        raise TypeError("Expected a Plugin instance")
    async with Scope() as scope:
        scope.provide(ToolRegistry, ToolRegistry(scope))
        await scope.install(services, strict=True)
        (handle,) = await scope.install((recipe,), strict=True, settle=True)
        instance = handle.instance
        if not isinstance(instance, type(recipe)):
            raise TypeError("The recipe did not activate a matching plugin instance")
        yield cast(T, instance)
