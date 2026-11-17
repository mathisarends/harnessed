"""Run offline with: uv run python -m examples.43_conditional_parameters"""

import asyncio
from typing import Annotated

from pydantic import Field

from harnessed import Agent, Plugin, PluginConfig, ToolParameter, tool
from harnessed.testing import ScriptedModel, call


class SearchConfig(PluginConfig):
    archives: bool = False


class Search(Plugin[SearchConfig]):
    def supports_archives(self) -> bool:
        return self.config.archives

    @tool(readonly=True)
    def search(
        self,
        query: str,
        *,
        include_archived: Annotated[
            bool,
            Field(description="Include archived records."),
            ToolParameter(enabled=supports_archives),
        ] = False,
    ) -> str:
        return f"Results for {query}; include_archived={include_archived}"


async def demonstrate(*, archives: bool) -> None:
    search = Search(config=SearchConfig(archives=archives))
    arguments = {"include_archived": True} if archives else {}
    model = ScriptedModel(
        [
            call(search.search, query="notes", **arguments),
            # Even passing the default explicitly is forbidden when disabled.
            call("search", query="notes", include_archived=False),
            "Done.",
        ]
    )
    async with Agent(model=model, plugins=[search]).session() as session:
        definition = session.tools["search"]
        assert ("include_archived" in definition.parameters["properties"]) is archives
        result = await session.run("Search notes")
        assert model.requests[0].tools == (definition,)
        assert result.tool_calls[0][1].text.endswith(f"include_archived={archives}")
        assert result.tool_calls[1][1].is_error is not archives
        print(f"archives={archives}: {dict(definition.parameters['properties'])}")
        print(result.tool_calls[0][1].text)


async def main() -> None:
    await demonstrate(archives=False)
    await demonstrate(archives=True)


if __name__ == "__main__":
    asyncio.run(main())
