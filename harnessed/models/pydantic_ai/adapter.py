from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from harnessed.core.errors import ConfigError
from harnessed.core.messages import to_dict
from harnessed.core.model import (
    GenerationSettings,
    ModelChunk,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelStream,
    ModelUsage,
    ToolArgumentsMode,
    ToolCallDelta,
    ToolCallStart,
    resolve_request_settings,
)

from .conversion import history, model_response, model_usage, provider_error, request_parameters

if TYPE_CHECKING:
    from pydantic_ai.messages import PartDeltaEvent, PartStartEvent
    from pydantic_ai.models import Model as NativeModel
    from pydantic_ai.models import ModelRequestParameters, StreamedResponse
    from pydantic_ai.native_tools import AbstractNativeTool
    from pydantic_ai.settings import ModelSettings as NativeSettings


class PydanticAIModel:
    """Translate the Direct API without implementing providers, HTTP or retries.

    Strings are resolved by PydanticAI. Both resolved and supplied native models
    enter their resource context for sessions and standalone requests. PydanticAI
    manages nested scopes and preserves caller-supplied clients. Provider response
    state is persisted with assistant messages so signatures and reasoning survive
    tool rounds/replay.

    ``native_tools`` forwards PydanticAI's provider-executed tools, such as
    ``WebSearchTool`` or ``CodeExecutionTool``, with every request. The provider
    runs them inside one response; their calls and results stay in the provider
    state for replay and never become harnessed tool calls.
    """

    def __init__(
        self,
        model: str | NativeModel,
        *,
        settings: GenerationSettings | None = None,
        context_window: int | None = None,
        native_tools: Sequence[AbstractNativeTool] = (),
    ) -> None:
        self._native = _resolve_model(model)
        _validate_context_window(context_window)
        self.native_tools = _native_tools(native_tools)
        self._generation_settings = _generation_settings(self._native, settings)
        self._info = _model_info(self._native, context_window=context_window)

    async def __aenter__(self) -> Self:
        await self._native.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        return await self._native.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def info(self) -> ModelInfo:
        return self._info

    @property
    def settings(self) -> GenerationSettings:
        return self._generation_settings

    async def request(self, request: ModelRequest) -> ModelResponse:
        from pydantic_ai.direct import model_request

        try:
            async with self:
                native = await model_request(
                    self._native,
                    history(request, self.info),
                    model_settings=self._settings(request),
                    model_request_parameters=self._parameters(request),
                    instrument=False,
                )
                return model_response(native, self.info)
        except Exception as error:
            mapped = provider_error(error)
            if mapped is not None:
                raise mapped from error
            raise

    def _settings(self, request: ModelRequest) -> NativeSettings:
        effective = resolve_request_settings(request, self.settings).settings
        settings = to_dict(effective.provider_options)
        if effective.max_tokens is not None:
            settings["max_tokens"] = effective.max_tokens
        if effective.temperature is not None:
            settings["temperature"] = effective.temperature
        return settings

    def _parameters(self, request: ModelRequest) -> ModelRequestParameters:
        return request_parameters(
            request,
            native_output=self.info.supports_structured_output,
            native_tools=self.native_tools,
        )

    @asynccontextmanager
    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelStream, None]:
        from pydantic_ai.direct import model_request_stream

        try:
            async with (
                self,
                model_request_stream(
                    self._native,
                    history(request, self.info),
                    model_settings=self._settings(request),
                    model_request_parameters=self._parameters(request),
                    instrument=False,
                ) as native,
            ):
                yield _Stream(native, self.info)
        except Exception as error:
            mapped = provider_error(error)
            if mapped is not None:
                raise mapped from error
            raise


def _resolve_model(model: str | NativeModel) -> NativeModel:
    try:
        from pydantic_ai.models import infer_model
    except ImportError as exc:
        raise ConfigError(
            f"Install the provider extra: uv add 'harnessed[{_extra(model)}]'"
        ) from exc
    try:
        return infer_model(model)
    except ImportError as exc:
        raise ConfigError(
            f"Install the provider extra: uv add 'harnessed[{_extra(model)}]'"
        ) from exc
    except Exception as exc:
        # Native provider construction can fail in arbitrary SDK/application code.
        raise ConfigError(f"Unable to configure model: {exc}") from exc


def _validate_context_window(context_window: int | None) -> None:
    if context_window is not None and (type(context_window) is not int or context_window < 1):
        raise ConfigError("context_window must be a positive integer")


def _native_tools(tools: Sequence[AbstractNativeTool]) -> tuple[AbstractNativeTool, ...]:
    from pydantic_ai.native_tools import AbstractNativeTool

    if isinstance(tools, AbstractNativeTool) or not all(
        isinstance(item, AbstractNativeTool) for item in tools
    ):
        raise ConfigError("native_tools must be a sequence of PydanticAI native tools")
    return tuple(tools)


