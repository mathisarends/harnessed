from time import monotonic

import pytest

from harnessed import Agent, Plugin
from harnessed.hooks import SessionOpened
from harnessed.integrations.json import JsonSessionStore
from harnessed.kernel import PluginScope, hook
from harnessed.models import UserMessage
from harnessed.testing import ScriptedModel


async def test_session_opened_observes_the_restored_context_before_entry(tmp_path):
    openings: list[SessionOpened] = []

    class Clock(Plugin, provides=True):
        started: float

        @hook(SessionOpened)
        def opened(self, event: SessionOpened) -> None:
            self.started = event.at
            openings.append(event)

    agent = Agent(model=ScriptedModel([]), plugins=[Clock()], store=JsonSessionStore(root=tmp_path))
    before = monotonic()
    async with agent.session(id="case") as session:
        first = session.scope[Clock]
        (opened,) = openings
        assert before <= first.started == opened.at <= monotonic()
        assert opened.session is session.plugin_context
        await session.append_message(UserMessage("existing history"))
    async with agent.session(id="case") as restored:
        _, reopened = openings
        assert reopened.session is restored.plugin_context
        assert reopened.session.messages[-1].text == "existing history"
        assert first is not restored.scope[Clock]
        assert first.started <= restored.scope[Clock].started == reopened.at


async def test_open_hook_failure_releases_resources_before_propagating():
    released = []

    class Broken(Plugin):
        async def setup(self, scope: PluginScope) -> None:
            scope.effect(lambda: released.append(True))

        @hook(SessionOpened)
        def fail_open(self, event: SessionOpened) -> None:
            raise ValueError("open failed")

    agent = Agent(model=ScriptedModel([]), plugins=[Broken()])
    with pytest.raises(ValueError, match="open failed"):
        async with agent.session():
            pytest.fail("Session body ran after an opening failure")
    assert released == [True]
