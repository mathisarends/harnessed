import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from types import MethodType
from typing import Annotated, Any, NamedTuple, overload

from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

from harnessed.core.errors import ApprovalUnavailable, ToolNameConflict
from harnessed.core.messages import ToolCall, ToolResult, ToolStatus
from harnessed.core.model import ToolDefinition
from harnessed.core.tools.arguments import ToolArguments, tool_result_content
from harnessed.core.tools.availability import ToolAvailability, ToolExplanation
from harnessed.core.tools.context import ToolContext, ToolContexts
from harnessed.core.tools.enabled import (
    BoundToolEnabled,
    ToolEnabled,
    bind_enabled,
    validate_enabled,
)
from harnessed.core.tools.envelope import (
    EnvelopeLayout,
    EnvelopePart,
    EnvelopePosition,
    EnvelopeSelector,
    InvalidEnvelope,
    envelope_models,
    envelope_selector,
)
from harnessed.core.tools.execution import ToolExecutionBudget
from harnessed.core.tools.info import (
    ToolChain,
    ToolInfo,
    ToolMetadata,
    ToolPresentation,
)
from harnessed.core.tools.parameters import DisabledToolParameterError
from harnessed.core.tools.policy import OutputLimit, OutputLimitSetting, validate_output_limit
from harnessed.core.tools.references import ToolReference, ToolSelector, tool_selector
from harnessed.core.tools.scheduling import (
    PreparedToolCall,
    ResourceSpec,
    ToolScheduling,
    resource_spec,
)
from harnessed.kernel import (
    BindingPlan,
    Disposer,
    PluginBase,
    PluginScope,
    RegistrationState,
    Scope,
    ServiceKey,
    binding_plan,
    run_sync,
)

logger = logging.getLogger(__name__)

_UNSPECIFIED = object()
_TIMEOUT = TypeAdapter(
    Annotated[float, Field(gt=0)] | None,
    config=ConfigDict(
        title="tool timeout", strict=True, allow_inf_nan=False, hide_input_in_errors=True
    ),
)


class ToolError(Exception):
    """An intentional, safe error message that may be shown to the model."""


type ParameterFields = Callable[[Any], Mapping[str, FieldInfo]]
type ToolTimeout = float | Callable[[Any], float | None] | None


@dataclass(frozen=True)
class ToolSpec:
    """A tool method declaration installed for one plugin lifetime."""

    name: str | None = None
    description: str | None = None
    enabled: ToolEnabled = True
    timeout_seconds: ToolTimeout = None
    parallel: bool = False
    resources: ResourceSpec = ()
    readonly: bool = False
    confirm: bool = False
    override: bool = False
    output_limit: OutputLimitSetting = OutputLimit.INHERIT
    parameter_fields: ParameterFields | None = None
    params: type[BaseModel] | None = None
    metadata: tuple[object, ...] = ()
    chain: ToolChain = ToolChain.CONTINUE
    presentation: ToolPresentation = ToolPresentation()
    envelope: tuple[type[BaseModel], ...] = ()

    def __post_init__(self) -> None:
        if type(self.confirm) is not bool:
            raise TypeError("confirm must be a bool")
        if self.readonly and self.confirm:
            raise TypeError("readonly=True cannot be combined with confirm=True")
        if not isinstance(self.presentation, ToolPresentation):
            raise TypeError("Tool presentation must be a ToolPresentation")
        object.__setattr__(self, "metadata", ToolMetadata(self.metadata).values)
        object.__setattr__(self, "envelope", envelope_models(self.envelope))
        object.__setattr__(self, "chain", ToolChain(self.chain))
        if self.parallel and self.chain is ToolChain.STOP_AFTER:
            raise ValueError("STOP_AFTER tools must run sequentially")
        if self.description is not None and not isinstance(self.description, str):
            raise TypeError("Tool description must be a string or None")
        if self.params is not None:
            if (
                not isinstance(self.params, type)
                or not issubclass(self.params, BaseModel)
                or self.params is BaseModel
                or issubclass(self.params, RootModel)
            ):
                raise TypeError("params must be a Pydantic BaseModel subclass, not RootModel")
            if self.parameter_fields is not None:
                raise ValueError(
                    "params cannot be combined with parameter_fields; use model fields"
                )
        validate_output_limit(self.output_limit)
        validate_enabled(self.enabled)

    @property
    def needs(self) -> tuple[type, ...]:
        return (ToolRegistry,)

    def mount(self, scope: Scope, callback: Callable) -> Disposer:
        fields = None
        if self.parameter_fields is not None:
            if not isinstance(callback, MethodType):
                raise TypeError("parameter_fields requires a bound @tool method")
            fields = self.parameter_fields(callback.__self__)
        return scope[ToolRegistry].register(
            callback, spec=self, owner=scope, parameter_fields=fields
        )


