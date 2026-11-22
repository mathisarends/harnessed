from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from harnessed.errors import (
    AuthError,
    ContextLengthExceeded,
    InvalidRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from harnessed.models.messages import (
    AssistantMessage,
    ContentPart,
    ImageReference,
    MalformedArguments,
    ProviderState,
    Role,
    TextPart,
    ToolCall,
    ToolMessage,
    ToolStatus,
)
from harnessed.models.model import (
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OutputMode,
    StopReason,
)
from harnessed.models.values import to_dict

if TYPE_CHECKING:
    from pydantic_ai import ImageUrl as NativeImageUrl
    from pydantic_ai import ModelMessage as NativeMessage
    from pydantic_ai import ModelRequest as NativeRequest
    from pydantic_ai import ModelResponse as NativeResponse
    from pydantic_ai import RequestUsage as NativeUsage
    from pydantic_ai import ToolCallPart as NativeToolCall
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.native_tools import AbstractNativeTool


def history(request: ModelRequest, info: ModelInfo) -> list[NativeMessage]:
    from pydantic_ai import messages as native

    history: list[NativeMessage] = []
    calls: dict[str, str] = {}
    for message in request.messages:
        for call in message.tool_calls:
            calls[call.id] = call.name
    for message in request.messages:
        if message.role == Role.ASSISTANT:
            state = message.provider_state
            if state is not None:
                history.append(_restored_response(message, state, info))
            else:
                history.append(_unsigned_response(message))
        elif message.role == Role.SYSTEM:
            history.append(native.ModelRequest([native.SystemPromptPart(message.text)]))
        elif message.role == Role.TOOL:
            history.append(_tool_return(message, calls))
        else:
            content = _content(message.content)
            history.append(native.ModelRequest([native.UserPromptPart(content)]))
    return history


def request_parameters(
    request: ModelRequest,
    *,
    native_output: bool = False,
    native_tools: Sequence[AbstractNativeTool] = (),
) -> ModelRequestParameters:
    from pydantic_ai import ToolDefinition
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.output import OutputObjectDefinition

    schema = request.output_schema
    if schema is None:
        mode = "text"
    elif schema.mode == OutputMode.AUTO:
        mode = OutputMode.NATIVE if native_output else OutputMode.TOOL
    else:
        mode = schema.mode
    function_tools = [
        ToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters_json_schema=to_dict(tool.parameters),
        )
        for tool in request.tools
        if schema is None or tool.name != "final_answer"
    ]
    parameters = ModelRequestParameters(
        function_tools=function_tools,
        native_tools=list(native_tools),
    )
    if schema is not None:
        match mode:
            case OutputMode.NATIVE:
                parameters.output_mode = "native"
            case OutputMode.TOOL:
                parameters.output_mode = "tool"
            case _:
                parameters.output_mode = "text"
        if mode == OutputMode.NATIVE:
            parameters.output_object = OutputObjectDefinition(
                json_schema=to_dict(schema.json_schema), name=schema.name
            )
        else:
            parameters.output_tools = [
                ToolDefinition(
                    name="final_answer",
                    parameters_json_schema=to_dict(schema.json_schema),
                    kind="output",
                )
            ]
            parameters.allow_text_output = False
    return parameters


def model_response(response: NativeResponse, info: ModelInfo) -> ModelResponse:
    from pydantic_ai import messages as native

    text, thinking, calls = [], [], []
    for part in response.parts:
        match part:
            case native.TextPart():
                text.append(part.content)
            case native.ThinkingPart():
                thinking.append(part.content)
            case native.ToolCallPart():
                calls.append(_tool_call(part))
            case native.NativeToolCallPart() | native.NativeToolReturnPart():
                # The provider already executed these; provider_state keeps them for replay.
                continue
            case _:
                raise ProviderError(f"Unsupported response part: {type(part).__name__}")
    raw = native.ModelMessagesTypeAdapter.dump_python([response], mode="json")[0]
    if info.provider is None:
        raise ProviderError("PydanticAI responses require a provider identity")
    message = AssistantMessage(
        "".join(text),
        tuple(calls),
        reasoning="".join(thinking),
        provider_state=ProviderState(
            format="pydantic-ai.response",
            version=1,
            provider=info.provider,
            model=info.name,
            data=raw,
        ),
    )
    reason = _STOP_REASONS.get(
        response.finish_reason or "", StopReason.TOOL_CALLS if calls else StopReason.END
    )
    if reason is StopReason.TOOL_CALLS and not calls:
        # Native calls finish inside the response; nothing is left to execute.
        reason = StopReason.END
    return ModelResponse(
        message,
        model_usage(response.usage),
        reason,
    )


def model_usage(usage: NativeUsage) -> ModelUsage:
    return ModelUsage(
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        usage.details.get("reasoning_tokens"),
        float(usage.cost) if usage.cost is not None else None,
    )


