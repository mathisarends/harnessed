import json
import os
import subprocess
import sys

import pytest

from harnessed.integrations.sandbox.bundle import worker_source


@pytest.mark.parametrize("serve", [False, True])
def test_bundled_worker_runs_without_site_packages_or_workspace_imports(tmp_path, serve):
    for name in ("harnessed", "_harnessed_worker"):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("raise AssertionError('workspace import')\n")
    (tmp_path / "json.py").write_text("raise AssertionError('workspace stdlib shadow')\n")
    command = [sys.executable, "-I", "-S", "-c", worker_source()]
    operations = ["invalid-operation"]
    if serve:
        command.append("--serve")
        operations.append("another-invalid-operation")
    payload = "\n".join(json.dumps({"operation": name}) for name in operations) + "\n"
    result = subprocess.run(
        command,
        input=payload,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    if serve:
        assert replies.pop(0) == {"ready": True}
    assert len(replies) == len(operations)
    for reply, operation in zip(replies, operations, strict=True):
        assert reply["value"] is None
        assert reply["error_type"] == "ValueError"
        assert operation in reply["error"]