type ErrorFormatter = Callable[[ToolCall, Exception], str]


def format_tool_error(
    call: ToolCall, error: Exception, formatter: ErrorFormatter | None = None
) -> str:
    logger.error("Tool %s failed", call.name, exc_info=error)
    if formatter is not None:
        return formatter(call, error)
    if isinstance(error, (ToolError, DisabledToolParameterError)):
        return str(error)
    if isinstance(error, ValidationError):
        return f"ValidationError: Invalid arguments for {call.name}: " + str(error)
    return f"{call.name} failed: {type(error).__name__}"


@dataclass(eq=False)
class _RegistryEnvelope:
    model: type[BaseModel]
    select: EnvelopeSelector | None
    on_invalid: InvalidEnvelope
    position: EnvelopePosition
    registration: RegistrationState


class _CachedLayout(NamedTuple):
    envelopes: tuple[_RegistryEnvelope, ...]
    layout: EnvelopeLayout


@dataclass(eq=False)
class _RegisteredTool:
    """A registration; `definition` holds the tool's parameters before any envelope."""

    definition: ToolDefinition
    callback: Callable
    owner_name: str
    registration: RegistrationState
    spec: ToolSpec
    timeout_seconds: float | None
    enabled: BoundToolEnabled
    declared: EnvelopeLayout = field(kw_only=True)
    cached: _CachedLayout | None = field(default=None, kw_only=True)


@dataclass(eq=False)
class _PythonTool(_RegisteredTool):
    plan: BindingPlan
    arguments: ToolArguments


type ExternalToolHandler = Callable[[Mapping[str, object], ToolContext], Awaitable[ToolResult]]


@dataclass(eq=False)
class _ExternalTool(_RegisteredTool):
    handler: ExternalToolHandler


type _Tool = _PythonTool | _ExternalTool


@dataclass(frozen=True, eq=False)
class _Binding:
    """The registration and envelope layout one scheduled call was planned against."""

    tool: _Tool
    layout: EnvelopeLayout


@dataclass(eq=False)
class _Converter:
    callback: Callable[[Any], ToolResult]
    registration: RegistrationState


