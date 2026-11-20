import sys

import pytest

from harnessed import Agent, Plugin, Session, SystemPrompt
from harnessed.errors import PluginConflict, PluginDependencyError
from harnessed.kernel import PluginScope, needs
from harnessed.models import ToolCall
from harnessed.plugins import Filesystem, LocalWorkspace, Shell, WorkspaceBackends
from harnessed.prompts import Instructions
from harnessed.sessions import SessionContext
from harnessed.testing import InMemoryFS, ScriptedModel
from harnessed.tools import ToolRegistry
from harnessed.tools.workspace import ShellBackend


@pytest.mark.parametrize("shell", [False, True])
async def test_explicit_project_composition_adds_only_the_requested_tools(tmp_path, shell):
    (tmp_path / "AGENTS.md").write_text("Use focused changes.", encoding="utf-8")
    model = ScriptedModel(["ready"])
    filesystem = Filesystem()
    commands = [Shell()] if shell else []
    app = Agent(
        model=model,
        plugins=[LocalWorkspace(root=tmp_path), filesystem, *commands],
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

    class Observer(Plugin):
        session = needs(SessionContext)

        async def setup(self, scope: PluginScope) -> None:
            starts.append(self.session)

    app = Agent(model=ScriptedModel(["ready"]), plugins=[Observer()])
    async with app.session() as session:
        assert [context.log for context in starts] == [session.log]
        assert (await session.run("hello")).text == "ready"
    assert closes == [session]


async def test_session_service_remains_available_during_dependent_teardown():
    seen = []

    class Dependent(Plugin):
        session = needs(SessionContext)

        async def teardown(self, scope: PluginScope) -> None:
            seen.append(self.session.id)

    async with Agent(model=ScriptedModel([]), plugins=[Dependent()]).session() as session:
        pass
    assert seen == [session.id]


async def test_local_file_and_shell_tools_share_one_workspace(tmp_path):
    (tmp_path / "nested").mkdir()
    app = Agent(
        model=ScriptedModel([]),
        plugins=[LocalWorkspace(root=tmp_path), Filesystem(), Shell(cwd="nested")],
    )
    async with app.session() as session:
        tools = session.scope[ToolRegistry]
        written = await tools.execute(
            ToolCall("write_file", {"path": "nested/check.txt", "text": "shared"})
        )
        assert not written.is_error
        command = await tools.execute(
            ToolCall(
                "run_command", {"argv": [sys.executable, "-c", "print(open('check.txt').read())"]}
            )
        )
        assert not command.is_error and "shared" in command.text


async def test_workspace_exposes_only_supplied_backends_and_requested_tools():
    provider = WorkspaceBackends(filesystem=InMemoryFS())
    async with Agent(model=ScriptedModel([]), plugins=[provider]).session() as session:
        assert not session.tools and ShellBackend not in session.scope
    with pytest.raises(PluginDependencyError, match="ShellBackend"):
        async with Agent(model=ScriptedModel([]), plugins=[provider, Shell()]).session():
            pytest.fail("Shell requires an explicit backend")
    with pytest.raises(PluginConflict, match="workspace"):
        Agent(plugins=[LocalWorkspace(), provider])
