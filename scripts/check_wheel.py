import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SMOKE = r"""
import asyncio
import sys
from importlib.metadata import distribution
from pathlib import Path
import re

from harnessed.kernel import Context, Key, dependency
assert 'pydantic' not in sys.modules
from harnessed import (
    Agent, CompositionDescription, Kit, SessionState, OutputContract, Plugin, PluginConfig,
    PluginOrder,
    PromptFormat, SectionDelivery,
    SystemPrompt, Instructions, tool,
)
from harnessed.errors import ConfigError
from harnessed.events import TextDeltaEvent, TurnFinishedEvent
from harnessed.plugins import Service, Skill, Skills
from harnessed.integrations.json import JsonSessionStore
from harnessed.sessions import StoredSession
from harnessed.testing import ScriptedModel, call, reply, assert_tool_called, mount_plugin
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

class CalculatorConfig(PluginConfig):
    offset: int = 0

class Calculator(Plugin[CalculatorConfig]):
    @tool(readonly=True)
    async def add(self, a: int, b: int) -> int:
        return a + b + self.config.offset

backend_key = Key[str]('wheel.backend')

class Consumer(Plugin):
    backend = dependency(backend_key)

class Answer(BaseModel):
    value: int

class Guidance(Plugin, slot='wheel.guidance'):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt('Plugin guidance.')

async def main():
    defaults = Kit('library', [Guidance()])
    selected = Guidance()
    configured = Agent(plugins=[defaults, selected],
                       prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))
    description = configured.describe()
    assert isinstance(description, CompositionDescription)
    assert configured.plugins[-1] is selected
    assert description.plugins[-1].replaces[0].kit_path == ('library',)
    assert await configured.prompt.render() == 'Plugin guidance.'
    filtered = Agent(plugins=[defaults.without(Guidance)])
    assert filtered.describe().removed[0].removed_by == ('library',)
    assert await filtered.prompt.render() == ''

    config = CalculatorConfig(offset=1)
    async with mount_plugin(Calculator(config=config)) as active:
        assert active.config is config
        assert await active.add(19, 23) == 43
    async with mount_plugin(Consumer(), services=(Service(backend_key, 'ready'),)) as active:
        assert active.backend == 'ready'

    continuation = Agent(model=ScriptedModel(["remembered", "continued"]))
    initial = await continuation.run("remember")
    state = SessionState.from_json(initial.state.to_json())
    next_turn = await continuation.run("continue", state=state)
    assert len(next_turn.state.history) == 4
    assert not hasattr(continuation, "sessions")

    with TemporaryDirectory(prefix='wheel-sessions-') as directory:
        store = JsonSessionStore(root=directory)
        persistent = Agent(model=ScriptedModel(['persisted', 'restored']), store=store)
        async with persistent.session(id='wheel-conversation') as session:
            await session.context.set('customer', 'Ada')
            await session.run('Remember the customer.')
        record = await store.load_session('wheel-conversation')
        assert isinstance(record, StoredSession)
        async with persistent.session(id='wheel-conversation') as session:
            assert session.context.values == {'customer': 'Ada'}
            assert (await session.run('Continue.')).text == 'restored'

    guidance = Guidance()
    for unplaced in (None, 'Role.'):
        try:
            Agent(instructions=unplaced, plugins=[guidance])
        except ConfigError as error:
            assert 'Guidance' in str(error) and 'from_plugins' in str(error)
        else:
            raise AssertionError('Plugin instructions require explicit collection order')
    simple = Agent(instructions='Role.',
                   prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
                   plugins=[guidance])
    assert await simple.prompt.render() == 'Role.\n\nPlugin guidance.'
    ordered = Agent(plugins=[guidance], prompt=SystemPrompt.from_plugins(
        'Prefix.', order=PluginOrder.AS_LISTED))
    assert await ordered.prompt.render() == 'Prefix.\n\nPlugin guidance.'
    try:
        SystemPrompt.from_plugins()
    except TypeError:
        pass
    else:
        raise AssertionError('Plugin order must be explicit')
    async with Agent(model=ScriptedModel([reply('Ready')]),
                     instructions='Fixed.').session() as session:
        handle = await session.use(guidance, section_delivery=SectionDelivery.CONTEXT)
        assert list(session.context.values.values()) == ['Plugin guidance.']
        await session.run('Mounted')
        await handle.aclose()
        assert not session.context.values
        assert await session.prompt.render() == 'Fixed.'
    agent = Agent(model=ScriptedModel([call(Calculator.add, a=19, b=23), reply('42')]),
                  plugins=[Calculator(config=CalculatorConfig())], prompt=SystemPrompt(
                      Instructions(title='Role', text='Fixed instructions.'),
                      format=PromptFormat.MARKDOWN))
    async with agent.session() as session:
        result = await session.run('Add')
        assert result.text == '42'
        assert assert_tool_called(result, 'add', a=19, b=23)[1].content == '42'
        assert await session.prompt.render() == '## Role\n\nFixed instructions.'
    typed = await Agent(model=ScriptedModel([call('final_answer', value=42)])).run(
        'Answer', output=OutputContract('answer.v1', Answer))
    assert isinstance(typed.output, Answer) and typed.output.value == 42
    async with Agent(model=ScriptedModel([call('final_answer', value=43)])).session() as session:
        notifications = [event async for event in session.stream('Answer', output=Answer)]
        assert isinstance(notifications[-1], TurnFinishedEvent)
        assert notifications[-1].result.output.value == 43
    async with Agent(model=ScriptedModel([reply('queued result')])).session() as session:
        handle = await session.submit('Submit')
        assert (await handle.result()).text == 'queued result'
        assert handle.done()
    assert TextDeltaEvent('message', 'hello').type == 'text.delta'
    inline = Skill(name='notes', description='Write notes.', instructions='Keep notes concise.')
    skills = Skills(inline)
    async with Agent(model=ScriptedModel([call('load_skill', name='notes'), reply('Loaded')]),
                     plugins=[skills], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)
                     ).session() as session:
        result = await session.run('Load notes')
        assert 'Keep notes concise.' in result.tool_calls[0][1].text
    assert not any(name.startswith('pydantic_ai') for name in sys.modules)
    assert not any(name in sys.modules for name in ('mkdocs', 'markdown', 'yaml'))

asyncio.run(main())
print('Installed harnessed wheel, metadata, public API, and SDK smoke checks verified.')
"""


def main() -> None:
    uv = os.environ.get("UV_BIN", "uv")
    wheels = tuple((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one wheel with `uv build --no-sources` first.")
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
