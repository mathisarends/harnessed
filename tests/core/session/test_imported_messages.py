import hashlib

import pytest

from harnessed import Agent, Compaction, Context
from harnessed.context.compaction import SummaryError
from harnessed.context.tokens import ApproximateTokenCounter
from harnessed.errors import ContextBudgetExceeded, ContextLengthExceeded, SessionFormatError
from harnessed.events import CompactionAppliedEvent
from harnessed.models import (
    AssetRef,
    AssistantMessage,
    ImageReference,
    ModelChunk,
    ModelInfo,
    ModelUsage,
    StopReason,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harnessed.sessions import AssetValue, SessionState
from harnessed.testing import CallbackModel, ExtractiveSummarizer, ScriptedModel
from tests._commits import CommitRecorder

_PIXELS = b"pixels"
_WINDOW = 512


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
    agent = Agent(model=ScriptedModel(["kept"]), context=Context(compaction=compaction))
    async with agent.session(state=state) as session:
        await session.run("Recent question.")
        applied = await session.compact()
        history = [entry.message for entry in session.state().history]
    assert applied is not None and applied.through == len(imported)
    assert not any(message in history for message in imported)
    assert history[-2].text == "Recent question."
    assert history[-1].text == "kept"


@pytest.mark.parametrize("reject_first_batch", [False, True])
async def test_long_import_is_compacted_in_batches_and_replays_from_the_journal(reject_first_batch):
    lookup = ToolCall("lookup", id="imported-lookup")
    text = "Old project material. " * 300
    imported = (
        UserMessage(text),
        AssistantMessage("", tool_calls=(lookup,)),
        ToolMessage("Result from the old session.", tool_call_id=lookup.id),
    )
    state = SessionState.from_messages(imported)
    store = CommitRecorder(documents=True)
    during_turn = []
    batch_count = 0
    rejected = False

    async def respond(request):
        nonlocal batch_count, rejected
        if request.messages[-1].text == "What remains?":
            during_turn.append(await store.read("imported"))
            yield ModelChunk(text="The remaining tasks.")
        else:
            if reject_first_batch and not rejected:
                rejected = True
                raise ContextLengthExceeded("Estimated block is too large for this provider")
            batch_count += 1
            yield ModelChunk(text=f"Handoff {batch_count}", usage=ModelUsage(8, 2))

    model = CallbackModel(respond, info=ModelInfo("bounded", context_window=_WINDOW))
    agent = Agent(
        model=model,
        instructions="Use the project facts.",
        context=Context(compaction=Compaction(max_summary_tokens=32)),
    )
    async with agent.session(id="imported", state=state, store=store) as session:
        result = await session.run("What remains?")

    assert rejected is reject_first_batch
    *batches, final = model.requests[int(reject_first_batch) :]
    assert len(batches) > 1
    counter = ApproximateTokenCounter()
    assert all(
        counter.count_request(batch) + batch.settings.max_tokens <= _WINDOW for batch in batches
    )
    transcript = "".join(
        batch.messages[-1]
        .text.split("NEXT HISTORY BLOCK\n", 1)[1]
        .split("\n\nRETAINED CONTEXT", 1)[0]
        for batch in batches
    )
    assert text.strip() in transcript
    assert "imported-lookup" in transcript and "Result from the old session." in transcript
    assert "PREVIOUS HANDOFF\nHandoff 1" in batches[1].messages[-1].text
    assert [message.text for message in final.messages] == [
        "Use the project facts.",
        f"Conversation summary:\nHandoff {len(batches)}",
        "What remains?",
    ]
    assert result.text == "The remaining tasks."
    assert result.usage.input_tokens == 8 * len(batches)

    [stored] = during_turn
    assert stored is not None
    recovery = stored.recovery()
    [applied] = [event for event in recovery.events if isinstance(event, CompactionAppliedEvent)]
    assert applied.through == len(imported)
    restored_store = CommitRecorder(documents=True)
    restored_store.put(recovery)
    async with agent.session(id="imported", store=restored_store) as restored:
        restored_texts = [message.text for message in restored.messages]
    assert restored_texts[-2:] == [f"Conversation summary:\n{applied.summary}", "What remains?"]


async def test_failed_batch_keeps_the_original_imported_history():
    imported = UserMessage("Old project material. " * 300)
    state = SessionState.from_messages([imported])
    failure = [ModelChunk(stop_reason=StopReason.CONTENT_FILTER)]
    model = ScriptedModel(
        ["First handoff", failure], info=ModelInfo("bounded", context_window=_WINDOW)
    )
    agent = Agent(model=model, context=Context(compaction=Compaction(max_summary_tokens=32)))
    async with agent.session(state=state) as session:
        with pytest.raises(SummaryError, match="did not complete"):
            await session.run("Continue.")
        assert imported in session.messages
        assert session.last(CompactionAppliedEvent) is None
    assert len(model.requests) == 2


async def test_oversized_current_input_still_fails_before_the_model():
    imported = UserMessage("Old project material. " * 300)
    state = SessionState.from_messages([imported])
    model = ScriptedModel([], info=ModelInfo("bounded", context_window=_WINDOW))
    agent = Agent(model=model, context=Context(compaction=Compaction(max_summary_tokens=32)))
    async with agent.session(state=state) as session:
        with pytest.raises(ContextBudgetExceeded):
            await session.run("Current project material. " * 300)
        assert imported in session.messages
    assert model.requests == []


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
