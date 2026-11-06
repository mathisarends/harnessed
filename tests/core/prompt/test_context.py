import json
from pathlib import Path

import pytest

from harnessed import Agent as Application
from harnessed import (
    File,
    Image,
    Inject,
    Observation,
    Plugin,
    Session,
    SessionState,
    ToolCall,
    tool,
)
from harnessed.core.events.messages import ContextMessageEvent
from harnessed.core.events.model import ModelRequestedEvent
from harnessed.core.events.observations import ObservationRecordedEvent
from harnessed.core.hooks import PreStep
from harnessed.core.messages import ImageDetail, ImagePart, Role, TextPart, UserMessage, user
from harnessed.core.model import ModelChunk
from harnessed.core.prompt.context import DynamicContext
from harnessed.core.session.codec import decode_event, encode_event
from harnessed.errors import SessionClosed
from harnessed.kernel import hook
from harnessed.plugins import Approvals, Service
from harnessed.plugins.filesystem import FileSystemBackend, LocalFileSystem
from harnessed.testing import ScriptedModel
from tests._audit import Audit, AuditProjection


async def test_task_context_lasts_one_turn_and_precedes_task_prompt():

    class ToolsPlugin(Plugin):
        @tool
        async def step(self) -> str:
            return "done"

    tools = ToolsPlugin(name="continue")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("step"))], "first", "second"])
    async with Application(model=model, plugins=[Approvals.allow_all(), tools]).session() as agent:
        agent_audit = Audit(agent)
        await agent.run("review", context={"ticket": "Fix login", "details": {"issue": 42}})
        for request in model.requests[:2]:
            assert "Task context [ticket]:\nFix login" in [m.text for m in request.messages]
            assert request.messages[0].role == Role.SYSTEM
            assert request.messages[2].text == "review"
        await agent.run("unrelated")
        assert all("Task context" not in m.text for m in model.requests[-1].messages)
        assert any(isinstance(e, ContextMessageEvent) for e in agent_audit.events)
        assert "Fix login" in json.dumps([encode_event(e) for e in agent_audit.events])


async def test_file_and_image_attachments_are_snapshotted_in_log_and_wire_payload(tmp_path):
    diff = tmp_path / "PR.diff"
    diff.write_text("+ fixed login", encoding="utf-8")
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"image-test-bytes")
    model = ScriptedModel(["reviewed"])
    async with Application(model=model, plugins=[Approvals.allow_all()]).session() as agent:
        agent_audit = Audit(agent)
        await agent.run("Review", attachments=[File(diff), Image(image)])
        request = model.requests[0]
        message = request.messages[0]
        assert "File: PR.diff\n+ fixed login" in message.text
        assert any(isinstance(part, ImagePart) for part in message.content)
        image_part = next(part for part in message.content if isinstance(part, ImagePart))
        assert image_part.url.startswith("data:image/png;")
        diff.write_text("changed after admission", encoding="utf-8")
        restored = tuple(
            decode_event(json.loads(json.dumps(encode_event(e)))) for e in agent_audit.events
        )
        recorded = next(e.request for e in restored if isinstance(e, ModelRequestedEvent))
        assert await agent.log.resolve_request(recorded) == request
        assert "changed after admission" not in recorded.messages[0].text


async def test_steering_carries_attachments_like_a_run(tmp_path):
    log = tmp_path / "failure.log"
    log.write_text("AssertionError: login", encoding="utf-8")
    model = ScriptedModel(["looked"])
    async with Application(model=model).session() as agent:
        await agent.steer("Check this failure", attachments=[File(log)])
        await agent.control.join()
        message = model.requests[0].messages[0]
        assert "Check this failure" in message.text
        assert "File: failure.log\nAssertionError: login" in message.text


async def test_added_context_is_additive_quiet_and_retained_across_turns():
    model = ScriptedModel(["first", "second"])
    async with Application(model=model, plugins=[Approvals.allow_all()]).session() as agent:
        await agent.add_context("External change")
        await agent.add_context("External change")
        await agent.control.join()
        assert model.requests == []
        assert not agent.inbox.pending
        assert agent.snapshot().turns == ()
        assert not hasattr(agent, "context")
        await agent.steer(user("Check auth"))
        await agent.control.join()
        assert [m.text for m in model.requests[0].messages] == [
            "External change",
            "External change",
            "Check auth",
        ]
        assert all(message.role == Role.USER for message in model.requests[0].messages)
        await agent.add_context("Updated project facts")
        await agent.run("another turn")
        texts = [message.text for message in model.requests[-1].messages]
        assert texts.count("External change") == 2
        assert texts[-2:] == ["Updated project facts", "another turn"]


