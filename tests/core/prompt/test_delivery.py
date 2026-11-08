import asyncio

import pytest

from harnessed import Agent, Instructions, Plugin, SectionDelivery, SystemPrompt, tool
from harnessed.core.events.messages import PluginInstructionsChangedEvent
from harnessed.hooks import PrepareRequest
from harnessed.kernel import PluginScope, PluginStatus, needs
from harnessed.models import ModelRequest
from harnessed.services import SessionContext
from harnessed.testing import ScriptedModel
from tests._audit import Audit


class Feature(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt("Runtime guidance.")

    @tool
    async def inspect(self) -> str:
        return "Inspected."


@pytest.mark.parametrize("fail", [False, True])
async def test_use_owns_tools_and_guidance_through_normal_and_exceptional_exit(fail):
    async with Agent(model=ScriptedModel(["during", "after"])).session() as session:
        try:
            async with session.use(Feature(), section_delivery=SectionDelivery.CONTEXT) as mount:
                assert mount.instance is not None
                assert "inspect" in session.tools
                await session.run("during")
                if fail:
                    raise ValueError("leave the mount")
        except ValueError as error:
            assert fail and str(error) == "leave the mount"
        assert "inspect" not in session.tools
        await session.run("after")
        assert all("Runtime guidance." not in message.text for message in session.messages)


async def test_context_delivery_is_mount_owned_and_leaves_system_text_fixed():
    model = ScriptedModel(["one", "two"])
    async with Agent(model=model, instructions="Fixed.").session() as session:
        session_audit = Audit(session)
        handle = await session.mount(Feature(), section_delivery=SectionDelivery.CONTEXT)
        assert not any(isinstance(e, PluginInstructionsChangedEvent) for e in session_audit.events)
        await session.run("Use the capability")
        first = model.requests[-1]
        assert first.messages[0].text == "Fixed."
        assert any("Runtime guidance." in m.text for m in first.messages[1:])
        assert [definition.name for definition in first.tools] == ["inspect"]
        await handle.aclose()
        assert session.snapshot().plugin_instructions == {}
        await session.run("After unmount")
        assert all("Runtime guidance." not in m.text for m in model.requests[-1].messages)
        assert model.requests[-1].tools == ()
        assert await session.prompt.render() == "Fixed."
        assert any("Runtime guidance." in m.text for m in first.messages)


async def test_context_delivery_suspends_and_restarts_with_the_plugin():
    class Backend:
        pass

    calls = []

    class Temporal(Plugin, needs=(Backend,)):
        def instructions(self) -> SystemPrompt:
            calls.append(self)
            return SystemPrompt(f"Activation {len(calls)}.")

    model = ScriptedModel(["one", "two", "three"])
    async with Agent(model=model).session() as session:
        handle = await session.mount(Temporal(), section_delivery=SectionDelivery.CONTEXT)
        assert handle.status is PluginStatus.WAITING
        assert calls == [] and session.snapshot().plugin_instructions == {}
        remove = session.scope.provide(Backend, Backend())
        await session.scope.settle()
        await session.run("Mounted")
        await remove()
        await session.scope.settle()
        assert session.snapshot().plugin_instructions == {}
        await session.run("Suspended")
        assert all("Activation 1." not in m.text for m in model.requests[-1].messages)
        session.scope.provide(Backend, Backend())
        await session.scope.settle()
        await session.run("Restarted")
        assert any("Activation 2." in m.text for m in model.requests[-1].messages)
        assert len(calls) == 2


async def test_ignore_does_not_execute_providers_or_factories_or_read_files(tmp_path):
    async def factory(scope: PluginScope) -> str:
        pytest.fail("Ignored factories must not run")

    class Ignored(Feature):
        def instructions(self) -> SystemPrompt:
            pytest.fail("Ignored configuration providers must not run")
            return SystemPrompt(
                Instructions.from_factory(factory=factory),
                Instructions.from_file(path=tmp_path / "missing.txt"),
            )

    async with Agent(model=ScriptedModel(["done"])).session() as session:
        handle = await session.mount(Ignored(), section_delivery=SectionDelivery.IGNORE)
        assert handle.status is PluginStatus.ACTIVE
        assert session.snapshot().plugin_instructions == {}
        await session.run("Tools still mounted")
        assert await session.prompt.render() == ""


@pytest.mark.parametrize("failure", ["provider", "factory", "file", "setup", "cancel"])
async def test_delivery_failures_roll_back_tools_context_and_owned_effects(failure, tmp_path):
    effects = []

    async def factory(scope: PluginScope) -> str:
        scope.effect(lambda: effects.append("factory cleaned"))
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "factory":
            raise ValueError("factory failed")
        return "Session rules."

    class Broken(Feature):
        async def setup(self, scope: PluginScope) -> None:
            scope.effect(lambda: effects.append("setup cleaned"))
            if failure == "setup":
                raise ValueError("setup failed")

        async def teardown(self, scope: PluginScope) -> None:
            effects.append("teardown")

        def instructions(self) -> SystemPrompt:
            if failure == "provider":
                raise ValueError("provider failed")
            return SystemPrompt(
                super().instructions(),
                Instructions.from_factory(factory=factory),
                Instructions.from_file(path=tmp_path / "missing.txt")
                if failure == "file"
                else None,
                "Configured rules.",
            )

    model = ScriptedModel(["after failure"])
    async with Agent(model=model).session() as session:
        error = (
            asyncio.CancelledError
            if failure == "cancel"
            else FileNotFoundError
            if failure == "file"
            else ValueError
        )
        with pytest.raises(error):
            await session.mount(Broken(), section_delivery=SectionDelivery.CONTEXT)
        assert session.snapshot().plugin_instructions == {}
        assert "setup cleaned" in effects
        if failure != "setup":
            assert "teardown" in effects
        if failure in ("factory", "file", "cancel"):
            assert "factory cleaned" in effects
        await session.run("No leaked capability")
        assert model.requests[-1].tools == ()
        assert all("rules." not in m.text for m in model.requests[-1].messages)


async def test_factory_runs_once_per_mount_activation_and_cleanup_belongs_to_mount():
    calls = []
    cleaned = []

    async def factory(scope: PluginScope) -> str:
        calls.append(scope)
        scope.effect(lambda: cleaned.append(scope))
        return "Factory guidance."

    class Factory(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(Instructions.from_factory(factory=factory))

    async with Agent(model=ScriptedModel(["one", "two"])).session() as session:
        handle = await session.mount(Factory(), section_delivery=SectionDelivery.CONTEXT)
        await session.run("First")
        await session.run("Second")
        assert len(calls) == 1 and cleaned == []
        await handle.aclose()
        assert cleaned == calls and session.snapshot().plugin_instructions == {}


async def test_duplicate_plugin_names_have_independent_mount_context():
    class Guidance(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Runtime guidance.")

    model = ScriptedModel(["both", "one", "none"])
    async with Agent(model=model).session() as session:
        one = await session.mount(Guidance(), section_delivery=SectionDelivery.CONTEXT)
        two = await session.mount(Guidance(), section_delivery=SectionDelivery.CONTEXT)
        await session.run("Both")
        assert [m.text for m in model.requests[-1].messages].count("Runtime guidance.") == 2
        await one.aclose()
        await session.run("One")
        assert [m.text for m in model.requests[-1].messages].count("Runtime guidance.") == 1
        await two.aclose()
        await session.run("None")
        assert all(m.text != "Runtime guidance." for m in model.requests[-1].messages)


async def test_runtime_guidance_is_not_restored_as_a_durable_fact():
    model = ScriptedModel(["one", "two"])
    agent = Agent(model=model)
    async with agent.session() as session:
        await session.mount(Feature(), section_delivery=SectionDelivery.CONTEXT)
        await session.run("Mounted")
    async with agent.session() as restored:
        assert restored.snapshot().plugin_instructions == {}
        await restored.run("Reopened")
        assert all("Runtime guidance." not in m.text for m in model.requests[-1].messages)


async def test_owned_request_hook_is_inactive_during_its_plugin_teardown():
    observed = []

    class InspectTeardown(Feature):
        context = needs(SessionContext)

        async def teardown(self, scope: PluginScope) -> None:
            request = ModelRequest(())
            result = await scope.dispatch(
                PrepareRequest(self.context, request), default=lambda: request
            )
            observed.append(result.messages)

    async with Agent(model=ScriptedModel([])).session() as session:
        handle = await session.mount(InspectTeardown(), section_delivery=SectionDelivery.CONTEXT)
        await handle.aclose()
        assert observed == [()]