class ToolRegistry:
    def __init__(self, scope: Scope | PluginScope) -> None:
        self._ctx = scope
        self._tools: dict[str, list[_Tool]] = {}
        self._converters: dict[type, list[_Converter]] = {}
        self._references: list[ToolReference] = []
        self._envelopes: list[_RegistryEnvelope] = []

    def reference(
        self, selector: ToolSelector, *, owner: Scope | PluginScope
    ) -> str | ToolReference:
        selected = tool_selector(selector)
        if isinstance(selected, ToolReference):

            def install(_registration: RegistrationState):
                self._references.append(selected)
                return lambda: self._references.remove(selected)

            owner.register(install)
        return selected

    def validate_references(self) -> None:
        for reference in self._references:
            if not any(
                reference.matches(tool.callback)
                for entries in self._tools.values()
                if (tool := self._visible(entries)) is not None
            ):
                raise ValueError(f"Referenced tool {reference.name!r} is not registered")

    def matches(self, selector: str | ToolReference, name: str) -> bool:
        if isinstance(selector, str):
            return selector == name
        tool = self._visible(self._tools.get(name, []))
        return tool is not None and selector.matches(tool.callback)

    def result_converter[T](
        self,
        kind: type[T],
        callback: Callable[[T], ToolResult],
        *,
        owner: Scope | PluginScope | None = None,
    ) -> Disposer:
        """Own a converter for a feature's result value for the plugin lifetime."""
        owner = owner if owner is not None else Scope.current() or self._ctx

        def install(registration: RegistrationState):
            entries = self._converters.setdefault(kind, [])
            entry = _Converter(callback, registration)
            entries.append(entry)

            def remove() -> None:
                entries.remove(entry)
                if not entries:
                    self._converters.pop(kind, None)

            return remove

        return owner.register(install)

    def envelope(
        self,
        model: type[BaseModel],
        *,
        select: EnvelopeSelector | None = None,
        on_invalid: InvalidEnvelope = InvalidEnvelope.DROP,
        position: EnvelopePosition = EnvelopePosition.LEADING,
        owner: Scope | PluginScope | None = None,
    ) -> Disposer:
        """Add a call envelope to every selected tool for the owner's lifetime.

        Applies to tools registered before or after it, including external tools of
        other plugins. `select` receives each tool's declared `ToolInfo` and must be
        deterministic, since layouts are cached until the set of active envelopes
        changes. `position` places the fields before or after the tool's parameters.
        A conflict with a Python tool raises ValueError here or when that tool
        registers; a conflicting external tool is skipped with a warning.
        """
        (model,) = envelope_models((model,))
        select = envelope_selector(select)
        on_invalid = InvalidEnvelope(on_invalid)
        position = EnvelopePosition(position)
        owner = owner if owner is not None else Scope.current() or self._ctx

        def install(registration: RegistrationState) -> Callable[[], None]:
            entry = _RegistryEnvelope(model, select, on_invalid, position, registration)
            envelopes = (*self._active_envelopes(), entry)
            for entries in self._tools.values():
                for tool in entries:
                    if isinstance(tool, _PythonTool):
                        _ = _layout_for(
                            tool.definition, tool.spec, tool.declared, envelopes=envelopes
                        )
            self._envelopes.append(entry)
            return lambda: self._envelopes.remove(entry)

        return owner.register(install)

    def _active_envelopes(self) -> tuple[_RegistryEnvelope, ...]:
        return tuple(entry for entry in self._envelopes if entry.registration.active)

    def _layout(self, tool: _Tool) -> EnvelopeLayout:
        """Resolve the tool's envelopes against the registry-wide set active right now."""
        envelopes = self._active_envelopes()
        if tool.cached is not None and tool.cached.envelopes == envelopes:
            return tool.cached.layout
        layout = _layout_for(
            tool.definition, tool.spec, tool.declared, envelopes=envelopes, skip_conflicts=True
        )
        tool.cached = _CachedLayout(envelopes, layout)
        return layout

    def _result(self, value: Any) -> ToolResult:
        if isinstance(value, ToolResult):
            return value
        for kind in type(value).__mro__:
            for entry in reversed(self._converters.get(kind, ())):
                if entry.registration.active:
                    return entry.callback(value)
        return ToolResult(tool_result_content(value))

    @overload
    def tool[F: Callable[..., Any]](self, fn: F, /) -> F: ...

    @overload
    def tool[F: Callable[..., Any]](
        self,
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
        owner: Scope | PluginScope | None = None,
        params: type[BaseModel] | None = None,
        metadata: tuple[object, ...] = (),
        chain: ToolChain = ToolChain.CONTINUE,
        envelope: tuple[type[BaseModel], ...] = (),
    ) -> Callable[[F], F]: ...

    def tool(
        self,
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
        owner: Scope | PluginScope | None = None,
        params: type[BaseModel] | None = None,
        metadata: tuple[object, ...] = (),
        chain: ToolChain = ToolChain.CONTINUE,
        envelope: tuple[type[BaseModel], ...] = (),
    ):
        def register(callback):
            self.register(
                callback,
                name=name,
                description=description,
                presentation=presentation,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                parallel=parallel,
                resources=resources,
                readonly=readonly,
                confirm=confirm,
                output_limit=output_limit,
                override=override,
                owner=owner,
                params=params,
                metadata=metadata,
                chain=chain,
                envelope=envelope,
            )
            return callback

        return register(fn) if fn is not None else register

    def register(
        self,
        callback: Callable,
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
        owner: Scope | PluginScope | None = None,
        spec: ToolSpec | None = None,
        parameter_fields: Mapping[str, FieldInfo] | None = None,
        params: type[BaseModel] | None = None,
        metadata: tuple[object, ...] = (),
        chain: ToolChain = ToolChain.CONTINUE,
        envelope: tuple[type[BaseModel], ...] = (),
    ) -> Disposer:
        spec = spec or ToolSpec(
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
            params=params,
            metadata=metadata,
            chain=chain,
            envelope=envelope,
        )
        name, parallel = spec.name, spec.parallel
        resources, readonly, override = spec.resources, spec.readonly, spec.override
        owner = owner if owner is not None else Scope.current() or self._ctx
        ToolScheduling(parallel)
        resources = resource_spec(resources)
        timeout_seconds = _resolve_timeout_seconds(spec.timeout_seconds, callback)
        plan = binding_plan(callback)
        tool_name = name or getattr(callback, "__name__", type(callback).__name__)
        existing = self._tools.get(tool_name, ())
        if existing and not override:
            previous = existing[-1].owner_name
            raise ToolNameConflict(
                f"Tool {tool_name!r} is already registered by {previous}; "
                f"{owner.owner_name} must declare override=True"
            )
        arguments = ToolArguments.from_signature(
            tool_name,
            plan.signature,
            plan.hints,
            injected=plan.dependencies,
            parameter_fields=parameter_fields,
            params=spec.params,
        )

        def install(registration: RegistrationState):
            effective = arguments.activate(callback, registration, scope=owner)
            definition = ToolDefinition(
                tool_name,
                spec.description
                if spec.description is not None
                else inspect.getdoc(callback) or "",
                effective.schema,
            )
            declared = EnvelopeLayout.build(
                definition, spec.envelope, reserved_names=effective.disabled
            )
            # Python tool conflicts with registry-wide envelopes fail the mount.
            _ = _layout_for(definition, spec, declared, envelopes=self._active_envelopes())
            check_enabled = bind_enabled(spec.enabled, callback, registration)
            entry = _PythonTool(
                definition,
                callback,
                owner.owner_name,
                registration,
                spec,
                timeout_seconds,
                check_enabled,
                plan,
                effective,
                declared=declared,
            )
            entries = self._tools.setdefault(definition.name, [])
            entries.append(entry)
            logger.debug("Registered tool %s with owner %s", tool_name, owner.owner_name)

            def remove() -> None:
                entries.remove(entry)
                if not entries:
                    del self._tools[definition.name]

            return remove

        return owner.register(install)

    def register_external(
        self,
        definition: ToolDefinition,
        handler: ExternalToolHandler,
        *,
        owner: Scope | PluginScope,
        spec: ToolSpec | None = None,
    ) -> Disposer:
        """Own a schema-defined tool on the normal execution and policy pipeline.

        The handler validates the external schema before performing any action.
        It receives JSON arguments without Python coercion and the call context.
        Ordinary Python tools continue to use decorated Plugin methods.
        """
        spec = spec or ToolSpec()
        if spec.name is not None and spec.name != definition.name:
            raise ValueError("External tool spec name must match its definition")
        if spec.parameter_fields is not None or spec.params is not None:
            raise ValueError("External tools use their definition's parameter schema")
        timeout_seconds = _TIMEOUT.validate_python(spec.timeout_seconds)
        spec = replace(spec, resources=resource_spec(spec.resources))
        ToolScheduling(spec.parallel)
        existing = self._tools.get(definition.name, ())
        if existing and not spec.override:
            raise ToolNameConflict(f"Tool {definition.name!r} is already registered")
        declared = EnvelopeLayout.build(definition, spec.envelope)

        def install(registration: RegistrationState) -> Callable[[], None]:
            check_enabled = bind_enabled(spec.enabled, handler, registration)
            entry = _ExternalTool(
                definition,
                handler,
                owner.owner_name,
                registration,
                spec,
                timeout_seconds,
                check_enabled,
                handler,
                declared=declared,
            )
            entries = self._tools.setdefault(definition.name, [])
            entries.append(entry)

            def remove() -> None:
                entries.remove(entry)
                if not entries:
                    del self._tools[definition.name]

            return remove

        return owner.register(install)

    def is_readonly(self, name: str) -> bool:
        tool = self._visible(self._tools.get(name, []))
        return tool is not None and tool.spec.readonly

    def explain(self, name: str, *, scope: Scope | PluginScope) -> ToolExplanation:
        """Inspect the current binding, including registrations hidden from the model."""
        tool = self._visible(self._tools.get(name, []))
        if tool is None:
            return ToolExplanation(name, ToolAvailability.NOT_REGISTERED)
        definition = self._layout(tool).definition
        missing = self._missing_services(tool, scope)
        if missing:
            return ToolExplanation(name, ToolAvailability.MISSING_SERVICES, definition, missing)
        availability = (
            ToolAvailability.AVAILABLE if tool.enabled(scope) else ToolAvailability.DISABLED
        )
        return ToolExplanation(name, availability, definition)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self.visible_definitions(self._ctx)

    def _available(self, tool: _Tool, scope: Scope | PluginScope) -> bool:
        return not self._missing_services(tool, scope)

    def _missing_services(
        self, tool: _Tool, scope: Scope | PluginScope
    ) -> tuple[ServiceKey[Any], ...]:
        required = list(tool.enabled.required_services)
        missing: list[ServiceKey[Any]] = []
        if isinstance(tool, _ExternalTool):
            if ToolContexts not in scope:
                missing.append(ToolContext)
        else:
            required.extend(
                dependency.key
                for dependency in tool.plan.dependencies.values()
                if not dependency.optional
            )
        missing.extend(
            key
            for key in dict.fromkeys(required)
            if key not in scope and not (key is ToolContext and ToolContexts in scope)
        )
        return tuple(dict.fromkeys(missing))

    def visible_definitions(self, scope: Scope | PluginScope) -> tuple[ToolDefinition, ...]:
        return tuple(
            self._layout(tool).definition
            for entries in self._tools.values()
            if (tool := self._visible(entries)) is not None
            and self._available(tool, scope)
            and tool.enabled(scope)
        )

    def _visible(self, entries: list[_Tool]) -> _Tool | None:
        return next((tool for tool in reversed(entries) if tool.registration.active), None)

    def check_enabled(
        self, plan: PreparedToolCall, *, scope: Scope | PluginScope
    ) -> ToolResult | None:
        """Reject disabled calls before policy/approval without following a replaced binding."""
        tool = self._visible(self._tools.get(plan.call.name, []))
        bound = plan.binding.tool if isinstance(plan.binding, _Binding) else plan.binding
        if tool is not bound:
            return ToolResult.error(
                "Tool registration changed during batch planning; issue a fresh call"
            )
        if tool is not None and not tool.enabled(scope):
            return ToolResult.unavailable(f"Tool {plan.call.name} is currently disabled")
        return None

    def plan(self, call: ToolCall) -> PreparedToolCall:
        if call.malformed is not None:
            return PreparedToolCall(
                call,
                ToolScheduling(),
                ToolResult.error("Tool arguments must be a valid JSON object"),
            )
        tool = self._visible(self._tools.get(call.name, []))
        if tool is None:
            return PreparedToolCall(call, ToolScheduling())
        resources = tool.spec.resources
        keys = resources(call) if callable(resources) else resources
        if inspect.isawaitable(keys):
            if inspect.iscoroutine(keys):
                keys.close()
            raise TypeError("Resource resolvers must return resource names, not an awaitable")
        if isinstance(keys, str) or not isinstance(keys, Iterable):
            raise TypeError("Resource resolvers must return an iterable of resource names")
        layout = self._layout(tool)
        envelope, _ = layout.split(call.arguments)
        return PreparedToolCall(
            call,
            ToolScheduling(tool.spec.parallel, tuple(keys)),
            binding=_Binding(tool, layout),
            output_limit=tool.spec.output_limit,
            tool=_tool_info(tool.spec, layout),
            envelope=envelope,
        )

    async def execute(
        self,
        call: ToolCall,
        *,
        scope: Scope | PluginScope | None = None,
        call_context: ToolContext | None = None,
        _expected: object = _UNSPECIFIED,
        error_formatter: ErrorFormatter | None = None,
        default_timeout: float | None = None,
    ) -> ToolResult:
        """Run one call; a tool's own timeout wins over the caller's default.

        A planned call keeps the envelope layout the model was shown, even when a
        registry-wide envelope unmounts before it runs.
        """
        planned = _expected.layout if isinstance(_expected, _Binding) else None
        expected = _expected.tool if isinstance(_expected, _Binding) else _expected
        tool = self._resolve(call, expected)
        if isinstance(tool, ToolResult):
            return tool
        scope = scope or self._ctx
        try:
            unavailable = self._unavailable(tool, call, scope)
            if unavailable is not None:
                return unavailable
            if call_context is None and _needs_context(tool):
                call_context = scope[ToolContexts].create(call)
            return await self._call(
                tool,
                call,
                scope=scope,
                call_context=call_context,
                layout=planned,
                default_timeout=default_timeout,
            )
        except asyncio.CancelledError:
            if call_context is not None:
                call_context.signal.set()
            raise
        except TimeoutError as exc:
            return ToolResult(format_tool_error(call, exc, error_formatter), ToolStatus.TIMEOUT)
        except ApprovalUnavailable as exc:
            return ToolResult.unavailable(str(exc))
        except Exception as exc:
            # Tool callbacks are user code; convert arbitrary failures at the tool result boundary.
            return ToolResult.error(format_tool_error(call, exc, error_formatter))

    def _resolve(self, call: ToolCall, expected: object) -> _Tool | ToolResult:
        """The visible tool for a call, or the error result that prevents running it."""
        if call.malformed is not None:
            return ToolResult.error("Tool arguments must be a valid JSON object")
        tool = self._visible(self._tools.get(call.name, []))
        if expected is not _UNSPECIFIED and tool is not expected:
            return ToolResult.error(
                "Tool registration changed during batch planning; issue a fresh call"
            )
        if tool is None:
            return ToolResult.error(f"Unknown tool: {call.name}")
        return tool

    def _unavailable(
        self, tool: _Tool, call: ToolCall, scope: Scope | PluginScope
    ) -> ToolResult | None:
        if not tool.enabled(scope):
            return ToolResult.unavailable(f"Tool {call.name} is currently disabled")
        if not self._available(tool, scope):
            return ToolResult.error(f"Tool {call.name} is unavailable: missing injected service")
        return None

    async def _call(
        self,
        tool: _Tool,
        call: ToolCall,
        *,
        scope: Scope | PluginScope,
        call_context: ToolContext | None,
        layout: EnvelopeLayout | None,
        default_timeout: float | None,
    ) -> ToolResult:
        """Invoke the tool within its activation and timeout budget."""
        with tool.registration.activate():
            async with ToolExecutionBudget(
                tool.timeout_seconds if tool.timeout_seconds is not None else default_timeout
            ):
                if layout is None:
                    layout = self._layout(tool)
                _, own_arguments = layout.split(call.arguments)
                if isinstance(tool, _ExternalTool):
                    if call_context is None:
                        raise TypeError("External tools require a ToolContext")
                    return await tool.handler(own_arguments, call_context)
                arguments = tool.arguments.parse(own_arguments)
                extra: dict[type, Any] = (
                    {ToolContext: call_context} if call_context is not None else {}
                )
                bound = tool.plan.bind(scope, kwargs=arguments, extra=extra)
                if inspect.iscoroutinefunction(tool.callback):
                    value = await tool.callback(*bound.args, **bound.kwargs)
                else:
                    value = await run_sync(
                        partial(tool.callback, *bound.args, **bound.kwargs),
                        on_cancel=call_context.signal.set if call_context is not None else None,
                    )
                    if inspect.isawaitable(value):
                        value = await value
        return self._result(value)


