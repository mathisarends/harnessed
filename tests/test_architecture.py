import ast
import importlib
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from harnessed.kernel import PluginStatus
from tests._kernel_recipe import kernel_recipe

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "harnessed"


def test_event_vocabulary_does_not_depend_on_runtime():
    live_and_persistence = {"codec.py", "completion.py", "live.py", "registry.py", "stream.py"}
    paths = [
        path for path in (PACKAGE / "events").glob("*.py") if path.name not in live_and_persistence
    ]
    assert paths
    violations = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            if any(
                module.startswith(
                    (
                        "harnessed.runtime",
                        "harnessed.sessions",
                        "harnessed.children",
                        "harnessed.approvals",
                        "harnessed.context",
                        "harnessed.output",
                    )
                )
                for module in modules
            ):
                violations.append(path.name)
    assert violations == []


def test_runtime_components_and_hooks_do_not_reference_the_application_session():
    # Composition and child-session ownership boundaries may name the application session.
    owners = {
        "runtime/session.py",
        "runtime/opener.py",
        "runtime/agent_loop.py",
        "children/contracts.py",
        "children/execution.py",
    }
    parts = ("runtime", "children", "approvals", "context", "output")
    paths = (path for part in parts for path in (PACKAGE / part).glob("*.py"))
    violations = [
        path.relative_to(PACKAGE).as_posix()
        for path in paths
        if path.relative_to(PACKAGE).as_posix() not in owners
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
        and (
            node.module == "harnessed.runtime.session"
            or (path.parent.name == "runtime" and node.level == 1 and node.module == "session")
        )
    ]
    assert violations == []


def test_optional_integrations_and_features_are_isolated():
    paths = tuple(PACKAGE.rglob("*.py"))
    assert paths
    for path in paths:
        relative = path.relative_to(PACKAGE).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = ".".join(("harnessed", *path.relative_to(PACKAGE).parts[:-1]))
                imported = [
                    importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                    if node.level
                    else node.module or ""
                ]
            for module in imported:
                if module == "harnessed.testing" or module.startswith("harnessed.testing."):
                    assert relative.startswith("testing/"), relative
                if module == "pydantic_ai" or module.startswith("pydantic_ai."):
                    assert relative.startswith("models/pydantic_ai/"), relative
                if module == "yaml":
                    assert relative == "plugins/skills.py", relative
                if module.split(".")[0] in {
                    "fastmcp",
                    "mcp",
                    "jsonschema",
                    "referencing",
                    "httpx",
                    "httpx2",
                    "anyio",
                }:
                    assert relative.startswith("plugins/mcp/"), relative
                if relative.startswith("plugins/") and relative != "plugins/__init__.py":
                    assert module != "harnessed.plugins", relative
                    if module.startswith("harnessed.plugins."):
                        feature = relative.split("/")[1].split(".")[0]
                        assert module.split(".")[2] == feature, relative


def test_kernel_import_is_independent_of_agents_adapters_and_third_party_packages():
    code = """
import sys
from harnessed.kernel import Scope, PluginBase, Bundle, Event, Key, hook
for layer in ('runtime', 'events', 'models', 'plugins', 'prompts', 'sessions', 'tools'):
    assert not any(name == 'harnessed.' + layer or name.startswith('harnessed.' + layer + '.')
                   for name in sys.modules), (layer, sorted(sys.modules))
assert 'httpx' not in sys.modules
assert 'httpx2' not in sys.modules
assert 'pydantic' not in sys.modules
assert not any(name.startswith('opentelemetry') for name in sys.modules)
print('standalone kernel')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_event_vocabulary_loads_without_the_execution_engine_or_provider_adapter():
    code = """
import sys
from harnessed.events import TurnEndedEvent
assert TurnEndedEvent.__module__ == 'harnessed.events.turn'
assert 'harnessed.events.stream' not in sys.modules
assert 'harnessed.events.registry' not in sys.modules
assert 'harnessed.models.pydantic_ai' not in sys.modules
assert 'harnessed.runtime' not in sys.modules
assert 'harnessed.sessions' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr


def test_validation_is_explicit_and_otel_is_optional_and_isolated():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == ["pydantic>=2.12,<3"]
    assert "otel" in project["optional-dependencies"]
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module.startswith("opentelemetry") for module in modules):
                assert path.relative_to(PACKAGE).as_posix() == "integrations/opentelemetry.py"


@pytest.mark.parametrize(
    "module",
    [
        "harnessed",
        "harnessed.errors",
        "harnessed.events",
        "harnessed.hooks",
        "harnessed.models",
        "harnessed.plugins",
        "harnessed.context",
        "harnessed.approvals",
        "harnessed.output",
        "harnessed.prompts",
        "harnessed.sessions",
        "harnessed.tools",
        "harnessed.tracing",
    ],
)
def test_public_exports_resolve_to_their_canonical_classes_and_functions(module):
    facade = importlib.import_module(module)
    declarations = dict(getattr(facade, "_EXPORTS", {}))
    type_aliases = set()
    if not declarations:
        source = Path(facade.__file__).read_text(encoding="utf-8")
        for node in ast.parse(source).body:
            if isinstance(node, ast.ImportFrom):
                canonical = (
                    importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), facade.__package__
                    )
                    if node.level
                    else node.module
                )
                for alias in node.names:
                    if (name := alias.asname or alias.name) in facade.__all__:
                        declarations[name] = (canonical, alias.name)
            elif isinstance(node, ast.TypeAlias) and node.name.id in facade.__all__:
                type_aliases.add(node.name.id)
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in facade.__all__:
                declarations[node.name] = (module, node.name)
    assert set(declarations) | type_aliases == set(facade.__all__)
    for name, (canonical, attribute) in declarations.items():
        module = importlib.import_module(canonical)
        expected = module if attribute is None else getattr(module, attribute)
        assert getattr(facade, name) is expected
    assert facade.__all__ == sorted(set(facade.__all__))


