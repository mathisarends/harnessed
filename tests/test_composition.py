import pytest

from harnessed import Agent, Plugin, Session, SystemPrompt
from harnessed.kernel import PluginScope, needs
from harnessed.plugins import Filesystem, Shell
from harnessed.prompts import Instructions
from harnessed.sessions import SessionContext
from harnessed.testing import ScriptedModel


@pytest.mark.parametrize("shell", [False, True])
async def test_explicit_project_composition_adds_only_the_requested_tools(tmp_path, shell):
    (tmp_path / "AGENTS.md").write_text("Use focused changes.", encoding="utf-8")
    model = ScriptedModel(["ready"])
    filesystem = Filesystem(tmp_path)
    commands = [Shell(tmp_path)] if shell else []
    app = Agent(
        model=model,
        plugins=[filesystem, *commands],
        instructions=SystemPrompt.from_plugins(Instructions.from_file(path=tmp_path / "AGENTS.md")),
    )
    async with app.session() as session:
        await session.run("inspect")
    request = model.requests[0]
    assert request.messages[0].text.startswith("Use focused changes.")
    names = {tool.name for tool in request.tools}
    assert {"read_file", "write_file"} <= names
    assert ("run_command" in names) == shell


async def test_session_installs_waiting_plugins_and_disposes_the_driver_once(monkeypatch):
    starts, closes = [], []
    original_close = Session.aclose

    async def close(self):
        closes.append(self)
        await original_close(self)

    monkeypatch.setattr(Session, "aclose", close)

    class Observer(Plugin, needs=[SessionContext]):
        async def setup(self, scope: PluginScope) -> None:
            starts.append(scope[SessionContext])

    app = Agent(model=ScriptedModel(["ready"]), plugins=[Observer()])
    async with app.session() as session:
        assert [context.log for context in starts] == [session.log]
        assert (await session.run("hello")).text == "ready"
    assert closes == [session]


async def test_session_service_remains_available_during_dependent_teardown():
    seen = []

    class Dependent(Plugin):
        session = needs(SessionContext)

        needs = (SessionContext,)

        async def teardown(self, scope: PluginScope) -> None:
            seen.append(self.session.id)

    async with Agent(model=ScriptedModel([]), plugins=[Dependent()]).session() as session:
        pass
    assert seen == [session.id]