def _needs_context(tool: _Tool) -> bool:
    return isinstance(tool, _ExternalTool) or (
        isinstance(tool, _PythonTool)
        and any(dependency.key is ToolContext for dependency in tool.plan.dependencies.values())
    )


def _tool_info(spec: ToolSpec, layout: EnvelopeLayout) -> ToolInfo:
    return ToolInfo(
        layout.definition,
        ToolMetadata(spec.metadata),
        spec.readonly,
        spec.parallel,
        spec.chain,
        spec.presentation,
        confirm=spec.confirm,
        envelope=tuple(binding.model for binding in layout.bindings),
    )


def _layout_for(
    definition: ToolDefinition,
    spec: ToolSpec,
    declared: EnvelopeLayout,
    *,
    envelopes: Iterable[_RegistryEnvelope],
    skip_conflicts: bool = False,
) -> EnvelopeLayout:
    """Merge the registry-wide envelopes whose selector accepts the declared tool."""
    info = _tool_info(spec, declared)
    shared = tuple(
        EnvelopePart(entry.model, entry.on_invalid, entry.position)
        for entry in envelopes
        if entry.select is None or entry.select(info)
    )
    return EnvelopeLayout.build(
        definition,
        spec.envelope,
        shared=shared,
        skip_conflicts=skip_conflicts,
        reserved_names=declared.reserved_names,
    )


def _resolve_timeout_seconds(
    timeout_seconds: ToolTimeout, callback: Callable[..., object]
) -> float | None:
    if not callable(timeout_seconds):
        return _TIMEOUT.validate_python(timeout_seconds)
    if (
        not isinstance(callback, MethodType)
        or not isinstance(callback.__self__, PluginBase)
        or not callback.__self__.is_active
    ):
        raise TypeError("Tool timeout_seconds resolvers require a mounted plugin method")
    if (
        inspect.iscoroutinefunction(timeout_seconds)
        or inspect.isgeneratorfunction(timeout_seconds)
        or inspect.isasyncgenfunction(timeout_seconds)
    ):
        raise TypeError("Tool timeout_seconds resolvers must be synchronous")
    resolved = timeout_seconds(callback.__self__)
    if inspect.isawaitable(resolved):
        if inspect.iscoroutine(resolved):
            resolved.close()
        raise TypeError("Tool timeout_seconds resolvers must return seconds or None")
    return _TIMEOUT.validate_python(resolved)
