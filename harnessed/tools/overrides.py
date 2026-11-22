import math
from collections.abc import Callable, Mapping
from dataclasses import field
from difflib import get_close_matches
from types import MappingProxyType, MethodType
from typing import Annotated, Any, Protocol, Self, runtime_checkable

from pydantic import AfterValidator, AliasChoices, BaseModel, Field, create_model
from pydantic.dataclasses import dataclass
from pydantic.fields import FieldInfo

from harnessed.kernel import PluginBase
from harnessed.models.values import VALUE_CONFIG

_UNSET = object()


@dataclass(frozen=True, kw_only=True, config=VALUE_CONFIG)
class Param:
    """Overrides for one model-facing field. Numeric bounds are inclusive."""

    default: object = _UNSET
    description: str | None = None
    ge: int | float | None = None
    le: int | float | None = None

    def updated(self, patch: Self) -> Self:
        return type(self)(
            default=self.default if patch.default is _UNSET else patch.default,
            description=self.description if patch.description is None else patch.description,
            ge=self.ge if patch.ge is None else patch.ge,
            le=self.le if patch.le is None else patch.le,
        )


def _read_only(parameters: Mapping[str, Param]) -> Mapping[str, Param]:
    return MappingProxyType(dict(parameters))


@dataclass(frozen=True, config=VALUE_CONFIG)
class ToolOverride:
    description: str | None = None
    parameters: Annotated[Mapping[str, Param], AfterValidator(_read_only)] = field(
        default_factory=dict
    )

    def updated(self, description: str | None, parameters: Mapping[str, Param] | None) -> Self:
        merged = dict(self.parameters)
        if parameters is not None:
            # Validate the patches before merging them into existing overrides.
            patches = type(self)(parameters=parameters).parameters
            for name, patch in patches.items():
                merged[name] = merged[name].updated(patch) if name in merged else patch
        return type(self)(self.description if description is None else description, merged)

    def derive(
        self, model: type[BaseModel], schema: Mapping[str, Any], *, tool_name: str
    ) -> type[BaseModel]:
        if not self.parameters:
            return model
        properties = schema["properties"]
        fields: dict[str, Any] = {}
        names = {_wire_name(name, field): name for name, field in model.model_fields.items()}
        for name, patch in self.parameters.items():
            if name not in properties or name not in names:
                matches = get_close_matches(name, properties, n=1)
                hint = f" Did you mean {matches[0]!r}?" if matches else ""
                raise ValueError(f"Unknown parameter {name!r} for {tool_name}.{hint}")

            internal = names[name]
            shape = properties[name]
            if patch.ge is not None or patch.le is not None:
                numeric = _numeric_schema(shape, schema.get("$defs", {}))
                default = shape.get("default") if patch.default is _UNSET else patch.default
                _check_bounds(patch, numeric, default)
            fields[internal] = _derive_field(model.model_fields[internal], patch)
        return create_model(model.__name__, __base__=model, **fields)


def _wire_name(name: str, field: FieldInfo) -> str:
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return next((choice for choice in alias.choices if isinstance(choice, str)), name)
    return alias if isinstance(alias, str) else name


def _numeric_schema(schema: Mapping[str, Any], definitions: Mapping[str, Any]) -> Mapping[str, Any]:
    if "$ref" in schema:
        target = definitions.get(schema["$ref"].removeprefix("#/$defs/"), {})
        schema = {**target, **schema}
    if "anyOf" in schema:
        variants = [variant for variant in schema["anyOf"] if variant.get("type") != "null"]
        if len(variants) == 1:
            return _numeric_schema(variants[0], definitions)
    return schema


def _derive_field(original: FieldInfo, patch: Param) -> tuple[Any, FieldInfo]:
    attributes = original.asdict()["attributes"]
    if patch.description is not None:
        attributes["description"] = patch.description
    if patch.default is not _UNSET:
        attributes.update(default=patch.default, default_factory=None)

    if patch.default is not _UNSET or patch.ge is not None or patch.le is not None:
        attributes["validate_default"] = True
    derived = Field(**attributes, ge=patch.ge, le=patch.le)
    derived.metadata = [*original.metadata, *derived.metadata]
    return original.annotation, derived


def _check_bounds(patch: Param, schema: Mapping[str, Any], default: object) -> None:
    if schema.get("type") not in ("integer", "number"):
        raise ValueError("Numeric bounds require a numeric tool parameter")
    lower = schema.get("minimum", -math.inf)
    upper = schema.get("maximum", math.inf)
    exclusive_lower = schema.get("exclusiveMinimum", -math.inf)
    exclusive_upper = schema.get("exclusiveMaximum", math.inf)
    if patch.ge is not None:
        if patch.ge < lower or patch.ge <= exclusive_lower:
            raise ValueError("ge would widen the tool's lower bound")
        lower = patch.ge
    if patch.le is not None:
        if patch.le > upper or patch.le >= exclusive_upper:
            raise ValueError("le would widen the tool's upper bound")
        upper = patch.le
    if lower > upper or lower >= exclusive_upper or upper <= exclusive_lower:
        raise ValueError("Tool parameter bounds define an empty range")
    if isinstance(default, int | float) and not (
        lower <= default <= upper and exclusive_lower < default < exclusive_upper
    ):
        raise ValueError("The default is outside the configured bounds")


@runtime_checkable
class ToolOverrideProvider(Protocol):
    def tool_override(self, declaration: Callable[..., Any]) -> ToolOverride:
        """Return the recipe settings for a declared tool method."""
        ...


def callback_override(callback: Callable[..., Any]) -> ToolOverride:
    if not isinstance(callback, MethodType):
        return ToolOverride()
    owner = callback.__self__
    if isinstance(owner, PluginBase) and isinstance(owner.recipe, ToolOverrideProvider):
        return owner.recipe.tool_override(callback.__func__)
    return ToolOverride()
