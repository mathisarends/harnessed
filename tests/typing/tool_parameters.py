from typing import assert_type

from pydantic import BaseModel, Field

from harnessed import Plugin, tool
from harnessed.core.tools.registry import ToolRegistry
from harnessed.kernel import Inject
from harnessed.testing import call


class SearchParams(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)


class CatalogTools(Plugin):
    @tool(params=SearchParams, description="Search the catalog")
    async def search(self, params: SearchParams, *, catalog: Inject[str]) -> str:
        assert_type(params, SearchParams)
        assert_type(catalog, str)
        return params.query


async def check_parameters(registry: ToolRegistry, catalog: CatalogTools) -> None:
    @registry.tool(params=SearchParams)
    async def search(params: SearchParams) -> str:
        return params.query

    assert_type(await search(SearchParams(query="keyboard")), str)
    assert_type(await catalog.search(SearchParams(query="keyboard"), catalog="products"), str)
    registry.register(search, name="direct", params=SearchParams)
    call(CatalogTools.search, query="keyboard")
