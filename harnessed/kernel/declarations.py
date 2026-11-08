from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol, Self

from .dependencies import DependencyField
from .di import Dependency, _dependency, bind_injected, injections
from .effects import resolve
from .events import Event
from .keys import Key, ServiceKey
from .spec import PluginSpec
from .state import PluginState

if TYPE_CHECKING:
    from .scope import Scope

type HookEntry = tuple[type[Event], Callable] | tuple[type[Event], Callable, int]


def _hooks(entries: Iterable[HookEntry]):
    for entry in entries:
        if len(entry) == 2:
            yield (*entry, 0)
        else:
            event, callback, priority = entry
            yield event, callback, priority


class Registration(Protocol):
    @property
    def needs(self) -> tuple[ServiceKey[Any], ...]: ...

    def mount(self, scope: Scope, callback: Callable) -> Any: ...


class Bundle:
    def __init__(self, name: str, *, needs: Iterable[ServiceKey[Any]] = ()) -> None:
        self.name = name
        self.needs = tuple(needs)
        self._hooks: list[tuple] = []
        self._registrations: list[tuple[Registration, Callable]] = []

    def add_hook(
        self, event: type[Event], callback: Callable, *, prepend: bool = False, priority: int = 0
    ):
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise TypeError("Listener priority must be an integer")
        self._hooks.append((event, callback, prepend, priority))
        return callback

    def on(self, event: type[Event], fn=None, *, prepend: bool = False, priority: int = 0):
        def register(callback):
            return self.add_hook(event, callback, prepend=prepend, priority=priority)

        return register(fn) if fn is not None else register

    def add_registration(self, registration: Registration, callback: Callable):
        self._registrations.append((registration, callback))
        return callback

    def _as_plugin_spec(self) -> PluginSpec:

        hooks, registrations = tuple(self._hooks), tuple(self._registrations)
        needs = list(self.needs)
        needs.extend(key for registration, _ in registrations for key in registration.needs)

        async def mount(scope):
            for event, callback, prepend, priority in hooks:
                scope.on(event, callback, prepend=prepend, priority=priority)
            for registration, callback in registrations:
                await resolve(registration.mount(scope, callback))
            yield

        return PluginSpec(self.name, mount, tuple(dict.fromkeys(needs)), (), {})


