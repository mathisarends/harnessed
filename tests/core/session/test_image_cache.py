from dataclasses import replace

from harnessed.core.assets import _MemoryAssets
from harnessed.core.messages import ImageDetail, ImagePart, ImageReference, UserMessage
from harnessed.core.model import ModelRequest
from harnessed.core.session.images import _SessionImages
from tests._images import inline


class CountingAssets(_MemoryAssets):
    def __init__(self):
        super().__init__()
        self.reads = 0

    async def read(self, ref):
        self.reads += 1
        return await super().read(ref)


async def test_cache_is_byte_bounded_reuses_active_images_and_bypasses_large_images():
    store = CountingAssets()
    images = _SessionImages(store, cache_bytes=100)
    first = await images._retain_content((inline(b"a" * 30),))
    request = ModelRequest((UserMessage(first),))
    await images.resolve(request)
    await images.resolve(request)
    assert store.reads == 0  # Ingestion already populated the encoded cache.
    await images._retain_content((inline(b"b" * 30),))
    assert images._cache_size <= 100 and len(images._cache) == 1
    await images.resolve(request)
    assert store.reads == 1
    large = await images._retain_content((inline(b"c" * 100),))
    assert all(ref != large[0].asset for ref in images._cache)
    await images.resolve(ModelRequest((UserMessage(large),)))
    assert images._cache_size <= 100
    images.clear()
    assert images._cache_size == 0 and not images._cache


async def test_small_cache_does_not_reread_warm_tail_or_repeated_oversized_image():
    store = CountingAssets()
    images = _SessionImages(store, cache_bytes=100)
    first = await images._retain_content((inline(b"a" * 30),))
    second = await images._retain_content((inline(b"b" * 30),))
    await images.resolve(ModelRequest((UserMessage((*first, *second)),)))
    assert store.reads == 1  # Loading the first image must not lose the warm second.
    large = await images._retain_content((inline(b"c" * 100),))
    wire = await images.resolve(ModelRequest((UserMessage((*large, *large)),)))
    assert store.reads == 2
    assert wire.messages[0].content[0].url is wire.messages[0].content[1].url


async def test_tiny_images_also_have_a_bounded_cache_entry_count():
    images = _SessionImages(_MemoryAssets())
    for index in range(300):
        await images._retain_content((inline(index.to_bytes(2)),))
    assert len(images._cache) == 256


async def test_text_requests_preserve_identity_without_storage_access():
    store = CountingAssets()
    images = _SessionImages(store)
    request = ModelRequest((UserMessage("text"),))
    assert await images.retain_request(request) is request
    assert await images.resolve(request) is request
    assert store.reads == 0


async def test_external_urls_remain_external_and_reference_detail_does_not_duplicate_bytes(
    tmp_path,
):
    store = _MemoryAssets()
    images = _SessionImages(store)
    remote = (ImagePart("https://example.test/frame.png"),)
    assert await images._retain_content(remote) is remote
    ref = await store.put(b"pixels", media_type="image/png")
    first = ImageReference(ref, ImageDetail.LOW)
    request = ModelRequest((UserMessage((first, replace(first, detail=ImageDetail.HIGH))),))
    resolved = await images.resolve(request)
    assert [part.detail for part in resolved.messages[0].content] == [
        ImageDetail.LOW,
        ImageDetail.HIGH,
    ]
    assert len(store._values) == 1
