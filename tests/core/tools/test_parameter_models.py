import json
from datetime import date
from typing import Annotated, Self

import pytest
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError, model_validator

from harnessed import Agent, Plugin, ToolCall, ToolContext, tool
from harnessed.core.tools.registry import ToolRegistry, ToolSpec
from harnessed.kernel import Inject, Scope
from harnessed.plugins import Service
from harnessed.testing import ScriptedModel, call


class SearchParams(BaseModel):
    query: str = Field(description="Text to search for", min_length=1)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def check_query(self) -> Self:
        if not self.query.strip():
            raise ValueError("Query must contain text")
        return self


class CatalogTools(Plugin):
    @tool(
        params=SearchParams, name="catalog_search", description="Search the catalog", readonly=True
    )
    async def search(
        self, params: SearchParams, *, catalog: Inject[str], call: Inject[ToolContext]
    ) -> dict[str, str | int]:
        assert type(params) is SearchParams
        assert call.call.name == "catalog_search"
        return {"query": params.query, "limit": params.limit, "catalog": catalog}


@pytest.mark.parametrize("bound", [False, True])
async def test_parameter_model_mount_schema_execution_and_scripted_references(bound):
    catalog = CatalogTools()
    reference = catalog.search if bound else CatalogTools.search
    response = call(reference, query="keyboard")
    assert response.message.tool_calls[0].arguments == {"query": "keyboard"}
    model = ScriptedModel([response, "done"])
    result = await Agent(model=model, plugins=[catalog, Service(str, "products")]).run("Search")
    assert not result.tool_calls[0][1].is_error
    assert json.loads(result.tool_calls[0][1].text) == {
        "query": "keyboard",
        "limit": 5,
        "catalog": "products",
    }
    definition = next(tool for tool in model.requests[0].tools if tool.name == "catalog_search")
    assert definition.description == "Search the catalog"
    schema = definition.parameters
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"query", "limit"}
    assert schema["required"] == ("query",)
    assert schema["properties"]["query"]["description"] == "Text to search for"
    assert schema["properties"]["limit"]["default"] == 5
    assert schema["properties"]["limit"]["minimum"] == 1
    assert schema["properties"]["limit"]["maximum"] == 20


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": " "},
        {"query": 42},
        {"query": "keyboard", "limit": 0},
        {"query": "keyboard", "limit": 21},
        {"query": "keyboard", "limit": "5"},
        {"query": "keyboard", "limit": True},
        {"query": "keyboard", "unexpected": True},
        {"params": {"query": "keyboard"}},
    ],
)
async def test_invalid_parameter_models_fail_before_invocation_and_in_scripted_calls(arguments):
    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool(params=SearchParams)
        async def search(params: SearchParams) -> str:
            pytest.fail("Invalid parameter object reached the tool")

        result = await registry.execute(ToolCall("search", arguments))
        assert result.is_error and "ValidationError" in result.text
        with pytest.raises(ValidationError):
            call(CatalogTools.search, **arguments)


async def test_parameter_model_direct_registration_sync_tools_and_annotated_aliases():
    type Request = Annotated[SearchParams, "Search request"]

    def search(*, request: Request, count: Inject[int]) -> int:
        assert type(request) is SearchParams
        return request.limit + count

    async with Scope() as scope:
        registry = ToolRegistry(scope)
        scope.provide(int, 2)
        dispose = registry.register(search, params=SearchParams)
        assert (await registry.execute(ToolCall("search", {"query": "keyboard"}))).text == "7"
        await dispose()
        assert registry.definitions == ()


class FilterParams(BaseModel):
    address: dict[str, list[int]] = Field(default_factory=dict)
    after: date = Field(alias="publishedAfter")
    terms: list[str] = Field(default_factory=list)


