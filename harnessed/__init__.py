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
    "Approvals": ("harnessed.runtime.approval_policy", "Approvals"),
    "Compaction": ("harnessed.context", "Compaction"),
    "Context": ("harnessed.context", "Context"),
    "Decision": ("harnessed.models", "Decision"),
    "File": ("harnessed.runtime.attachments", "File"),
    "Image": ("harnessed.runtime.attachments", "Image"),
    "OutputContract": ("harnessed.runtime.outputs", "OutputContract"),
    "Plugin": ("harnessed.runtime.plugin", "Plugin"),
    "PluginConfig": ("harnessed.runtime.plugin", "PluginConfig"),
    "RunResult": ("harnessed.sessions.result", "RunResult"),
    "SessionStatus": ("harnessed.runtime.control", "SessionStatus"),
    "PluginValidation": ("harnessed.runtime.composition", "PluginValidation"),
    "Session": ("harnessed.runtime.session", "Session"),
    "SystemPrompt": ("harnessed.prompts", "SystemPrompt"),
    "ToolContext": ("harnessed.tools.context", "ToolContext"),
    "events": ("harnessed.events", None),
    "tool": ("harnessed.tools.decorator", "tool"),
}


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