def _generation_settings(
    model: NativeModel, settings: GenerationSettings | None
) -> GenerationSettings:
    defaults = model.settings
    if defaults is None:
        defaults = {}
    native_settings = dict(defaults)
    provider_options = {
        key: value
        for key, value in native_settings.items()
        if key not in ("max_tokens", "temperature")
    }
    return GenerationSettings(
        max_tokens=defaults.get("max_tokens"),
        temperature=defaults.get("temperature"),
        provider_options=provider_options,
    ).override(settings)


def _model_info(model: NativeModel, *, context_window: int | None) -> ModelInfo:
    profile = model.profile
    return ModelInfo(
        name=model.model_name,
        provider=model.system,
        context_window=(
            context_window if context_window is not None else profile.get("context_window")
        ),
        supports_images=bool(profile.get("supports_image_input")),
        supports_structured_output=bool(profile.get("supports_json_schema_output")),
    )


def _extra(model: object) -> str:
    provider = model.split(":", 1)[0] if isinstance(model, str) else "openai"
    if provider == "anthropic":
        return "anthropic"
    if provider in ("google", "google-gla", "google-vertex"):
        return "google"
    return "openai"


@dataclass
class _ToolPreview:
    name: str
    call_id: str | None
    arguments: dict[str, Any] | None = None


class _Stream:
    def __init__(self, stream: StreamedResponse, info: ModelInfo) -> None:
        self._native = stream
        self._info = info
        self._response: ModelResponse | None = None
        self._iterator = self._chunks()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> ModelChunk:
        return await anext(self._iterator)

    @property
    def usage(self) -> ModelUsage | None:
        usage = self._native.usage
        return model_usage(usage) if usage.has_values() else None

    def response(self) -> ModelResponse:
        if self._response is None:
            raise RuntimeError("The model stream must finish before reading its response")
        return self._response

    async def _chunks(self) -> AsyncIterator[ModelChunk]:
        from pydantic_ai import messages as native

        previews: dict[int, _ToolPreview] = {}
        async for event in self._native:
            if isinstance(event, native.PartStartEvent):
                for chunk in _start_chunks(event, previews):
                    yield chunk
            elif isinstance(event, native.PartDeltaEvent):
                for chunk in _delta_chunks(event, previews):
                    yield chunk
        self._response = response = model_response(self._native.get(), self._info)
        for call in response.message.tool_calls:
            yield ModelChunk(tool_call=call)
        yield ModelChunk(usage=response.usage, stop_reason=response.stop_reason)


def _start_chunks(event: PartStartEvent, previews: dict[int, _ToolPreview]) -> Iterator[ModelChunk]:
    from pydantic_ai import messages as native

    # A start replaces whatever part previously used this index.
    previews.pop(event.index, None)
    part = event.part
    if isinstance(part, native.TextPart):
        yield ModelChunk(text=part.content)
    elif isinstance(part, native.ThinkingPart):
        yield ModelChunk(reasoning=part.content)
    elif isinstance(part, native.ToolCallPart):
        preview = _ToolPreview(part.tool_name, part.tool_call_id)
        previews[event.index] = preview
        yield ModelChunk(tool_call_start=ToolCallStart(event.index, preview.name, preview.call_id))
        if part.args is not None:
            if isinstance(part.args, dict):
                preview.arguments = dict(part.args)
                arguments = json.dumps(preview.arguments, ensure_ascii=False)
                mode = ToolArgumentsMode.REPLACE
            else:
                arguments = part.args
                mode = ToolArgumentsMode.APPEND
            yield ModelChunk(
                tool_call_delta=ToolCallDelta(
                    event.index, arguments, preview.name, preview.call_id, mode
                )
            )


def _delta_chunks(event: PartDeltaEvent, previews: dict[int, _ToolPreview]) -> Iterator[ModelChunk]:
    from pydantic_ai import messages as native

    delta = event.delta
    if isinstance(delta, native.TextPartDelta):
        yield ModelChunk(text=delta.content_delta)
    elif isinstance(delta, native.ThinkingPartDelta) and delta.content_delta:
        yield ModelChunk(reasoning=delta.content_delta)
    elif isinstance(delta, native.ToolCallPartDelta):
        # Provider-executed native calls share this delta type but are
        # not previewed; the final response decides whether they are valid.
        preview = previews.get(event.index)
        if preview is None:
            return
        preview.name += delta.tool_name_delta or ""
        if delta.tool_call_id is not None:
            preview.call_id = delta.tool_call_id
        if isinstance(delta.args_delta, dict):
            # Each patch re-sends the full snapshot: payload grows with the
            # number of patches. Fine while providers send few of them.
            preview.arguments = {**(preview.arguments or {}), **delta.args_delta}
            arguments = json.dumps(preview.arguments, ensure_ascii=False)
            mode = ToolArgumentsMode.REPLACE
        else:
            arguments = delta.args_delta or ""
            mode = ToolArgumentsMode.APPEND
        yield ModelChunk(
            tool_call_delta=ToolCallDelta(
                event.index, arguments, preview.name, preview.call_id, mode
            )
        )
