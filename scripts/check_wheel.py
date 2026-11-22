import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SMOKE = r"""
import asyncio
import json
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
import re

from harnessed.kernel import Scope, Key, needs
assert 'pydantic' not in sys.modules
from harnessed import Agent, AgentLoop, OutputContract, Plugin, PluginConfig, SystemPrompt, tool
from harnessed.errors import PluginConflict
from harnessed.models import ModelRequest, StopReason, UserMessage
from harnessed.events import TextDeltaEvent
from harnessed.events.stream import TurnResultEvent
from harnessed.plugins import Service, Skill, Skills
from harnessed.integrations.sandbox.bundle import worker_source
from harnessed.prompts import Instructions, PromptFormat, SectionDelivery, SystemPromptStore
from harnessed.runtime.composition import CompositionDescription
from harnessed.runtime.handles import RunHandle, RunStream
from harnessed.sessions import SessionCheckpoint, SessionCommit, SessionContext, SessionState
from harnessed.testing import ScriptedModel, call, reply, assert_tool_called, mount_plugin
from harnessed.tools import SessionTools, ToolPresentation, ToolRegistry
from pydantic import BaseModel
from tempfile import TemporaryDirectory
import harnessed

package = Path(harnessed.__file__).resolve()
assert package.is_relative_to(Path(sys.prefix).resolve()), package
installed = distribution('harnessed')
assert installed.metadata['Name'] == 'harnessed'
assert installed.metadata['License-Expression'] == 'MIT'
assert installed.metadata.get_all('License-File') == ['LICENSE']
licenses = [file for file in installed.files or ()
            if str(file).endswith('.dist-info/licenses/LICENSE')]
assert len(licenses) == 1
assert installed.locate_file(licenses[0]).read_text(encoding='utf-8').startswith('MIT License\n')
assert (installed.read_text('top_level.txt') or '').split() == ['harnessed']
assert installed.locate_file('harnessed/__init__.py').resolve() == package
assert (package.parent / 'py.typed').is_file()
assert (package.parent / '__init__.pyi').is_file()
requirements = {
    re.split(r'[\s\[<>=!~;(]', requirement, maxsplit=1)[0].lower().replace('_', '-')
    for requirement in installed.requires or ()
}
assert not requirements & {
    'mkdocs', 'prek', 'pytest', 'pytest-asyncio', 'ruff', 'smelt-cli', 'twine', 'ty',
}
assert not any(str(file).startswith(('docs/', 'build/', 'site/')) for file in installed.files or ())
package_files = {str(file) for file in installed.files or ()}
assert not package_files & {'harnessed/services.py', 'harnessed/sessions.py'}
assert not any(file.startswith('harnessed/core/') for file in package_files)

with TemporaryDirectory(prefix='harnessed-installed-worker-') as directory:
    worker = subprocess.run(
        [sys.executable, '-I', '-S', '-c', worker_source()],
        input='{"operation":"unknown"}', capture_output=True, text=True,
        cwd=directory, timeout=10,
    )
    assert worker.returncode == 0, worker.stderr
    worker_reply = json.loads(worker.stdout)
    assert worker_reply['value'] is None and worker_reply['error_type'] == 'ValueError'

class CalculatorConfig(PluginConfig):
    offset: int = 0

class Calculator(Plugin[CalculatorConfig]):
    @tool(readonly=True, presentation=ToolPresentation(title='Add numbers', tags=('calculator',)))
    async def add(self, a: int, b: int) -> int:
        return a + b + self.config.offset

backend_key = Key[str]('wheel.backend')

class Consumer(Plugin):
    backend = needs(backend_key)

class Answer(BaseModel):
    value: int

class Guidance(Plugin, slot='wheel.guidance'):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt('Plugin guidance.')

async def main():
    selected = Guidance()
    configured = Agent(plugins=[selected], loop=AgentLoop(max_steps=4))
    description = configured.describe()
    assert isinstance(description, CompositionDescription)
    assert configured.plugins == (selected,)
    assert description.plugins[0].slot == 'wheel.guidance'
    assert await configured.prompt.render() == 'Plugin guidance.'
    try:
        Agent(plugins=[Guidance(), Guidance()])
    except PluginConflict:
        pass
    else:
        raise AssertionError('duplicate slots must conflict')

    config = CalculatorConfig(offset=1)
    async with mount_plugin(Calculator(config=config)) as active:
        assert active.config is config
        assert await active.add(19, 23) == 43
    async with mount_plugin(Consumer(), services=(Service(backend_key, 'ready'),)) as active:
        assert active.backend == 'ready'

    message = UserMessage('Browser snapshot', id='wheel-snapshot')
    original_request = ModelRequest(())
    assert original_request.append_message(message).messages == (message,)
    assert original_request.messages == ()
    assert not hasattr(harnessed, 'Observation')
    observed_model = ScriptedModel(['observed'])
    async with Agent(model=observed_model).session() as session:
        session.observe(message, source='browser', refs=('wheel-frame',))
        await session.run('Inspect')
        assert message in observed_model.requests[0].messages

    continuation = Agent(model=ScriptedModel(["remembered", "continued"]))
    initial = await continuation.run("remember")
    state = SessionState.from_json(initial.state.to_json())
    next_turn = await continuation.run("continue", state=state)
    assert len(next_turn.state.history) == 4
    assert not hasattr(continuation, "sessions")

    documents: dict[str, dict[str, object]] = {}

    async def save(commit: SessionCommit) -> None:
        documents[commit.session_id] = commit.checkpoint.to_dict()

    persistent = Agent(model=ScriptedModel(['persisted', 'restored']))
    async with persistent.session(id='wheel-conversation', on_commit=save) as session:
        await session.append_message(UserMessage('The customer is Ada.'))
        await session.run('Remember the customer.')
    stored = SessionCheckpoint.from_dict(documents['wheel-conversation'])
    async with persistent.session(state=stored, on_commit=save) as session:
        assert session.messages[0].text == 'The customer is Ada.'
        assert (await session.run('Continue.')).text == 'restored'

    guidance = Guidance()
    simple = Agent(instructions='Role.', plugins=[guidance])
    assert await simple.prompt.render() == 'Role.\n\nPlugin guidance.'
    ordered = Agent(plugins=[guidance], instructions=SystemPrompt.from_plugins('Prefix.'))
    assert await ordered.prompt.render() == 'Prefix.\n\nPlugin guidance.'
    explicit = Agent(plugins=[guidance], instructions=SystemPrompt('Only this.'))
    assert await explicit.prompt.render() == 'Only this.'
    assert 'Guidance' in str(explicit.prompt)
    mounted_model = ScriptedModel([reply('Ready'), reply('Unmounted')])
    async with Agent(model=mounted_model,
                     instructions='Fixed.').session() as session:
        async with session.mount(guidance) as mount:
            assert mount.instance is not None
            await session.run('Mounted')
            assert any('Plugin guidance.' in message.text
                       for message in mounted_model.requests[-1].messages)
        await session.run('Unmounted')
        assert all('Plugin guidance.' not in message.text
                   for message in mounted_model.requests[-1].messages)
        assert await session.prompt.render() == 'Fixed.'
        mount = await session.mount(guidance, section_delivery=SectionDelivery.CONTEXT)
        await mount.aclose()
    agent = Agent(model=ScriptedModel([call(Calculator.add, a=19, b=23), reply('42')]),
                  plugins=[Calculator(config=CalculatorConfig())], instructions=SystemPrompt(
                      Instructions(title='Role', text='Fixed instructions.'),
                      format=PromptFormat.MARKDOWN))
    async with agent.session() as session:
        assert isinstance(session.plugin_context, SessionContext)
        assert isinstance(session.tools, SessionTools)
        assert isinstance(session.scope[SystemPromptStore], SystemPromptStore)
        assert isinstance(session.scope[ToolRegistry], ToolRegistry)
        result = await session.run('Add')
        assert result.text == '42'
        assert assert_tool_called(result, 'add', a=19, b=23)[1].content == '42'
        assert await session.prompt.render() == '## Role\n\nFixed instructions.'
    typed = await Agent(model=ScriptedModel([call('final_answer', value=42)])).run(
        'Answer', output=OutputContract('answer.v1', Answer))
    assert isinstance(typed.require_output(), Answer) and typed.require_output().value == 42
    async with Agent(model=ScriptedModel([call('final_answer', value=43)])).session() as session:
        async with session.stream('Answer', output=Answer) as run:
            assert isinstance(run, RunStream) and isinstance(run, RunHandle)
            notifications = [event async for event in run]
            result = await run.result()
            assert isinstance(notifications[-1], TurnResultEvent)
            assert notifications[-1].result is result
            assert result.require_output().value == 43
    async with Agent(model=ScriptedModel([reply('queued result')])).session() as session:
        handle = await session.submit('Submit')
        assert isinstance(handle, RunHandle) and not isinstance(handle, RunStream)
        assert (await handle.result()).text == 'queued result'
        assert handle.done()
    async with Agent(model=ScriptedModel([])).session() as session:
        await session.pause()
        handle = await session.submit('Cancel queued work')
        await handle.cancel()
        assert (await handle.result()).stop_reason is StopReason.DISCARDED
    assert TextDeltaEvent('message', 'hello').type == 'text.delta'
    inline = Skill(name='notes', description='Write notes.', instructions='Keep notes concise.')
    skills = Skills(inline)
    async with Agent(model=ScriptedModel([call('load_skill', name='notes'), reply('Loaded')]),
                     plugins=[skills]).session() as session:
        result = await session.run('Load notes')
        assert 'Keep notes concise.' in result.tool_calls[0][1].text
    assert not any(name.startswith('pydantic_ai') for name in sys.modules)
    assert not any(name in sys.modules for name in ('mkdocs', 'markdown', 'yaml'))

asyncio.run(main())
print('Installed harnessed wheel, metadata, public API, and SDK smoke checks verified.')
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test an installed wheel outside the checkout."
    )
    parser.add_argument(
        "wheel", nargs="?", type=Path, help="Wheel to check; defaults to the only dist/*.whl"
    )
    options = parser.parse_args()
    uv = os.environ.get("UV_BIN", "uv")
    wheels = (
        tuple((ROOT / "dist").glob("*.whl"))
        if options.wheel is None
        else (options.wheel.resolve(strict=True),)
    )
    if len(wheels) != 1:
        raise SystemExit(
            "Pass a wheel path or build exactly one wheel with `uv build --no-sources`."
        )
    with TemporaryDirectory(prefix="harnessed-wheel-") as directory:
        temporary = Path(directory)
        environment = temporary / "venv"
        requirements = temporary / "requirements.txt"
        subprocess.run(
            [
                uv,
                "export",
                "--locked",
                "--no-dev",
                "--all-extras",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run([uv, "venv", "--python", sys.executable, str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--requirements",
                str(requirements),
            ],
            check=True,
        )
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                str(wheels[0]),
            ],
            check=True,
        )
        subprocess.run([str(python), "-I", "-c", SMOKE], cwd=temporary, check=True)


if __name__ == "__main__":
    main()
