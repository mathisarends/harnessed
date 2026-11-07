from typing import Protocol, assert_type

from harnessed import Plugin
from harnessed.kernel import DependencyField, Key, needs, observes
from harnessed.plugins import Service
from harnessed.testing import mount_plugin


class Backend(Protocol):
    def read(self) -> str: ...


backend_key = Key[Backend]("backend")


class Feature(Plugin):
    backend = needs(Backend)
    named = needs(backend_key)
    optional = observes(Backend)


assert_type(Feature.backend, DependencyField[Backend])
assert_type(Feature.named, DependencyField[Backend])
assert_type(Feature.optional, DependencyField[Backend | None])
assert_type(Feature().backend, Backend)
assert_type(Feature().named, Backend)
assert_type(Feature().optional, Backend | None)


async def mounted(backend: Backend) -> None:
    async with mount_plugin(
        Feature(), services=(Service(Backend, backend), Service(backend_key, backend))
    ) as feature:
        assert_type(feature, Feature)
        assert_type(feature.backend.read(), str)


def invalid_binding_options() -> None:
    needs(Backend, optional=True)  # ty: ignore[unknown-argument]
    observes(Backend, optional=False)  # ty: ignore[unknown-argument]
