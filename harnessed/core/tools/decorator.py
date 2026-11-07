import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, overload

from pydantic import BaseModel

from harnessed.core.tools.enabled import ToolEnabled
from harnessed.core.tools.info import ToolChain, ToolPresentation
from harnessed.core.tools.policy import OutputLimit, OutputLimitSetting
from harnessed.core.tools.registry import ParameterFields, ToolSpec, ToolTimeout
from harnessed.core.tools.scheduling import ResourceSpec, resource_spec
from harnessed.kernel import PluginBase


@overload
def tool[F: Callable[..., Any]](fn: F, /) -> F: ...


@overload
def tool[F: Callable[..., Any]](
    *,
    name: str | None = None,
    description: str | None = None,
    presentation: ToolPresentation | None = None,
    enabled: ToolEnabled = True,
    timeout_seconds: ToolTimeout = None,
    parallel: bool = False,
    resources: ResourceSpec = (),
    readonly: bool = False,
    confirm: bool = False,
    output_limit: OutputLimitSetting = OutputLimit.INHERIT,
    override: bool = False,
    parameter_fields: ParameterFields | None = None,
    params: type[BaseModel] | None = None,
    metadata: tuple[object, ...] = (),
    chain: ToolChain = ToolChain.CONTINUE,
) -> Callable[[F], F]: ...


def tool(
    fn=None,
    /,
    *,
    name: str | None = None,
    description: str | None = None,
    presentation: ToolPresentation | None = None,
    enabled: ToolEnabled = True,
    timeout_seconds: ToolTimeout = None,
    parallel: bool = False,
    resources: ResourceSpec = (),
    readonly: bool = False,
    confirm: bool = False,
    output_limit: OutputLimitSetting = OutputLimit.INHERIT,
    override: bool = False,
    parameter_fields: ParameterFields | None = None,
    params: type[BaseModel] | None = None,
    metadata: tuple[object, ...] = (),
    chain: ToolChain = ToolChain.CONTINUE,
):
    """Declare a tool, optionally binding its arguments to a Pydantic params model."""

    resources = resource_spec(resources)

    def decorate(callback):
        spec = ToolSpec(
            name=name,
            description=description,
            presentation=presentation if presentation is not None else ToolPresentation(),
            enabled=enabled,
            timeout_seconds=timeout_seconds,
            parallel=parallel,
            resources=resources,
            readonly=readonly,
            confirm=confirm,
            override=override,
            output_limit=output_limit,
            parameter_fields=parameter_fields,
            params=params,
            metadata=metadata,
            chain=chain,
        )
        vars(callback)["_harnessed_tool"] = spec
        vars(callback)["_harnessed_registrations"] = [
            *getattr(callback, "_harnessed_registrations", ()),
            spec,
        ]
        return callback

    return decorate(fn) if fn is not None else decorate


def _activation_tool(callback: Callable[..., Any]) -> Callable[..., Any]:
    parameters = tuple(inspect.signature(callback).parameters)

    def check_recipe(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        owner = args[0] if args else kwargs.get(parameters[0]) if parameters else None
        if isinstance(owner, PluginBase) and not owner.is_active:
            raise RuntimeError(
                "Tools can be called only on an active plugin instance, not its recipe "
                "or a disposed activation"
            )

    if inspect.iscoroutinefunction(callback):

        @wraps(callback)
        async def asynchronous(*args: Any, **kwargs: Any) -> Any:
            check_recipe(args, kwargs)
            return await callback(*args, **kwargs)

        return asynchronous

    @wraps(callback)
    def synchronous(*args: Any, **kwargs: Any) -> Any:
        check_recipe(args, kwargs)
        return callback(*args, **kwargs)

    return synchronous
