from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from typing import cast

from harnessed.core.tools import ToolRegistry
from harnessed.kernel import PluginBase, PluginLike, Scope


@asynccontextmanager
async def mount_plugin[T: PluginBase](
    recipe: T, *, services: Iterable[PluginLike] = ()
) -> AsyncGenerator[T]:
    """Activate a recipe with explicit fake services and guarantee owned cleanup.

    No agent, model, prompt rendering or event codec is required. The yielded
    instance belongs to this mount and becomes invalid on context-manager exit.
    """
    async with Scope() as scope:
        scope.provide(ToolRegistry, ToolRegistry(scope))
        await scope.install(services, strict=True)
        (handle,) = await scope.install((recipe,), strict=True, settle=True)
        instance = handle.instance
        if not isinstance(instance, type(recipe)):
            raise TypeError("The recipe did not activate a matching plugin instance")
        yield cast(T, instance)
