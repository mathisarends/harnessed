from collections.abc import Iterator, Mapping

from harnessed.kernel import Scope
from harnessed.models import ToolDefinition

from .availability import ToolExplanation
from .info import ToolInfo
from .references import ToolSelector
from .registry import ToolRegistry


class SessionTools(Mapping[str, ToolDefinition]):
    """Read-only live view of the definitions visible to this session's model.

    Iteration follows mapping semantics and yields names; use values() for
    definitions. Existing views reflect activation, enabled predicates and
    replacement without owning or changing registrations.
    """

    def __init__(self, registry: ToolRegistry, scope: Scope) -> None:
        self._registry = registry
        self._ctx = scope

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

    def explain(self, name: str) -> ToolExplanation:
        """Explain a registered tool's visibility; this does not grant execution permission.

        Missing services take precedence over predicates. Predicate failures
        propagate as they do when constructing a model request.
        """
        return self._registry.explain(name, scope=self._ctx)

    def resolve(self, selector: ToolSelector) -> ToolInfo | None:
        """Describe the visible tool a selector names, or None when the model cannot call it.

        Use the returned definition's name in prompts instead of repeating tool
        names as strings. An action member resolves to the first visible tool
        that declares it.
        """
        return self._registry.resolve(selector, scope=self._ctx)
