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
    "ApprovalAnswer": ("harnessed.models.messages", "ApprovalAnswer"),
    "CompositionDescription": ("harnessed.runtime.composition", "CompositionDescription"),
    "File": ("harnessed.runtime.attachments", "File"),
    "Image": ("harnessed.runtime.attachments", "Image"),
    "ImageDetail": ("harnessed.models.messages", "ImageDetail"),
    "Inject": ("harnessed.kernel", "Inject"),
    "PluginPack": ("harnessed.runtime.composition", "PluginPack"),
    "PluginPackPromptPolicy": ("harnessed.runtime.composition", "PluginPackPromptPolicy"),
    "OutputContract": ("harnessed.runtime.outputs", "OutputContract"),
    "Plugin": ("harnessed.runtime.plugin", "Plugin"),
    "PluginConfig": ("harnessed.runtime.plugin", "PluginConfig"),
    "PluginValidation": ("harnessed.runtime.composition", "PluginValidation"),
    "PluginState": ("harnessed.kernel", "PluginState"),
    "PluginConflict": ("harnessed.errors", "PluginConflict"),
    "PolicyDecision": ("harnessed.models.messages", "PolicyDecision"),
    "Role": ("harnessed.models.messages", "Role"),
    "ResolvedPluginPack": ("harnessed.runtime.composition", "ResolvedPluginPack"),
    "RunHandle": ("harnessed.runtime.handles", "RunHandle"),
    "RunResult": ("harnessed.sessions.result", "RunResult"),
    "RunStream": ("harnessed.runtime.handles", "RunStream"),
    "PromptFormat": ("harnessed.prompts.values", "PromptFormat"),
    "SectionDelivery": ("harnessed.prompts.values", "SectionDelivery"),
    "SystemPrompt": ("harnessed.prompts.values", "SystemPrompt"),
    "Instructions": ("harnessed.prompts.values", "Instructions"),
    "Session": ("harnessed.runtime.session", "Session"),
    "SessionClosed": ("harnessed.errors", "SessionClosed"),
    "SessionLog": ("harnessed.sessions.log", "SessionLog"),
    "SessionState": ("harnessed.sessions.state", "SessionState"),
    "SessionStatus": ("harnessed.runtime.control", "SessionStatus"),
    "StopReason": ("harnessed.models.model", "StopReason"),
    "StreamOverflow": ("harnessed.errors", "StreamOverflow"),
    "ToolCall": ("harnessed.models.messages", "ToolCall"),
    "ToolContext": ("harnessed.tools.context", "ToolContext"),
    "ToolError": ("harnessed.tools.registry", "ToolError"),
    "ToolResult": ("harnessed.models.messages", "ToolResult"),
    "ToolAnnotations": ("harnessed.tools.info", "ToolAnnotations"),
    "ToolIcon": ("harnessed.tools.info", "ToolIcon"),
    "ToolParameter": ("harnessed.tools.parameters", "ToolParameter"),
    "ToolPresentation": ("harnessed.tools.info", "ToolPresentation"),
    "ToolChain": ("harnessed.tools.info", "ToolChain"),
    "ToolInfo": ("harnessed.tools.info", "ToolInfo"),
    "ToolMetadata": ("harnessed.tools.info", "ToolMetadata"),
    "ToolEnvelope": ("harnessed.tools.info", "ToolEnvelope"),
    "ToolStatus": ("harnessed.models.messages", "ToolStatus"),
    "envelope_value": ("harnessed.tools.envelope", "envelope_value"),
    "events": ("harnessed.events", None),
    "hook": ("harnessed.kernel", "hook"),
    "plugin": ("harnessed.kernel", "plugin"),
    "tool": ("harnessed.tools.decorator", "tool"),
}


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