@pytest.mark.parametrize("content", [None, 42, {"constraints": "Include URLs."}])
async def test_added_context_rejects_non_text_without_changing_history(content):
    model = ScriptedModel([])
    async with Application(model=model).session() as session:
        with pytest.raises(TypeError, match="Scope content must be a string"):
            await session.add_context(content)
        assert not session.messages and not model.requests


async def test_added_context_rejects_closed_sessions():
    model = ScriptedModel([])
    async with Application(model=model).session() as session:
        pass
    with pytest.raises(SessionClosed):
        await session.add_context("Too late")
    assert not session.messages and not model.requests


async def test_added_context_roundtrips_through_an_explicit_snapshot():
    model = ScriptedModel(["Remembered", "Continued"])
    app = Application(model=model)
    async with app.session() as session:
        await session.add_context("Include source URLs.")
        saved = SessionState.from_json(session.snapshot().to_json())
    async with app.session(state=saved) as restored:
        await restored.add_context("Use a comparison table.")
        await restored.run("Compare the missions.")
    assert [message.text for message in model.requests[0].messages] == [
        "Include source URLs.",
        "Use a comparison table.",
        "Compare the missions.",
    ]
    assert saved.history[0].message.text == "Include source URLs."


async def test_push_context_from_pre_step_is_in_the_current_request():
    seen = []

    class Recall(Plugin, needs=(Session,)):
        @hook(PreStep)
        async def recall(self, event, next_, agent: Inject[Session]):
            seen.append((event.is_turn_start, event.latest_user_text))
            if event.is_turn_start:
                await agent.add_context("Relevant memory: prefers Python")
            return await next_()

    model = ScriptedModel(["done"])
    async with Application(
        model=model, plugins=[Approvals.allow_all(), Recall()]
    ).session() as agent:
        await agent.run("my task")
        assert seen == [(True, "my task")]
        assert len(model.requests) == 1
        assert any("prefers Python" in m.text for m in model.requests[0].messages)
        assert not agent.inbox.pending


async def test_explicit_class_needs_and_injected_dynamic_project_context(tmp_path):
    instructions = tmp_path / "AGENTS.md"
    instructions.write_text("first rules", encoding="utf-8")

    class Project(Plugin, needs=(DynamicContext, FileSystemBackend)):
        @hook(PreStep)
        async def refresh(
            self, event, next_, context: Inject[DynamicContext], fs: Inject[FileSystemBackend]
        ):
            await context.set("project", await fs.read(Path("AGENTS.md")))
            return await next_()

    model = ScriptedModel(["one", "two"])
    async with Application(
        model=model,
        plugins=[
            Approvals.allow_all(),
            Service(FileSystemBackend, LocalFileSystem(tmp_path)),
            Project(),
        ],
    ).session() as agent:
        await agent.run("one")
        instructions.write_text("second rules", encoding="utf-8")
        await agent.run("two")
        assert any("first rules" in message.text for message in model.requests[0].messages)
        assert any("second rules" in message.text for message in model.requests[1].messages)


async def test_observations_remain_unchanged_in_later_steps():
    states = iter(["before", "after"])

    class BrowserObserver(Plugin, needs=(Session,)):
        @hook(PreStep)
        async def observe(self, event, next_, agent: Inject[Session]):
            state = next(states)
            agent.observe(Observation("browser", f"FULL {state} refs"))
            return await next_()

    class ToolsPlugin(Plugin):
        @tool
        async def click(self, ref: str) -> str:
            return f"Clicked {ref}"

    tools = ToolsPlugin(name="browser")

    model = ScriptedModel([[ModelChunk(tool_call=ToolCall("click", {"ref": "button-1"}))], "done"])
    async with Application(
        model=model, plugins=[Approvals.allow_all(), tools, BrowserObserver()]
    ).session() as agent:
        agent_audit = Audit(agent)
        await agent.run("Open settings")
        assert model.requests[0].messages[-1].text == "FULL before refs"
        texts = [m.text for m in model.requests[1].messages]
        assert "FULL before refs" in texts
        assert texts[-1] == "FULL after refs"
        assert any(isinstance(e, ObservationRecordedEvent) for e in agent_audit.events)
        replay = AuditProjection(agent_audit.events)
        assert replay.messages == agent.log.messages
        assert [m.text for m in replay.messages if m.text.startswith("FULL")] == [
            "FULL before refs",
            "FULL after refs",
        ]


async def test_remote_images_are_references_without_download():
    model = ScriptedModel(["done"])
    async with Application(model=model).session() as agent:
        await agent.run(
            "Describe this image",
            attachments=[Image("https://example.com/screenshot.png", detail=ImageDetail.LOW)],
        )
    message = model.requests[0].messages[-1]
    assert isinstance(message, UserMessage)
    assert message.content == (
        TextPart("Describe this image"),
        ImagePart("https://example.com/screenshot.png", ImageDetail.LOW),
    )
