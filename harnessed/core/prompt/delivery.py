from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import wraps
from typing import Any
from uuid import uuid4

from harnessed.core.errors import ConfigError
from harnessed.core.hooks import PrepareRequest
from harnessed.core.messages import UserMessage
from harnessed.core.model import ModelRequest
from harnessed.kernel import Next, PluginLike, PluginSpec, Scope, as_plugin_spec

from .values import (
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

    @wraps(spec.factory)
    async def mount(scope: Scope, *args: Any, **kwargs: Any) -> AsyncGenerator[object, None]:
        async with asynccontextmanager(spec.factory)(scope, *args, **kwargs) as instance:
            composition = PromptComposition(
                SystemPrompt.from_plugins(),
                contributions=(contribution,),
            )
            text = await composition.render(scope)
            if text:
                message = UserMessage(text, id=f"mount-context:{uuid4().hex}")

                async def enrich(event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
                    request = await next_()
                    return request.append_message(message)

                scope.on(PrepareRequest, enrich)
            yield instance

    return replace(
        spec,
        factory=mount,
    )
