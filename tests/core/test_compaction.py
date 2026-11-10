import pytest

from harnessed.core.compaction import ModelSummarizer, SummaryError
from harnessed.core.errors import ContextBudgetExceeded
from harnessed.core.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    UserMessage,
)
from harnessed.core.model import ModelInfo, ModelResponse, ModelUsage, StopReason
from harnessed.core.tokens import ApproximateTokenCounter
from harnessed.plugins import DEFAULT_COMPACTION_PROMPT, ContextState
from harnessed.services import CompactionContext, CompactionRequest
from harnessed.testing import ScriptedModel


def request() -> CompactionRequest:
    return CompactionRequest(
        CompactionContext(
            (UserMessage("Fix tests/auth.py."),),
            (UserMessage("Current task input"),),
            (SystemMessage("Operational agent instructions"),),
            ContextState(500, 100, 2000, 2),
            600,
        ),
        32,
    )


@pytest.mark.parametrize("factory", ["literal", "sync", "async", "default"])
async def test_prompt_replacement_and_separate_reference_roles(factory):
    seen = []

    def sync(context):
        seen.append(context)
        return "Preserve failing test names."

    async def asynchronous(context):
        return sync(context)

    prompts = {
        "literal": "Preserve failing test names.",
        "sync": sync,
        "async": asynchronous,
        "default": None,
    }
    usage = ModelUsage(123, 12)
    model = ScriptedModel([ModelResponse(AssistantMessage("  Decisions and next steps.  "), usage)])
    original = request()
    summary = await ModelSummarizer(
        model, prompt=prompts[factory], counter=ApproximateTokenCounter()
    ).summarize(original)
    assert summary.text == "Decisions and next steps." and summary.usage == usage
    sent = model.requests[0]
    assert sent.settings.max_tokens == 32
    assert not sent.tools
    assert sent.messages[0].text == (
        DEFAULT_COMPACTION_PROMPT if factory == "default" else "Preserve failing test names."
    )
    assert "tests/auth.py" in sent.messages[1].text
    assert "RETAINED CONTEXT\nuser: Current task input" in sent.messages[1].text
    assert "AGENT INSTRUCTIONS\nsystem: Operational agent instructions" in sent.messages[1].text
    assert len(seen) == (1 if factory in ("sync", "async") else 0)
    if seen:
        assert seen[0] is original.context


@pytest.mark.parametrize(
    "message, stop, error",
    [
        (AssistantMessage("  "), StopReason.END, "empty summary"),
        (AssistantMessage(tool_calls=(ToolCall("work"),)), StopReason.END, "tool call"),
        (AssistantMessage("unfinished"), StopReason.MAX_TOKENS, "truncated"),
        (AssistantMessage("filtered"), StopReason.CONTENT_FILTER, "did not complete"),
    ],
)
async def test_invalid_responses_preserve_usage(message, stop, error):
    model = ScriptedModel([ModelResponse(message, ModelUsage(123, 12), stop)])
    with pytest.raises(SummaryError, match=error) as caught:
        await ModelSummarizer(model, counter=ApproximateTokenCounter()).summarize(request())
    assert caught.value.usage == ModelUsage(123, 12)


async def test_smaller_model_refuses_required_reference_context_before_transport():
    model = ScriptedModel([], info=ModelInfo("small", context_window=20))
    with pytest.raises(ContextBudgetExceeded):
        await ModelSummarizer(model, counter=ApproximateTokenCounter()).summarize(request())
    assert model.requests == []


@pytest.mark.parametrize("prompt", [lambda _: "", lambda _: 3])
async def test_empty_or_invalid_factory_results_fail_before_transport(prompt):
    model = ScriptedModel([])
    with pytest.raises(ValueError, match="prompt"):
        await ModelSummarizer(model, prompt=prompt, counter=ApproximateTokenCounter()).summarize(
            request()
        )
    assert model.requests == []
