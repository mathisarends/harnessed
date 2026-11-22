import logging
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Keep the standalone kernel independent of Pydantic and optional provider SDKs.
def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    imported = import_module(module)
    value = imported if attribute is None else getattr(imported, attribute)
    globals()[name] = value
    return value


_EXPORTS = {
    "Agent": ("harnessed.agent", "Agent"),
    "AgentLoop": ("harnessed.runtime.agent_loop", "AgentLoop"),
    "Approvals": ("harnessed.approvals", "Approvals"),
    "AutoReview": ("harnessed.auto_review", "AutoReview"),
    "Compaction": ("harnessed.context", "Compaction"),
    "Context": ("harnessed.context", "Context"),
    "Decision": ("harnessed.models", "Decision"),
    "FinalStepPolicy": ("harnessed.events.turn", "FinalStepPolicy"),
    "File": ("harnessed.runtime.attachments", "File"),
    "Image": ("harnessed.runtime.attachments", "Image"),
    "Inject": ("harnessed.kernel", "Inject"),
    "Instructions": ("harnessed.prompts", "Instructions"),
    "OutputContract": ("harnessed.output", "OutputContract"),
    "Plugin": ("harnessed.runtime.plugin", "Plugin"),
    "PluginConfig": ("harnessed.runtime.plugin", "PluginConfig"),
    "PluginScope": ("harnessed.kernel", "PluginScope"),
    "PluginState": ("harnessed.kernel", "PluginState"),
    "RunResult": ("harnessed.sessions.result", "RunResult"),
    "Service": ("harnessed.runtime.plugin", "Service"),
    "SessionStatus": ("harnessed.runtime.control", "SessionStatus"),
    "PluginValidation": ("harnessed.runtime.composition", "PluginValidation"),
    "Session": ("harnessed.runtime.session", "Session"),
    "SystemPrompt": ("harnessed.prompts", "SystemPrompt"),
    "ToolContext": ("harnessed.tools.context", "ToolContext"),
    "ToolError": ("harnessed.tools.registry", "ToolError"),
    "events": ("harnessed.events", None),
    "hook": ("harnessed.kernel", "hook"),
    "needs": ("harnessed.kernel", "needs"),
    "observes": ("harnessed.kernel", "observes"),
    "tool": ("harnessed.tools.decorator", "tool"),
}


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
