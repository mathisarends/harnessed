from dataclasses import replace
from uuid import uuid4

from harnessed.events import (
    AssistantMessageEvent,
    ModelUsageRecordedEvent,
    ReasoningDeltaEvent,
    TextDeltaEvent,
    ToolArgumentsDeltaEvent,
    ToolCallGenerationStartedEvent,
)
from harnessed.kernel import Scope
from harnessed.models import (
    AssistantMessage,
    Model,
    ModelAttemptOutcome,
    ModelChunk,
    ModelRequest,
    ModelStream,
    ToolCall,
    model_diagnostics,
)
from harnessed.sessions import SessionContext
from harnessed.sessions.log import SessionLog

from .diagnostics import RequestDiagnostics
from .hooks import StreamModel
from .tracing import Span, span


class ModelResponder:
    def __init__(self, scope: Scope, log: SessionLog, context: SessionContext) -> None:
        self.scope = scope
        self.log = log
        self.context = context

    async def respond(
        self, request: ModelRequest, *, model: Model, request_id: str
    ) -> AssistantMessage:
        with span(
            "harnessed.model",
            scope=self.scope,
            session_id=self.log.id,
            model=model.info.name,
            request_id=request_id,
        ) as current:
            diagnostics = RequestDiagnostics(self.log, request_id)
            try:
                with model_diagnostics(diagnostics.receive):
                    response = await self._stream_response(
                        request,
                        model=model,
                        request_id=request_id,
                        trace=current,
                        diagnostics=diagnostics,
                    )
                await diagnostics.finish(ModelAttemptOutcome.SUCCEEDED)
                return response
            except BaseException as error:
                await diagnostics.failed(error)
                raise
            finally:
                diagnostics.closed = True

    async def _stream_response(
        self,
        request: ModelRequest,
        *,
        model: Model,
        request_id: str,
        trace: Span,
        diagnostics: RequestDiagnostics,
    ) -> AssistantMessage:
        message_id = uuid4().hex
        calls: list[ToolCall] = []
        text: list[str] = []
        reasoning: list[str] = []
        stream: ModelStream | None = None

        def message() -> AssistantMessage:
            return AssistantMessage(
                "".join(text),
                tuple(calls),
                reasoning="".join(reasoning),
                id=message_id,
            )

        try:
            transport = await self.log.resolve_request(request)
            # Trace the transport request: stored image references are resolved to data.
            trace.set_attribute("request", transport)
            manager = await self.scope.dispatch(
                StreamModel(self.context, transport, model, request_id=request_id),
                default=lambda: model.stream(transport),
            )
            async with manager as stream:
                async for chunk in stream:
                    if chunk.diagnostic is not None:
                        await diagnostics.receive(chunk.diagnostic)
                    if (
                        chunk.text
                        or chunk.reasoning
                        or chunk.tool_call
                        or chunk.tool_call_start
                        or chunk.tool_call_delta
                    ):
                        diagnostics.partial = True
                    if chunk.text or chunk.reasoning:
                        text.append(chunk.text)
                        reasoning.append(chunk.reasoning)
                    if chunk.tool_call is not None:
                        calls.append(chunk.tool_call)
                    await self._publish_deltas(message_id, chunk)
                response = stream.response()
        except BaseException:
            if stream is not None and (usage := stream.usage) is not None:
                trace.set_attribute("usage", usage)
                await self.log.append(ModelUsageRecordedEvent(usage, request_id=request_id))
            if text or reasoning:
                trace.set_attribute("output", message())
                await self.log.append(
                    AssistantMessageEvent(message(), request_id=request_id, interrupted=True)
                )
            raise
        trace.set_attribute("usage", response.usage)
        trace.set_attribute("output", response.message)
        trace.set_attribute("stop_reason", response.stop_reason)
        call_ids = {call.id for call in calls}
        if len(call_ids) != len(calls):
            raise ValueError("The model returned duplicate tool call ids")
        await self.log.append(ModelUsageRecordedEvent(response.usage, request_id=request_id))
        final = replace(response.message, id=message_id)
        await self.log.append(
            AssistantMessageEvent(final, request_id=request_id, stop_reason=response.stop_reason)
        )
        return final

    async def _publish_deltas(self, message_id: str, chunk: ModelChunk) -> None:
        """Publish a chunk's transient text, reasoning and tool-call generation deltas."""
        if chunk.text:
            await self.log.publish(TextDeltaEvent(message_id, chunk.text))
        if chunk.reasoning:
            await self.log.publish(ReasoningDeltaEvent(message_id, chunk.reasoning))
        if chunk.tool_call_start is not None:
            await self.log.publish(
                ToolCallGenerationStartedEvent(message_id, chunk.tool_call_start)
            )
        if chunk.tool_call_delta is not None:
            await self.log.publish(ToolArgumentsDeltaEvent(message_id, chunk.tool_call_delta))
