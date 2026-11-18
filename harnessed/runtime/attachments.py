import asyncio
import base64
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from harnessed.models.assets import AssetStore
from harnessed.models.messages import (
    ContentPart,
    ImageDetail,
    ImagePart,
    ImageReference,
    Role,
    TextPart,
    UserMessage,
    user,
)


@dataclass(frozen=True)
class File:
    path: str | Path
    encoding: str = "utf-8"


@dataclass(frozen=True)
class Image:
    path: str | Path
    detail: ImageDetail = ImageDetail.AUTO


type Attachment = File | Image


async def _file_parts(file: File) -> tuple[ContentPart, ...]:
    path = Path(file.path)
    text = await asyncio.to_thread(path.read_text, encoding=file.encoding)
    return (TextPart(f"File: {path.name}\n{text}"),)


async def _image_parts(image: Image, assets: AssetStore | None) -> tuple[ContentPart, ...]:
    location = str(image.path)
    if location.startswith(("https://", "http://", "data:image/")):
        return (ImagePart(location, image.detail),)
    path = Path(image.path)
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        raise ValueError("Images must be PNG, JPEG, GIF or WebP")
    data = await asyncio.to_thread(path.read_bytes)
    if assets is not None:
        asset = await assets.put(data, media_type=mime)
        return (ImageReference(asset, image.detail),)
    encoded = base64.b64encode(data).decode("ascii")
    return (ImagePart(f"data:{mime};base64,{encoded}", image.detail),)


async def prepare_input(
    content: str | UserMessage,
    *,
    attachments: Iterable[Attachment] = (),
    assets: AssetStore | None = None,
) -> UserMessage:
    message = user(content) if isinstance(content, str) else content
    if not isinstance(message, UserMessage):
        raise TypeError("Run input must be text or a UserMessage")
    attachments = tuple(attachments)
    if attachments:
        if message.role != Role.USER:
            raise ValueError("Attachments belong to user messages")
        parts = (
            [TextPart(message.content)]
            if isinstance(message.content, str)
            else list(message.content)
        )
        for attachment in attachments:
            if isinstance(attachment, Image):
                added = await _image_parts(attachment, assets)
            else:
                added = await _file_parts(attachment)
            parts.extend(added)
        message = UserMessage(tuple(parts), id=message.id)
    return message
