"""Opt-in fixtures: pytest_plugins = ['harnessed.testing.pytest_plugin']."""

import pytest

from harnessed.agent import Agent
from harnessed.kernel import as_plugin_spec
from harnessed.prompts import SystemPrompt

from .models import ScriptedModel


@pytest.fixture
def harnessed_session():
    """Open a real session using a script and the configured public plugins."""

    def open_session(
        agent: Agent | None = None,
        *,
        script=(),
        plugins=(),
        state=None,
        instructions: str | SystemPrompt | None = None,
    ):
        plugins = tuple(plugins)
        replaced = {slot for value in plugins if (slot := as_plugin_spec(value).slot) is not None}
        configured = (
            tuple(value for value in agent.plugins if as_plugin_spec(value).slot not in replaced)
            if agent is not None
            else ()
        )
        app = Agent(
            model=ScriptedModel(script),
            instructions=agent.instructions
            if agent is not None and instructions is None
            else instructions,
            approvals=agent.approvals if agent is not None else None,
            context=agent.context if agent is not None else None,
            loop=agent.loop if agent is not None else None,
            plugins=(*configured, *plugins),
        )
        return app.session(state=state)

    return open_session
