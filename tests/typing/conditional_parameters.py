from typing import Annotated, assert_type

from harnessed import Plugin, PluginConfig, tool
from harnessed.kernel import Inject
from harnessed.tools import ToolParameter


class SearchOptions(PluginConfig):
    archives: bool = False


def supports_archives(options: Inject[SearchOptions]) -> bool:
    return options.archives


type IncludeArchived = Annotated[bool, ToolParameter(enabled=supports_archives)]


class Search(Plugin[SearchOptions]):
    def can_search_archives(self) -> bool:
        assert_type(self.config, SearchOptions)
        return self.config.archives

    @tool
    def search(
        self,
        query: str,
        *,
        include_archived: Annotated[bool, ToolParameter(enabled=can_search_archives)] = False,
    ) -> str:
        assert_type(include_archived, bool)
        return query


def check_parameter_metadata(search: Search) -> None:
    assert_type(search.search("notes", include_archived=True), str)
    assert_type(ToolParameter(enabled=False), ToolParameter)
    assert_type(ToolParameter(enabled=supports_archives), ToolParameter)
