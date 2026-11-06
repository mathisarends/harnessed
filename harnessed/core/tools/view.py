from collections.abc import Iterator, Mapping

from harnessed.core.model import ToolDefinition
from harnessed.kernel import Context

from .registry import ToolRegistry


class SessionTools(Mapping[str, ToolDefinition]):
    """Read-only live view of the definitions visible to this session's model.

    Iteration follows mapping semantics and yields names; use values() for
    definitions. Existing views reflect activation, enabled predicates and
    replacement without owning or changing registrations.
    """

    def __init__(self, registry: ToolRegistry, ctx: Context) -> None:
        self._registry = registry
        self._ctx = ctx

    def __getitem__(self, name: str) -> ToolDefinition:
        for definition in self._registry.visible_definitions(self._ctx):
            if definition.name == name:
                return definition
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        return (definition.name for definition in self._registry.visible_definitions(self._ctx))

    def __len__(self) -> int:
        return len(self._registry.visible_definitions(self._ctx))

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self)