def provider_error(error: Exception) -> ProviderError | None:
    from pydantic_ai import exceptions as native

    if not isinstance(error, native.ModelAPIError):
        return None
    kind = ProviderError
    match error:
        case native.ModelContextWindowExceededError():
            kind = ContextLengthExceeded
        case native.ModelAuthenticationError():
            kind = AuthError
        case native.ModelRateLimitError():
            kind = RateLimited
        case native.ModelRequestRejectedError():
            kind = InvalidRequest
        case (
            native.ModelConnectionError()
            | native.ModelTimeoutError()
            | native.ModelUnavailableError()
            | native.ModelServerError()
            | native.ModelOverloadedError()
        ):
            kind = ProviderUnavailable
    if kind is ProviderError and isinstance(error, native.ModelHTTPError):
        status = error.status_code
        if status in (401, 403):
            kind = AuthError
        elif status == 429:
            kind = RateLimited
        elif status in (400, 413, 422):
            kind = (
                ContextLengthExceeded
                if error.provider_error_code == "context_length_exceeded"
                else InvalidRequest
            )
        elif status in (408, 504) or status >= 500:
            kind = ProviderUnavailable
    return kind(
        f"Model request failed: {type(error).__name__}",
        status_code=getattr(error, "status_code", None),
        body=str(getattr(error, "body", "")) or None,
        retry_after=getattr(error, "retry_after", None),
    )


def _restored_response(
    message: AssistantMessage, state: ProviderState, info: ModelInfo
) -> NativeResponse:
    """Replay the original signed response, rejecting edits and foreign provider state."""
    from pydantic_ai import messages as native

    if (
        state.format != "pydantic-ai.response"
        or state.version != 1
        or state.provider != info.provider
        or state.model != info.name
    ):
        raise InvalidRequest(
            "Incompatible provider_state format or originating model; clear "
            "provider_state explicitly to discard the original signed response"
        )
    restored = native.ModelMessagesTypeAdapter.validate_json(
        json.dumps([to_dict(state.data)], allow_nan=False)
    )[0]
    if not isinstance(restored, native.ModelResponse):
        raise ValueError("Assistant provider state must contain a response")
    original = model_response(restored, info).message
    if (
        message.content != original.content
        or message.tool_calls != original.tool_calls
        or message.reasoning != original.reasoning
    ):
        raise InvalidRequest(
            "Assistant message differs from its provider state; clear provider_state "
            "when editing history to discard the original signed response"
        )
    return restored


def _unsigned_response(message: AssistantMessage) -> NativeResponse:
    from pydantic_ai import messages as native

    parts: list[native.ModelResponsePart] = []
    # Only native state carries the provider's reasoning signatures. Neutral
    # partial/synthetic reasoning remains in the audit log, never replayed unsigned.
    if message.text:
        parts.append(native.TextPart(message.text))
    parts.extend(
        native.ToolCallPart(
            call.name,
            call.malformed.text if call.malformed is not None else to_dict(call.arguments),
            call.id,
        )
        for call in message.tool_calls
    )
    return native.ModelResponse(parts)


def _tool_return(message: ToolMessage, calls: dict[str, str]) -> NativeRequest:
    from pydantic_ai import messages as native

    if message.status == ToolStatus.DENIED:
        outcome = "denied"
    elif message.is_error:
        outcome = "failed"
    else:
        outcome = "success"
    return native.ModelRequest(
        [
            native.ToolReturnPart(
                calls.get(message.tool_call_id or "", "unknown_tool"),
                _content(message.content),
                message.tool_call_id or "",
                outcome=outcome,
            )
        ]
    )


def _content(content: str | tuple[ContentPart, ...]) -> str | list[str | NativeImageUrl]:
    from pydantic_ai import messages as native

    if isinstance(content, str):
        return content
    if any(isinstance(part, ImageReference) for part in content):
        raise InvalidRequest("Resolve stored image references before calling a model directly")
    parts: list[str | NativeImageUrl] = []
    for part in content:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif not isinstance(part, ImageReference):
            parts.append(
                native.ImageUrl(
                    part.url,
                    vendor_metadata={
                        "detail": "high" if part.detail == "original" else part.detail
                    },
                )
            )
    return parts


def _tool_call(part: NativeToolCall) -> ToolCall:
    # Native convenience serializers may wrap invalid JSON in an ordinary
    # object. Validate the original payload so it cannot become executable.
    raw = part.args if isinstance(part.args, str) else repr(part.args)
    try:
        if not isinstance(part.args, str):
            raw = json.dumps(part.args or {})
        return ToolCall(part.tool_name, json.loads(raw or "{}"), part.tool_call_id)
    except (ValueError, TypeError):
        return ToolCall(
            part.tool_name,
            {},
            part.tool_call_id,
            malformed=MalformedArguments(raw),
        )


_STOP_REASONS = {
    "stop": StopReason.END,
    "length": StopReason.MAX_TOKENS,
    "tool_call": StopReason.TOOL_CALLS,
    "content_filter": StopReason.CONTENT_FILTER,
    "error": StopReason.ERROR,
}