class PluginBase:
    """Reusable recipe reconstructed from explicit constructor inputs per activation.

    Constructors must only describe configuration and capabilities. Inputs retain
    their identity; constructor-created values and PluginState are fresh per mount.
    Acquire resources in setup and register their cleanup on its Scope.
    """

    needs: tuple[ServiceKey[Any], ...] = ()

    _recipe: Self | None = None

    slot: str | None = None

    _mount_state: dict[PluginState[Any], Any] | None = None

    _mount_injections: dict[str, Any] | None = None

    _provides: ServiceKey[Any] | None = None

    _constructor_args: tuple[Any, ...]
    _constructor_kwargs: dict[str, Any]

    def __init__(
        self,
        *,
        name: str | None = None,
        hooks: Iterable[HookEntry] = (),
        needs: Iterable[ServiceKey[Any]] | None = None,
        registrations: Iterable[tuple[Registration, Callable]] = (),
    ) -> None:
        if "_constructor_args" not in vars(self):
            self._constructor_args = ()
            self._constructor_kwargs = dict(
                name=name, hooks=tuple(hooks), needs=needs, registrations=tuple(registrations)
            )
            hooks = self._constructor_kwargs["hooks"]
            registrations = self._constructor_kwargs["registrations"]
        self.name = type(self).__name__ if name is None or name == "" else name
        self.hooks = tuple(hooks)
        self.needs = tuple(self.needs if needs is None else needs)
        self.registrations = tuple(registrations)

    def __init_subclass__(
        cls,
        *,
        slot: str | None = None,
        needs: Iterable[ServiceKey[Any]] | None = None,
        provides: bool | ServiceKey[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        if slot is not None:
            cls.slot = slot
        if needs is not None:
            cls.needs = tuple(needs)
        if provides is not None:
            if (
                provides is not True
                and provides is not False
                and not isinstance(provides, type | Key)
            ):
                raise TypeError("provides requires True, False, a service class or named Key")
            cls._provides = cls if provides is True else None if provides is False else provides
        try:
            annotations = inspect.get_annotations(cls)
        except NameError:
            # Python 3.14 defers ordinary annotations, including unresolved names.
            # They declare no dependencies and must not block class creation.
            annotations = {}
        for annotation in annotations.values():
            if (
                isinstance(annotation, str)
                and "Inject[" in annotation
                or not isinstance(annotation, str)
                and _dependency(annotation) is not None
            ):
                raise TypeError(
                    "Class-level Inject fields were removed; use needs(key) or observes(key)"
                )
        constructor = cls.__dict__.get("__init__")
        if constructor is not None:

            @wraps(constructor)
            def initialize(self: PluginBase, *args: Any, **kwargs: Any) -> None:
                if "_constructor_args" not in vars(self):
                    self._constructor_args = args
                    self._constructor_kwargs = dict(kwargs)
                constructor(self, *args, **kwargs)

            type.__setattr__(cls, "__init__", initialize)

    @property
    def is_active(self) -> bool:
        """Whether runtime values are available, including activation cleanup."""
        return self._mount_state is not None

    @property
    def recipe(self) -> Self:
        """The configured recipe which owns this activation, or this recipe itself."""
        return self._recipe if self._recipe is not None else self

    async def setup(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def teardown(self, *args: Any, **kwargs: Any) -> None:
        pass

    def create_instance(self) -> Self:
        """Create an unmounted instance from this recipe.

        Replay the original constructor with its explicit inputs, without copying
        arbitrary attributes or resources. Custom factories must return a fresh
        instance of exactly this class. Constructors may run more than once.
        """
        return type(self)(*self._constructor_args, **self._constructor_kwargs)

    def _registration_entries(self):
        return tuple(getattr(self, "registrations", ()))

    def _as_plugin_spec(self) -> PluginSpec:
        if self.is_active:
            raise TypeError("Cannot mount an already mounted instance; use its recipe")
        dependencies = _class_dependencies(type(self))
        members = {}
        for base in reversed(type(self).__mro__):
            members.update(vars(base))
        state_fields = tuple(
            dict.fromkeys(member for member in members.values() if isinstance(member, PluginState))
        )
        registrations = list(self._registration_entries())
        for name, member in members.items():
            if callable(member):
                callback = getattr(self, name)
                registrations.extend(
                    (recipe, callback) for recipe in getattr(member, "_harnessed_registrations", ())
                )
        needs: list[ServiceKey[Any]] = list(self.needs)
        needs.extend(binding.key for binding in dependencies.values() if not binding.optional)
        needs.extend(key for registration, _ in registrations for key in registration.needs)

        async def mount(scope):
            instance = self.create_instance()
            if instance is self or type(instance) is not type(self):
                raise TypeError("create_instance must return a fresh instance of the recipe class")
            if instance._mount_state is not None:
                raise TypeError("create_instance returned an already mounted instance")
            instance._recipe = self

            def clear_state() -> None:
                instance._mount_state = None
                instance._mount_injections = None

            scope.effect(clear_state)
            instance._mount_state = {member: member.factory() for member in state_fields}
            instance._mount_injections = {
                name: dependency.resolve(scope) for name, dependency in dependencies.items()
            }
            if self._provides is not None:
                scope.provide(self._provides, instance)
            await instance._setup(scope)
            try:
                for event, callback, priority in _hooks(getattr(instance, "hooks", ())):
                    scope.on(event, callback, priority=priority)
                for registration, callback in instance._registration_entries():
                    await resolve(registration.mount(scope, callback))
                for name, member in members.items():
                    if not callable(member):
                        continue
                    callback = getattr(instance, name)
                    for event, prepend, priority in getattr(member, "_harnessed_hooks", ()):
                        scope.on(event, callback, prepend=prepend, priority=priority)
                    for registration in getattr(member, "_harnessed_registrations", ()):
                        await resolve(registration.mount(scope, callback))
                yield instance
            finally:
                await instance._teardown(scope)

        return PluginSpec(
            getattr(self, "name", type(self).__name__),
            mount,
            tuple(dict.fromkeys(needs)),
            (),
            {},
            self.slot,
            () if self._provides is None else (self._provides,),
            tuple(binding.key for binding in dependencies.values() if binding.optional),
        )

    async def _setup(self, scope: Scope) -> None:
        bound = _lifecycle_arguments(self.setup, scope)
        await resolve(self.setup(*bound.args, **bound.kwargs))

    async def _teardown(self, scope: Scope) -> None:
        bound = _lifecycle_arguments(self.teardown, scope)
        await resolve(self.teardown(*bound.args, **bound.kwargs))


def _class_dependencies(cls: type[PluginBase]) -> dict[str, Dependency]:
    members: dict[str, object] = {}
    for base in reversed(cls.__mro__):
        members.update(vars(base))
    return {
        name: member.binding
        for name, member in members.items()
        if isinstance(member, DependencyField)
    }


def _lifecycle_arguments(callback, scope):
    parameters = tuple(inspect.signature(callback).parameters)
    args = (scope,) if parameters and parameters[0] not in injections(callback) else ()
    return bind_injected(callback, scope, args)
