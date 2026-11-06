from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import wraps
from typing import Any
from uuid import uuid4

from harnessed.core.errors import ConfigError
from harnessed.kernel import PluginLike, PluginSpec, Scope, as_plugin_spec

from .context import DynamicContext
from .values import (
    PluginOrder,
    PromptComposition,
    PromptContribution,
    SectionDelivery,
    SystemPrompt,
)


def runtime_sections(
    plugin: PluginLike, contribution: PromptContribution, delivery: SectionDelivery
) -> PluginSpec:
    """Attach section delivery to the capability's own activation and rollback."""
    spec = as_plugin_spec(plugin)
    if contribution.instructions is None or delivery is SectionDelivery.IGNORE:
        return spec
    if delivery is SectionDelivery.ERROR:
        raise ConfigError(
            f"Cannot mount {contribution.name} at runtime: it declares system sections. "
            "Select section_delivery=SectionDelivery.CONTEXT or SectionDelivery.IGNORE, "
            "or compose the plugin when constructing Agent."
        )
    key = f"runtime.sections.{spec.name}.{uuid4().hex}"

    @wraps(spec.factory)
    async def mount(scope: Scope, *args: Any, **kwargs: Any) -> AsyncGenerator[object, None]:
        async with asynccontextmanager(spec.factory)(scope, *args, **kwargs) as instance:
            composition = PromptComposition(
                SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
                contributions=(contribution,),
            )
            text = await composition.render(scope)
            if text:
                scope[DynamicContext].register(key, text, owner=scope)
            yield instance

    return replace(
        spec,
        factory=mount,
        needs=tuple(dict.fromkeys((*spec.needs, DynamicContext))),
    )
