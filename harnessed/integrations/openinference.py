"""OpenInference message attributes for model spans, independent of the OTel SDK.

Messages first become portable JSON values in which every image is an opaque
identifier. Content filters and redaction see only these values, so images are
either exported whole or dropped whole. ``MessageAttributes`` then flattens the
filtered values into ``llm.input_messages.*`` / ``llm.output_messages.*`` keys and
attaches image data at most once per conversation.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from pydantic import JsonValue

from harnessed.models import ContentPart, ImagePart, ImageReference, Message, TextPart

type AttributeScalar = str | bool | int | float


class TraceImage(NamedTuple):
    """Image data available to one export; references the tracer cannot read have no URL."""

    url: str | None
    size_bytes: int


class LimitedText(NamedTuple):
    text: str
    truncated: bool


class MessageFingerprint(NamedTuple):
    """Cheap content identity for detecting the history shared with an earlier request.

    Message ids are excluded: the runtime stores model output under a new id.
    """

    role: str
    text: str
    tool_call_id: str | None
    tool_calls: tuple[str, ...]
    images: tuple[str, ...]


def limit_text(value: str, limit: int) -> LimitedText:
    """Keep the text, or its prefix with a marker recording the original length."""
    if len(value) <= limit:
        return LimitedText(value, truncated=False)
    return LimitedText(
        f"{value[:limit]}… [truncated, original length {len(value)}]", truncated=True
    )


def portable_message(message: Message, images: dict[str, TraceImage]) -> dict[str, Any]:
    """Copy the portable conversation fields, replacing each image with its identifier.

    Provider state and reasoning can include opaque or signed payloads and are
    never exported. Image data is collected in ``images`` by identifier.
    """
    value: dict[str, Any] = {"role": message.role.value}
    if isinstance(message.content, str):
        value["content"] = message.content
    else:
        value["content"] = [portable_part(part, images) for part in message.content]
    if message.tool_calls:
        value["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        value["tool_call_id"] = message.tool_call_id
    return value


def portable_part(part: ContentPart, images: dict[str, TraceImage]) -> dict[str, str]:
    """Copy text, or record an image under its content digest and return a stand-in."""
    match part:
        case TextPart():
            return {"type": "text", "text": part.text}
        case ImagePart():
            image_id, image = _url_image(part.url)
        case ImageReference():
            image_id = part.asset.sha256
            image = TraceImage(None, part.asset.size_bytes)
    images.setdefault(image_id, image)
    return {"type": "image", "image": image_id}


def _fingerprint(message: Message) -> MessageFingerprint:
    images: list[str] = []
    if not isinstance(message.content, str):
        for part in message.content:
            match part:
                case ImageReference():
                    images.append(part.asset.sha256)
                case ImagePart():
                    # Avoid hashing image bytes on every request; str hashes are
                    # cached and only compared within this process.
                    images.append(f"{len(part.url)}:{hash(part.url)}")
                case TextPart():
                    pass
    call_ids = tuple(call.id for call in message.tool_calls)
    return MessageFingerprint(
        message.role.value,
        message.text,
        message.tool_call_id,
        call_ids,
        tuple(images),
    )


class ConversationExport:
    """Export state shared by the model spans of one session.

    It remembers the messages already visible on earlier model spans, as inputs
    or outputs, and the span that first carried each distinct image.
    """

    def __init__(self) -> None:
        self._exported: list[MessageFingerprint] = []
        self._image_spans: dict[str, str] = {}

    def advance(self, messages: Sequence[Message], *, full: bool) -> int:
        """Record a request and return how many leading messages earlier spans showed."""
        current = [_fingerprint(message) for message in messages]
        inherited = 0
        if not full:
            for previous, message in zip(self._exported, current, strict=False):
                if previous != message:
                    break
                inherited += 1
        self._exported = current
        return inherited

    def seen(self, message: Message) -> None:
        """Mark a model output as visible, so the next request need not repeat it."""
        self._exported.append(_fingerprint(message))

    def first_span(self, image_id: str) -> str | None:
        return self._image_spans.get(image_id)

    def record_images(self, spans: Mapping[str, str]) -> None:
        self._image_spans.update(spans)


class MessageAttributes:
    """Flatten filtered portable messages into OpenInference message attributes.

    Text parts and tool arguments are limited individually with an explicit
    marker. Images are exported whole, once per conversation, or replaced by a
    text placeholder. Claimed images are recorded only by ``commit()``, after
    every attribute was built successfully.

    An attribute budget keeps the newest messages that fit and omits older ones
    whole, so a span never exports a partial message.
    """

    def __init__(
        self,
        *,
        text_limit: int,
        image_limit: int,
        images: Mapping[str, TraceImage],
        conversation: ConversationExport,
        span_id: str,
    ) -> None:
        self._text_limit = text_limit
        self._image_limit = image_limit
        self._images = images
        self._conversation = conversation
        self._span_id = span_id
        self._attributes: dict[str, AttributeScalar] = {}
        self._claimed: dict[str, str] = {}
        self._truncated = False
        self._omitted = 0

    @property
    def truncated(self) -> bool:
        return self._truncated or self._omitted > 0

    @property
    def omitted(self) -> int:
        """Older messages left out because they did not fit the attribute budget."""
        return self._omitted

    def messages(
        self, prefix: str, value: JsonValue, *, budget: int | None = None
    ) -> dict[str, AttributeScalar]:
        """Flatten messages, keeping the newest ones that fit within ``budget`` attributes."""
        if not isinstance(value, list):
            raise TypeError("Trace messages must be a list")
        kept = len(value) if budget is None else self._fitting(prefix, value, budget)
        self._omitted = len(value) - kept
        for index, message in enumerate(value[self._omitted :]):
            self._message(f"{prefix}.{index}.message", message)
        return dict(self._attributes)

    def commit(self) -> None:
        self._conversation.record_images(self._claimed)

    def text(self, value: str) -> str:
        limited = limit_text(value, self._text_limit)
        self._truncated = self._truncated or limited.truncated
        return limited.text

    def json(self, value: JsonValue) -> str:
        return self.text(json.dumps(value, ensure_ascii=False, allow_nan=False))

    def _fitting(self, prefix: str, messages: list[JsonValue], budget: int) -> int:
        """Count the newest messages whose attributes fit together within ``budget``.

        A scratch builder measures them without claiming images, because image
        placeholders and image URLs use the same number of attributes.
        """
        scratch = MessageAttributes(
            text_limit=self._text_limit,
            image_limit=self._image_limit,
            images=self._images,
            conversation=self._conversation,
            span_id=self._span_id,
        )
        kept = 0
        for message in reversed(messages):
            scratch._message(f"{prefix}.{kept}.message", message)
            if len(scratch._attributes) > budget:
                break
            kept += 1
        return kept

    def _message(self, prefix: str, message: JsonValue) -> None:
        if not isinstance(message, dict) or not isinstance(role := message.get("role"), str):
            raise TypeError("Trace messages must be objects with a role")
        self._attributes[f"{prefix}.role"] = role
        match message.get("content"):
            case str() as text:
                self._attributes[f"{prefix}.content"] = self.text(text)
            case list() as parts:
                for index, part in enumerate(parts):
                    self._part(f"{prefix}.contents.{index}.message_content", part)
            case None:
                pass
            case _:
                raise TypeError("Trace message content must be text or a part list")
        if isinstance(call_id := message.get("tool_call_id"), str):
            self._attributes[f"{prefix}.tool_call_id"] = call_id
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            raise TypeError("Trace tool calls must be a list")
        for index, call in enumerate(calls):
            self._tool_call(f"{prefix}.tool_calls.{index}.tool_call", call)

    def _part(self, prefix: str, part: JsonValue) -> None:
        if not isinstance(part, dict):
            raise TypeError("Trace content parts must be objects")
        match part.get("type"), part.get("text"), part.get("image"):
            case "text", str() as text, _:
                self._attributes[f"{prefix}.type"] = "text"
                self._attributes[f"{prefix}.text"] = self.text(text)
            case "image", _, str() as image_id:
                url, placeholder = self._image(image_id)
                if url is not None:
                    self._attributes[f"{prefix}.type"] = "image"
                    self._attributes[f"{prefix}.image.image.url"] = url
                else:
                    self._attributes[f"{prefix}.type"] = "text"
                    self._attributes[f"{prefix}.text"] = placeholder
            case _:
                raise TypeError("Trace content parts must be text or image stand-ins")

    def _image(self, image_id: str) -> tuple[str | None, str]:
        image = self._images.get(image_id)
        if image is None or image.url is None:
            return None, f"[image {image_id}, data unavailable to the tracer]"
        if image.size_bytes > self._image_limit:
            return None, (
                f"[image {image_id}, {image.size_bytes} bytes exceeds the "
                f"{self._image_limit}-byte trace image limit]"
            )
        first = self._conversation.first_span(image_id) or self._claimed.get(image_id)
        if first is not None:
            return None, f"[image {image_id}, first sent in span {first}]"
        self._claimed[image_id] = self._span_id
        return image.url, ""

    def _tool_call(self, prefix: str, call: JsonValue) -> None:
        if not isinstance(call, dict):
            raise TypeError("Trace tool calls must be objects")
        if isinstance(call_id := call.get("id"), str):
            self._attributes[f"{prefix}.id"] = call_id
        if isinstance(name := call.get("name"), str):
            self._attributes[f"{prefix}.function.name"] = name
        if "arguments" in call:
            self._attributes[f"{prefix}.function.arguments"] = self.json(call["arguments"])


def _url_image(url: str) -> tuple[str, TraceImage]:
    """Identify inline data by its decoded digest, matching the session asset identity."""
    header, separator, encoded = url.partition(",")
    if url.startswith("data:") and separator and header.endswith(";base64"):
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            pass
        else:
            return hashlib.sha256(data).hexdigest(), TraceImage(url, len(data))
    encoded_url = url.encode()
    return hashlib.sha256(encoded_url).hexdigest(), TraceImage(url, len(encoded_url))