async def test_parameter_models_preserve_aliases_json_types_and_per_call_default_factories():
    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool(params=FilterParams)
        async def filter_products(request: FilterParams) -> FilterParams:
            assert isinstance(request.after, date)
            assert request.terms == []
            request.terms.append("fresh")
            return request

        schema = registry.definitions[0].parameters
        assert "publishedAfter" in schema["properties"] and "after" not in schema["properties"]
        for _ in range(2):
            result = await registry.execute(
                ToolCall("filter_products", {"publishedAfter": "2026-10-30"})
            )
            assert not result.is_error
            assert json.loads(result.text) == {
                "address": {},
                "after": "2026-10-30",
                "terms": ["fresh"],
            }


async def test_parameter_models_forbid_extra_inputs_even_when_model_allows_them():
    class OpenParams(BaseModel):
        model_config = ConfigDict(extra="allow")
        query: str

    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool(params=OpenParams)
        def search(params: OpenParams) -> str:
            return params.query

        assert registry.definitions[0].parameters["additionalProperties"] is False
        assert (await registry.execute(ToolCall("search", {"query": "ok", "extra": 1}))).is_error
        assert (await registry.execute(ToolCall("search", {"query": "ok"}))).text == "ok"


class TreeParams(BaseModel):
    name: str
    children: list["TreeParams"] = Field(default_factory=list)


async def test_recursive_parameter_models_keep_nested_schema_references():
    async with Scope() as scope:
        registry = ToolRegistry(scope)

        @registry.tool(params=TreeParams)
        async def tree(params: TreeParams) -> str:
            return params.children[0].name

        schema = registry.definitions[0].parameters
        assert "$ref" not in schema
        assert schema["properties"]["children"]["items"]["$ref"] == "#/$defs/TreeParams"
        assert schema["$defs"]["TreeParams"]["properties"]["name"]["type"] == "string"
        result = await registry.execute(
            ToolCall("tree", {"name": "root", "children": [{"name": "child"}]})
        )
        assert result.text == "child"


def no_parameters() -> None: ...


def multiple_parameters(params: SearchParams, other: int) -> None: ...


def wrong_annotation(params: str) -> None: ...


def unannotated(params) -> None: ...


_DEFAULT_PARAMS = SearchParams(query="default")


def default_parameter(params: SearchParams = _DEFAULT_PARAMS) -> None: ...


def positional_only(params: SearchParams, /) -> None: ...


def variadic(params: SearchParams, **kwargs: str) -> None: ...


@pytest.mark.parametrize(
    "callback",
    [
        no_parameters,
        multiple_parameters,
        wrong_annotation,
        unannotated,
        default_parameter,
        positional_only,
        variadic,
    ],
)
async def test_invalid_parameter_model_signatures_fail_registration_atomically(callback):
    async with Scope() as scope:
        registry = ToolRegistry(scope)
        with pytest.raises(TypeError):
            registry.register(callback, params=SearchParams)
        assert registry.definitions == ()


@pytest.mark.parametrize(
    "params",
    [SearchParams(query="test"), str, BaseModel, RootModel[str], RootModel[dict[str, str]]],
)
def test_params_requires_an_object_model_class(params):
    with pytest.raises(TypeError, match="params must be a Pydantic BaseModel subclass"):
        tool(params=params)(no_parameters)


def test_parameter_fields_cannot_be_combined_with_a_parameter_model():
    with pytest.raises(ValueError, match="params cannot be combined with parameter_fields"):
        tool(params=SearchParams, parameter_fields=lambda owner: {"query": Field(min_length=3)})(
            wrong_annotation
        )
    registry = ToolRegistry(Scope())
    with pytest.raises(ValueError, match="params cannot be combined with parameter_fields"):
        registry.register(wrong_annotation, params=SearchParams, parameter_fields={})
    assert registry.definitions == ()


def test_external_tools_cannot_declare_a_python_parameter_model():
    from harnessed.core.model import ToolDefinition

    async def handler(arguments, context):
        pytest.fail("Invalid external registration was invoked")

    scope = Scope()
    registry = ToolRegistry(scope)
    with pytest.raises(ValueError, match="External tools use their definition's parameter schema"):
        registry.register_external(
            ToolDefinition("search", "", {}),
            handler,
            owner=scope,
            spec=ToolSpec(params=SearchParams),
        )
    assert registry.definitions == ()
