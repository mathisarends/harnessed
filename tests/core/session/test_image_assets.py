from dataclasses import replace

import pytest

from harnessed import Agent, Observation, Plugin
from harnessed.core.assets import AssetRef
from harnessed.core.messages import ImagePart, ImageReference, TextPart, UserMessage
from harnessed.core.model import ModelRequest
from harnessed.core.session.events import (
    ModelRequestedEvent,
    ObservationRecordedEvent,
    ObservationsIncludedEvent,
)
from harnessed.hooks import Next, PrepareRequest
from harnessed.kernel import hook
from harnessed.plugins import Compaction
from harnessed.testing import ExtractiveSummarizer, ScriptedModel
from tests._images import inline


async def test_public_asset_access_preserves_storage_ownership_and_recorded_requests(tmp_path):
    async with Agent(model=ScriptedModel(["done"]), plugins=[]).session() as session:
        backend = session.assets
        assert session.log.assets is backend
        ref = await backend.put(b"pixels", media_type="image/png")
        ref.verify(await backend.read(ref))
        with pytest.raises(ValueError):
            ref.verify(b"wrong pixels")
        with pytest.raises(AttributeError):
            session.assets = backend
        with pytest.raises(AttributeError):
            session.log.assets = backend
        assert session.assets is session.log.assets is backend
        await session.run(UserMessage((ImageReference(ref),)))
        recorded = session.last(ModelRequestedEvent).request
        wire = await session.log.resolve_request(recorded)
        assert wire.messages[0].content == (inline(b"pixels"),)
        assert recorded.messages[0].content == (ImageReference(ref),)
        assert session.last(ModelRequestedEvent).request is recorded


async def test_compaction_does_not_load_images_removed_from_the_request(tmp_path):
    model = ScriptedModel(["first", "after compaction"])
    app = Agent(
        model=model,
        plugins=[
            Compaction(
                trigger=lambda _: False,
                keep_turns=0,
                summary_tokens=8,
                summarizer=ExtractiveSummarizer(),
            )
        ],
    )
    async with app.session() as session:
        session.observe(Observation("screen", (TextPart("state " * 500), inline(b"old"))))
        await session.run("inspect")
        old = session.last(ObservationRecordedEvent).observation.content[1].asset
        # Cold-cache compaction must not load discarded images.
        session.log._images.clear()
        # Neither the text-only summarizer nor the following request needs this file.
        assert await session.compact() is not None
        with pytest.raises(FileNotFoundError):
            await session.assets.read(old)
        await session.run("continue")
        assert not session.last(ObservationsIncludedEvent).observations
        assert all(
            not isinstance(part, ImagePart)
            for message in model.requests[-1].messages
            if not isinstance(message.content, str)
            for part in message.content
        )


async def test_request_hook_inline_images_are_stored_before_recording(tmp_path):
    class Attach(Plugin):
        @hook(PrepareRequest)
        async def prepare(self, event: PrepareRequest, next_: Next[ModelRequest]) -> ModelRequest:
            request = await next_()
            return replace(request, messages=(*request.messages, UserMessage((inline(b"hook"),))))

    model = ScriptedModel(["done"])
    async with Agent(model=model, plugins=[Attach()]).session() as session:
        await session.run("inspect")
        stored = session.last(ModelRequestedEvent).request
        assert isinstance(stored.messages[-1].content[0], ImageReference)
        assert model.requests[-1].messages[-1].content == (inline(b"hook"),)
        assert not session.snapshot().assets  # Hook-owned images are supplied by the hook again.


@pytest.mark.parametrize("sha", ["../escape", "A" * 64, "x" * 64, "a" * 63])
def test_asset_references_reject_invalid_digest_paths(sha):
    with pytest.raises(ValueError):
        AssetRef(sha, "image/png", 1)
