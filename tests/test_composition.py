import pytest

from harnessed import (
    Agent,
    Instructions,
    Plugin,
    Session,
    SystemPrompt,
    plugin,
)
from harnessed.kernel import PluginScope, Scope, needs
from harnessed.plugins import Filesystem, Shell
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
        prompt=SystemPrompt.from_plugins(Instructions.from_file(path=tmp_path / "AGENTS.md")),
        plugins=[filesystem, *commands],
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

    @plugin(needs=[SessionContext])
    async def observer(scope: Scope):
        starts.append(scope[SessionContext])
        yield

    app = Agent(model=ScriptedModel(["ready"]), plugins=[observer()])
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
