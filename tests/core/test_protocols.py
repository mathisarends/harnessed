from pathlib import Path
from typing import Protocol

import pytest

from harnessed import Agent as Application
from harnessed import Approvals, Plugin
from harnessed.events import ToolCallFinishedEvent
from harnessed.kernel import PluginScope
from harnessed.models import Decision, Model, ModelChunk, ToolCall
from harnessed.plugins.filesystem import Filesystem, FileSystemBackend
from harnessed.plugins.shell import ProcResult, Shell, ShellBackend
from harnessed.plugins.spill import SpillStore
from harnessed.runtime.approval_policy import Approver
from harnessed.runtime.compaction import Summarizer
from harnessed.runtime.hooks import ToolPolicy
from harnessed.runtime.tokens import TokenCounter
from harnessed.testing import ScriptedModel


@pytest.mark.parametrize(
    "contract",
    [
        FileSystemBackend,
        Model,
        ShellBackend,
        Approver,
        TokenCounter,
        Summarizer,
        SpillStore,
    ],
)
def test_service_contracts_use_structural_protocols(contract):
    assert Protocol in contract.__bases__


class ForeignFiles:
    def __init__(self):
        self.files = {}

    async def read(self, path: Path, *, offset: int = 0, limit: int | None = None) -> str:
        return self.files[str(path)][offset : None if limit is None else offset + limit]

    async def write(self, path: Path, text: str) -> None:
        self.files[str(path)] = text


class ForeignModel:
    def __init__(self, responses):
        self.backend = ScriptedModel(responses)
        self.info = self.backend.info
        self.settings = self.backend.settings
        self.requests = self.backend.requests

    async def request(self, request):
        return await self.backend.request(request)

    def stream(self, request):
        return self.backend.stream(request)


async def test_foreign_fs_and_model_work_through_di_without_framework_inheritance():
    files = ForeignFiles()
    model = ForeignModel(
        [
            [ModelChunk(tool_call=ToolCall("write_file", {"path": "note.txt", "text": "hello"}))],
            [ModelChunk(tool_call=ToolCall("read_file", {"path": "note.txt"}))],
            [ModelChunk(text="done")],
        ]
    )

    class FilesProvider(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.provide(FileSystemBackend, files)

    assert ForeignFiles.__bases__ == (object,)
    assert ForeignModel.__bases__ == (object,)
    async with Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[FilesProvider(), Filesystem()],
    ).session() as agent:
        result = await agent.run("Write and read the note.")
        assert result.text == "done"
        assert files.files == {"note.txt": "hello"}
        results = [
            event.result for event in result.events if isinstance(event, ToolCallFinishedEvent)
        ]
        assert results[-1].content == "hello" and (not results[-1].is_error)
        definition = next(tool for tool in model.requests[0].tools if tool.name == "read_file")
        assert set(definition.parameters["properties"]) == {"path", "limit", "offset"}


class ForeignShell:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def run(self, argv, *, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        return self.result


class ForeignApprover:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def ask(self, request):
        call, reason = request.call, request.reason
        self.calls.append((call, reason))
        return self.decision


async def test_foreign_shell_and_approver_are_accepted_structurally(tmp_path):
    shell = ForeignShell(ProcResult(7, "failed test", ""))
    approver = ForeignApprover(Decision.ALLOW)
    call = ToolCall("run_command", {"argv": ["python", "-m", "pytest"]})
    model = ForeignModel([[ModelChunk(tool_call=call)], [ModelChunk(text="reviewed")]])

    class ForeignServices(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.provide(ShellBackend, shell)
            scope.provide(Approver, approver)

    class Policy(Plugin, needs=[Approver]):
        async def setup(self, scope: PluginScope) -> None:
            @scope.on(ToolPolicy)
            async def ask(event, next_):
                return Decision.ASK

    assert ForeignShell.__bases__ == (object,)
    assert ForeignApprover.__bases__ == (object,)
    harness = Application(
        model=model,
        approvals=Approvals(default=Decision.ALLOW),
        plugins=[ForeignServices(), Shell(tmp_path), Policy()],
    )
    async with harness.session() as agent:
        result = await agent.run("Run the tests.")
        returned = next(
            event.result for event in result.events if isinstance(event, ToolCallFinishedEvent)
        )
        assert returned.exit_code == 7 and returned.is_error
        assert shell.calls == [(["python", "-m", "pytest"], tmp_path.resolve(), 30)]
        assert len(approver.calls) == 1 and approver.calls[0][0] == call
