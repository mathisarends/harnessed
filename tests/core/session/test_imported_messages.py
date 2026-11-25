import hashlib

import pytest

from harnessed import Agent, Compaction, Context
from harnessed.errors import SessionFormatError
from harnessed.models import (
    AssetRef,
    AssistantMessage,
    ImageReference,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harnessed.sessions import AssetValue, SessionState
from harnessed.testing import ExtractiveSummarizer, ScriptedModel

_PIXELS = b"pixels"


def _image() -> AssetValue:
    ref = AssetRef(hashlib.sha256(_PIXELS).hexdigest(), "image/png", len(_PIXELS))
    return AssetValue(ref, _PIXELS)


async def test_imported_messages_continue_as_the_model_visible_history():
    lookup = ToolCall("lookup", {"name": "Ada"})
    imported = [
        UserMessage("Who wrote the first program?"),
        AssistantMessage("", tool_calls=(lookup,)),
        ToolMessage("Ada Lovelace", tool_call_id=lookup.id),
        AssistantMessage("Ada Lovelace."),
    ]
    state = SessionState.from_messages(iter(imported))
    model = ScriptedModel(["In 1843."])
    async with Agent(model=model).session(state=state) as session:
        result = await session.run("When?")
    sent = [message for message in model.requests[0].messages if message.id != "system"]
    assert sent[:4] == imported
    assert sent[4].text == "When?"
    assert [entry.message for entry in result.state.history[:4]] == imported
    assert [entry.turn_id for entry in state.history] == [None] * 4
    assert SessionState.from_json(state.to_json()) == state


async def test_imported_images_are_restored_from_their_assets():
    image = _image()
    message = UserMessage((ImageReference(image.ref),))
    state = SessionState.from_messages([message], assets=[image, image])
    assert state.assets == (image,)
    async with Agent(model=ScriptedModel(["A picture."])).session(state=state) as session:
        assert await session.assets.read(image.ref) == _PIXELS


async def test_imported_messages_are_earlier_context_for_compaction():
    compaction = Compaction(
        trigger=lambda _: False,
        keep_last_steps=1,
        summarizer=ExtractiveSummarizer(),
        max_summary_tokens=8,
    )
    imported = [UserMessage("old context " * 200), AssistantMessage("Noted.")]
    state = SessionState.from_messages(imported)
    agent = Agent(model=ScriptedModel(["first", "kept"]), context=Context(compaction=compaction))
    async with agent.session(state=state) as session:
        await session.run("First question.")
        # Imported messages are no step of their own, so one recorded step is kept as is.
        assert await session.compact() is None
        await session.run("Recent question.")
        assert await session.compact() is not None
        history = [entry.message for entry in session.snapshot().history]
    assert not any(message in history for message in imported)
    assert history[-1].text == "kept"


@pytest.mark.parametrize(
    ("messages", "reason"),
    [
        ([SystemMessage("Be brief.")], "system instructions"),
        (
            [UserMessage("Look it up."), AssistantMessage("", tool_calls=(ToolCall("lookup"),))],
            "unanswered tool calls",
        ),
        ([ToolMessage("orphan", tool_call_id="missing")], "retained tool call"),
        ([UserMessage("Hi", id="system")], "reserved"),
        ([UserMessage("Hi"), "not a message"], "Invalid conversation history"),
    ],
)
def test_invalid_histories_name_the_violated_invariant(messages, reason):
    with pytest.raises(SessionFormatError, match=reason):
        SessionState.from_messages(messages)


def test_image_assets_must_match_the_referenced_images():
    image = _image()
    with pytest.raises(SessionFormatError, match="Snapshot images must exactly match"):
        SessionState.from_messages([UserMessage((ImageReference(image.ref),))])
    with pytest.raises(SessionFormatError, match="Snapshot images must exactly match"):
        SessionState.from_messages([UserMessage("No image.")], assets=[image])
