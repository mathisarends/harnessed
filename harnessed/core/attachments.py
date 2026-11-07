import asyncio
import base64
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from harnessed.core.assets import AssetStore
from harnessed.core.messages import (
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

    async def _parts(self) -> tuple[ContentPart, ...]:
        path = Path(self.path)
        text = await asyncio.to_thread(path.read_text, encoding=self.encoding)
        return (TextPart(f"File: {path.name}\n{text}"),)


@dataclass(frozen=True)
class Image:
    path: str | Path
    detail: ImageDetail = ImageDetail.AUTO

    async def _parts(self, *, assets: AssetStore | None = None) -> tuple[ContentPart, ...]:
        location = str(self.path)
        if location.startswith(("https://", "http://", "data:image/")):
            return (ImagePart(location, self.detail),)
        path = Path(self.path)
        mime, _ = mimetypes.guess_type(path.name)
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            raise ValueError("Images must be PNG, JPEG, GIF or WebP")
        data = await asyncio.to_thread(path.read_bytes)
        if assets is not None:
            return (ImageReference(await assets.put(data, media_type=mime), self.detail),)
        encoded = base64.b64encode(data).decode("ascii")
        return (ImagePart(f"data:{mime};base64,{encoded}", self.detail),)


type Attachment = File | Image


async def _prepare_input(
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
            parts.extend(
                await attachment._parts(assets=assets)
                if isinstance(attachment, Image)
                else await attachment._parts()
            )
        message = UserMessage(tuple(parts), id=message.id)
    return message