def test_application_facade_stays_small_and_has_no_transitional_names():
    import harnessed

    assert set(harnessed.__all__) == {
        "Agent",
        "AgentLoop",
        "Approvals",
        "AutoReview",
        "Compaction",
        "Context",
        "Decision",
        "File",
        "Image",
        "OutputContract",
        "Plugin",
        "PluginConfig",
        "RunResult",
        "Session",
        "SystemPrompt",
        "ToolContext",
        "events",
        "tool",
        "SessionStatus",
        "PluginValidation",
    }


def test_kernel_scope_has_one_canonical_public_name():
    import harnessed.kernel as kernel

    assert kernel.Scope.__name__ == "Scope"
    assert kernel.ScopeClosed.__name__ == "ScopeClosed"
    assert not hasattr(kernel, "Context") and not hasattr(kernel, "ContextClosed")


def test_kernel_has_no_function_plugin_decorator():
    import harnessed.kernel as kernel

    assert "plugin" not in kernel.__all__
    assert not callable(getattr(kernel, "plugin", None))


async def test_session_exposes_its_kernel_scope_without_a_legacy_alias():
    from harnessed import Agent
    from harnessed.kernel import Scope
    from harnessed.testing import ScriptedModel

    async with Agent(model=ScriptedModel([])).session() as session:
        assert isinstance(session.scope, Scope)
        assert not hasattr(session, "ctx")


@pytest.mark.parametrize(
    "name", ["SystemMessage", "UserMessage", "AssistantMessage", "ToolMessage"]
)
def test_message_facade_uses_explicit_canonical_names_without_aliases(name: str) -> None:
    import harnessed.models as models
    import harnessed.models.messages as messages

    assert getattr(models, name) is getattr(messages, name)


@pytest.mark.parametrize("name", ["System", "User", "Assistant", "ToolReply"])
def test_message_variants_have_no_transitional_aliases(name: str) -> None:
    import harnessed.models as models
    import harnessed.models.messages as messages

    assert not hasattr(messages, name) and not hasattr(models, name)


def test_package_root_has_only_public_entrypoints_and_no_transition_modules():
    assert {path.name for path in PACKAGE.glob("*.py")} == {
        "__init__.py",
        "agent.py",
        "auto_review.py",
        "errors.py",
        "hooks.py",
        "tracing.py",
    }
    assert (PACKAGE / "events" / "__init__.py").is_file()
    assert not (PACKAGE / "core" / "__init__.py").exists()
    assert not (PACKAGE / "std").exists()
    assert not (PACKAGE / "adapters").exists()
    assert not (PACKAGE / "seams").exists()
    for name in ("_context", "agents", "observe", "permissions", "tools"):
        assert not (PACKAGE / "plugins" / name).exists()


async def test_typed_keys_have_identity_and_work_with_temporal_needs():
    from harnessed.kernel import Key, Scope

    first, second = Key[str]("backend"), Key[str]("backend")
    seen = []

    @kernel_recipe(needs=[first])
    async def feature(scope):
        seen.append(scope[first])
        yield

    async with Scope() as scope:
        scope.provide(second, "other")
        handle = await scope.plugin(feature())
        assert handle.status == PluginStatus.WAITING
        remove = scope.provide(first, "one")
        await scope.settle()
        assert seen == ["one"] and scope[second] == "other"
        await remove()
        await scope.settle()
        assert handle.status == PluginStatus.WAITING


async def test_kernel_registration_recipes_extend_lifetimes_without_agent_imports():
    from harnessed.kernel import PluginBase as Plugin
    from harnessed.kernel import Scope

    class Sink:
        def __init__(self):
            self.callbacks = []

    class CallbackRegistration:
        needs = (Sink,)

        def mount(self, scope, callback):
            callbacks = scope[Sink].callbacks
            callbacks.append(callback)
            scope.effect(lambda: callbacks.remove(callback))

    def callback(fn):
        fn._harnessed_registrations = (CallbackRegistration(),)
        return fn

    class Feature(Plugin):
        @callback
        def answer(self):
            return 42

    async with Scope() as scope:
        sink = Sink()
        remove = scope.provide(Sink, sink)
        handle = await scope.plugin(Feature())
        assert sink.callbacks[0]() == 42
        await remove()
        await scope.settle()
        assert sink.callbacks == [] and handle.status == PluginStatus.WAITING


def test_lazy_sdk_exports_and_type_stub_stay_in_sync():
    import harnessed

    stub = ast.parse((PACKAGE / "__init__.pyi").read_text(encoding="utf-8"))
    declared = {
        alias.asname or alias.name: (
            (
                importlib.util.resolve_name("." * node.level + alias.name, "harnessed"),
                None,
            )
            if node.module is None
            else (
                importlib.util.resolve_name("." * node.level + node.module, "harnessed"),
                alias.name,
            )
        )
        for node in stub.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    source = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    exports = next(
        node.value
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_EXPORTS" for target in node.targets)
    )
    assert declared == ast.literal_eval(exports)
    assert set(declared) == set(harnessed.__all__)


def test_events_module_is_available_lazily_from_a_fresh_sdk_import() -> None:
    code = """
import sys
import harnessed

assert 'harnessed.events' not in sys.modules
assert 'pydantic' not in sys.modules
assert 'events' in dir(harnessed)
events = harnessed.events
assert events is sys.modules['harnessed.events']
assert harnessed.events is events
assert events.MessageAppendedEvent.__name__ == 'MessageAppendedEvent'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
