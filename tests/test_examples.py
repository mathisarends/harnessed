import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_RUN_EXAMPLE = """
import faulthandler
import runpy
import sys

module = sys.argv.pop(1)
faulthandler.dump_traceback_later(15, repeat=True)
runpy.run_module(module, run_name="__main__", alter_sys=True)
"""


_STAGES = ("getting_started", "applications", "plugin_authoring")
_EXTERNAL_EXAMPLES = frozenset(
    {
        "examples.getting_started.05_deepseek",
        "examples.applications.09_mcp",
    }
)
_OFFLINE_EXAMPLES = (
    "examples.getting_started.01_tool_agent",
    "examples.getting_started.02_plugins",
    "examples.getting_started.03_basic_stream",
    "examples.getting_started.04_structured_output",
    "examples.applications.01_approvals",
    "examples.applications.02_ui_stream",
    "examples.applications.03_inbox",
    "examples.applications.04_pause_resume_stop",
    "examples.applications.05_parallel_tools",
    "examples.applications.06_background_shell",
    "examples.applications.07_sandbox",
    "examples.applications.08_skills",
    "examples.applications.10_tool_output_limits",
    "examples.applications.11_session_spill",
    "examples.applications.12_compaction_policies",
    "examples.applications.13_context_strategies",
    "examples.applications.14_session_state",
    "examples.applications.15_external_state",
    "examples.applications.16_session_store",
    "examples.applications.17_typed_session_store",
    "examples.applications.18_atomic_session_store",
    "examples.applications.19_subagents",
    "examples.applications.20_background_subagents",
    "examples.applications.21_tracing",
    "examples.plugin_authoring.01_explicit_composition",
    "examples.plugin_authoring.02_tool_context",
    "examples.plugin_authoring.03_validated_forms",
    "examples.plugin_authoring.04_conditional_tools",
    "examples.plugin_authoring.05_conditional_parameters",
    "examples.plugin_authoring.06_system_prompts",
    "examples.plugin_authoring.07_class_plugins",
    "examples.plugin_authoring.08_failure_nudge",
    "examples.plugin_authoring.09_rules",
    "examples.plugin_authoring.10_context",
    "examples.plugin_authoring.11_observations",
    "examples.plugin_authoring.12_custom_events",
    "examples.plugin_authoring.13_plugin_recipes",
    "examples.plugin_authoring.14_plugin_lifecycle",
    "examples.plugin_authoring.15_builtin_tool_lifetimes",
    "examples.plugin_authoring.16_temporal_needs",
    "examples.plugin_authoring.17_layered_sdk",
    "examples.plugin_authoring.18_models",
)


@pytest.mark.parametrize("module", _OFFLINE_EXAMPLES)
def test_offline_sdk_example_runs(module):
    if module == "examples.applications.21_tracing":
        pytest.importorskip("opentelemetry.sdk")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _RUN_EXAMPLE, module],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired as error:
        output = error.stderr or b""
        details = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
        pytest.fail(f"{module} timed out; child traceback:\n{details[-8000:]}", pytrace=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout


def test_examples_follow_a_contiguous_learning_path():
    root = Path(__file__).resolve().parents[1] / "examples"
    found = set()
    for stage in _STAGES:
        numbered = sorted(path.stem for path in (root / stage).glob("[0-9][0-9]_*.py"))
        assert [name[:2] for name in numbered] == [f"{n:02}" for n in range(1, len(numbered) + 1)]
        found.update(f"examples.{stage}.{name}" for name in numbered)
    assert sorted(path.name for path in root.glob("[0-9]*.py")) == []
    assert found == set(_OFFLINE_EXAMPLES) | _EXTERNAL_EXAMPLES


def test_examples_do_not_import_the_internal_runtime():
    root = Path(__file__).resolve().parents[1] / "examples"
    found = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "harnessed.runtime"
            ):
                found.update((node.module, alias.name) for alias in node.names)
    assert found == set()
