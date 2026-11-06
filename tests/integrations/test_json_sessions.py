import asyncio
import json
import sys
from pathlib import Path

import pytest

from harnessed import Agent, Plugin, tool
from harnessed.errors import SessionFormatError, SessionWriteConflict
from harnessed.integrations.json import JsonSessionStore
from harnessed.sessions import StoredSession
from harnessed.testing import ScriptedModel
from tests._checkpoints import checkpoint, commit


async def test_json_store_is_opt_in_and_handles_arbitrary_application_ids(tmp_path):
    root = tmp_path / "sessions"
    store = JsonSessionStore(root=root)
    assert await store.load_session("../tenant:客户/4711") is None
    assert not root.exists()
    assert await store.save_session("../tenant:客户/4711", commit(), expected_revision=None) == 1
    assert await JsonSessionStore(root=root).load_session("../tenant:客户/4711") == StoredSession(
        checkpoint(), 1
    )
    assert len(list(root.glob("*.json"))) == 1
    assert all(path.parent == root for path in root.iterdir())


async def test_json_store_compare_and_set_preserves_the_winner(tmp_path):
    store = JsonSessionStore(root=tmp_path)
    await store.save_session("one", commit("first"), expected_revision=None)
    assert await store.save_session("one", commit("second"), expected_revision=1) == 2
    with pytest.raises(SessionWriteConflict):
        await store.save_session("one", commit("stale"), expected_revision=1)
    with pytest.raises(SessionWriteConflict):
        await store.save_session("one", commit("overwrite"), expected_revision=None)
    assert await store.load_session("one") == StoredSession(checkpoint("second"), 2)


@pytest.mark.parametrize("change", ["truncated", "version", "revision", "identity", "extra"])
async def test_json_store_rejects_corruption_without_overwriting_it(tmp_path, change):
    store = JsonSessionStore(root=tmp_path)
    await store.save_session("one", commit(), expected_revision=None)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    if change == "truncated":
        data = "{"
    else:
        key, value = {
            "version": ("version", 1),
            "revision": ("revision", True),
            "identity": ("conversation_id", "someone-else"),
            "extra": ("unknown", 1),
        }[change]
        record[key] = value
        data = json.dumps(record)
    path.write_text(data)
    with pytest.raises(SessionFormatError):
        await store.load_session("one")
    with pytest.raises(SessionFormatError):
        await store.save_session("one", commit("replacement"), expected_revision=1)
    assert path.read_text() == data


async def test_json_compare_and_set_is_atomic_between_processes(tmp_path: Path):
    store = JsonSessionStore(root=tmp_path)
    await store.save_session("shared", commit("initial"), expected_revision=None)
    code = """
import asyncio
import sys
from harnessed.errors import SessionWriteConflict
from harnessed.integrations.json import JsonSessionStore
from harnessed import SessionState
from harnessed.events import SessionControlChangedEvent, ControlState
from harnessed.sessions import SessionCheckpoint, SessionCommit

async def main():
    try:
        await JsonSessionStore(root=sys.argv[1]).save_session(
            "shared", SessionCommit(SessionCheckpoint(
                state=SessionState(context={"marker": sys.argv[2]}), offset=0,
                control=SessionControlChangedEvent(ControlState.ACTIVE),
            )), expected_revision=1
        )
    except SessionWriteConflict:
        print("conflict")
    else:
        print("saved")

asyncio.run(main())
"""
    first = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        "first",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    second = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        "second",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        outputs = await asyncio.gather(
            asyncio.wait_for(first.communicate(), timeout=30),
            asyncio.wait_for(second.communicate(), timeout=30),
        )
    finally:
        for process in (first, second):
            if process.returncode is None:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=5)
    assert first.returncode == second.returncode == 0, outputs
    assert sorted(output.decode().strip() for output, _ in outputs) == ["conflict", "saved"]
    stored = await store.load_session("shared")
    assert stored is not None and stored.revision == 2
    assert stored.checkpoint.state.context["marker"] in {"first", "second"}


async def test_process_kill_recovers_tool_intent_without_repeating_the_action(tmp_path):
    code = """
import asyncio
import sys
from harnessed import Agent, Plugin, tool
from harnessed.integrations.json import JsonSessionStore
from harnessed.testing import ScriptedModel, call

class Work(Plugin):
    @tool(readonly=True)
    async def work(self) -> str:
        print("started", flush=True)
        await asyncio.Event().wait()
        return "unreachable"

async def main():
    agent = Agent(
        model=ScriptedModel([call(Work.work)]),
        plugins=[Work()],
        store=JsonSessionStore(root=sys.argv[1]),
    )
    async with agent.session(id="interrupted") as session:
        await session.run("Work")

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert (await asyncio.wait_for(process.stdout.readline(), timeout=30)).strip() == b"started"
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)

    calls: list[str] = []

    class Work(Plugin):
        @tool(readonly=True)
        async def work(self) -> str:
            calls.append("repeated")
            return "unexpected"

    model = ScriptedModel([])
    agent = Agent(model=model, plugins=[Work()], store=JsonSessionStore(root=tmp_path))
    async with agent.session(id="interrupted") as restored:
        assert restored.log.open_turn is None
        assert any("external outcome is unknown" in message.text for message in restored.messages)
    assert not calls and not model.requests
