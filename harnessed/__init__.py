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
    "ApprovalAnswer": ("harnessed.core.messages", "ApprovalAnswer"),
    "CompositionDescription": ("harnessed.core.composition", "CompositionDescription"),
    "File": ("harnessed.core.attachments", "File"),
    "Image": ("harnessed.core.attachments", "Image"),
    "ImageDetail": ("harnessed.core.messages", "ImageDetail"),
    "Inject": ("harnessed.kernel", "Inject"),
    "PluginPack": ("harnessed.core.composition", "PluginPack"),
    "PluginPackPromptPolicy": ("harnessed.core.composition", "PluginPackPromptPolicy"),
    "Observation": ("harnessed.core.messages", "Observation"),
    "OutputContract": ("harnessed.core.outputs", "OutputContract"),
    "Plugin": ("harnessed.core.plugin", "Plugin"),
    "PluginConfig": ("harnessed.core.plugin", "PluginConfig"),
    "PluginValidation": ("harnessed.core.composition", "PluginValidation"),
    "PluginState": ("harnessed.kernel", "PluginState"),
    "PluginConflict": ("harnessed.core.errors", "PluginConflict"),
    "PolicyDecision": ("harnessed.core.messages", "PolicyDecision"),
    "Role": ("harnessed.core.messages", "Role"),
    "ResolvedPluginPack": ("harnessed.core.composition", "ResolvedPluginPack"),
    "RunHandle": ("harnessed.core.runtime.handles", "RunHandle"),
    "RunResult": ("harnessed.core.result", "RunResult"),
    "PromptFormat": ("harnessed.core.prompt.values", "PromptFormat"),
    "SectionDelivery": ("harnessed.core.prompt.values", "SectionDelivery"),
    "SystemPrompt": ("harnessed.core.prompt.values", "SystemPrompt"),
    "Instructions": ("harnessed.core.prompt.values", "Instructions"),
    "Session": ("harnessed.core.runtime.session", "Session"),
    "SessionClosed": ("harnessed.core.errors", "SessionClosed"),
    "SessionLog": ("harnessed.core.session.session", "SessionLog"),
    "SessionState": ("harnessed.core.session.state", "SessionState"),
    "SessionStatus": ("harnessed.core.runtime.control", "SessionStatus"),
    "StopReason": ("harnessed.core.model", "StopReason"),
    "StreamOverflow": ("harnessed.core.errors", "StreamOverflow"),
    "ToolCall": ("harnessed.core.messages", "ToolCall"),
    "ToolContext": ("harnessed.core.tools.context", "ToolContext"),
    "ToolError": ("harnessed.core.tools.registry", "ToolError"),
    "ToolResult": ("harnessed.core.messages", "ToolResult"),
    "ToolAnnotations": ("harnessed.core.tools.info", "ToolAnnotations"),
    "ToolIcon": ("harnessed.core.tools.info", "ToolIcon"),
    "ToolPresentation": ("harnessed.core.tools.info", "ToolPresentation"),
    "ToolChain": ("harnessed.core.tools.info", "ToolChain"),
    "ToolInfo": ("harnessed.core.tools.info", "ToolInfo"),
    "ToolMetadata": ("harnessed.core.tools.info", "ToolMetadata"),
    "ToolStatus": ("harnessed.core.messages", "ToolStatus"),
    "events": ("harnessed.events", None),
    "hook": ("harnessed.kernel", "hook"),
    "plugin": ("harnessed.kernel", "plugin"),
    "tool": ("harnessed.core.tools.decorator", "tool"),
}


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
