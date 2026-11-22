import asyncio

import pytest

from harnessed import Agent, Plugin, PluginScope, needs, observes
from harnessed.kernel import Key, PluginBase, PluginStatus, Scope
from harnessed.sessions import SessionContext
from harnessed.testing import ScriptedModel

_CYCLE_DEADLINE_SECONDS = 2.0
_SESSION = Key[str]("session")


class Recorder:
    def __init__(self) -> None:
        self.setups: list[str] = []

    def count(self, name: str) -> int:
        return self.setups.count(name)


class Secrets:
    pass


def _plugins(log: Recorder, *, late_session: bool = False):
    class Provider(PluginBase, provides=Secrets, needs=(_SESSION,) if late_session else ()):
        async def setup(self, scope: Scope) -> None:
            log.setups.append("provider")
            scope.provide(Secrets, Secrets())

    class Observer(PluginBase, provides=True, needs=(_SESSION,) if late_session else ()):
        secrets = observes(Secrets)

        async def setup(self, scope: Scope) -> None:
            log.setups.append("observer")

    class Dependent(PluginBase):
        observer = needs(Observer)

        async def setup(self, scope: Scope) -> None:
            log.setups.append("dependent")

    return Observer, Dependent, Provider


async def test_a_batch_mounts_a_declared_provider_before_its_observer():
    log = Recorder()
    observer, dependent, provider = _plugins(log)
    async with Scope() as scope:
        await scope.install([observer(), dependent(), provider()], settle=True)
        assert scope[observer].secrets is scope[Secrets]
    assert log.setups == ["provider", "observer", "dependent"]


async def test_plugins_waiting_for_a_late_service_keep_provider_first_order():
    log = Recorder()
    observer, dependent, provider = _plugins(log, late_session=True)
    async with Scope() as scope:
        await scope.install([observer(), dependent(), provider()], settle=True)
        assert log.setups == []
        scope.provide(_SESSION, "session")
        await scope.settle()
        assert scope[observer].secrets is scope[Secrets]
    assert (log.count("observer"), log.count("dependent"), log.count("provider")) == (1, 1, 1)


async def test_a_provider_that_cannot_start_does_not_hold_back_its_observer():
    log = Recorder()

    class Stuck(PluginBase, provides=Secrets, needs=(_SESSION,)):
        pass

    class Observer(PluginBase, provides=True):
        secrets = observes(Secrets)

        async def setup(self, scope: Scope) -> None:
            log.setups.append("observer")

    async with Scope() as scope:
        handles = await scope.install([Observer(), Stuck()], settle=True)
        assert handles[0].status is PluginStatus.ACTIVE
        assert scope[Observer].secrets is None
    assert log.setups == ["observer"]


async def test_a_failed_provider_does_not_hold_back_its_observer():
    log = Recorder()

    class Failing(PluginBase, provides=Secrets, needs=(_SESSION,)):
        async def setup(self, scope: Scope) -> None:
            raise ValueError("provider failed")

    class Observer(PluginBase, provides=True, needs=(_SESSION,)):
        secrets = observes(Secrets)

        async def setup(self, scope: Scope) -> None:
            log.setups.append("observer")

    async with Scope() as scope:
        await scope.install([Observer(), Failing()], settle=True)
        scope.provide(_SESSION, "session")
        with pytest.raises(ExceptionGroup) as failure:
            await scope.settle()
        assert failure.value.subgroup(ValueError) is not None
        assert scope[Observer].secrets is None
    assert log.setups == ["observer"]


async def test_providers_observing_each_other_report_a_cycle_instead_of_waiting():
    class Left(PluginBase, provides=True):
        right = observes(Secrets)

    class Right(PluginBase, provides=Secrets):
        left = observes(Left)

    async with Scope() as scope:
        with pytest.raises(ExceptionGroup) as failure:
            async with asyncio.timeout(_CYCLE_DEADLINE_SECONDS):
                await scope.install([Right(), Left()], settle=True)
    assert "Cyclic plugin needs" in str(failure.value.exceptions[0])


async def test_a_session_starts_an_observer_once_when_its_provider_is_listed_later():
    log = Recorder()

    class SessionSecrets(Plugin, provides=True):
        session = needs(SessionContext)

        async def setup(self, scope: PluginScope) -> None:
            log.setups.append("provider")

    class Perception(Plugin, provides=True):
        session = needs(SessionContext)
        secrets = observes(SessionSecrets)

        async def setup(self, scope: PluginScope) -> None:
            log.setups.append("observer")

    class Pointer(Plugin):
        perception = needs(Perception)

        async def setup(self, scope: PluginScope) -> None:
            log.setups.append("dependent")

    agent = Agent(
        model=ScriptedModel([]),
        plugins=[Perception(), Pointer(), SessionSecrets()],
    )
    async with agent.session() as session:
        assert session.scope[Perception].secrets is session.scope[SessionSecrets]
    assert log.setups == ["provider", "observer", "dependent"]
