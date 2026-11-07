import pytest

from harnessed import Agent, Instructions, Plugin, PluginOrder, PromptFormat, SystemPrompt, tool
from harnessed.errors import ConfigError
from harnessed.kernel import Scope
from harnessed.plugins import ContextValue
from harnessed.testing import ScriptedModel


class Observe(Plugin):
    def instructions(self) -> SystemPrompt:
        return SystemPrompt(
            Instructions(title="Page state", text="DOM snapshot."),
            Instructions(title="Freshness", text="Use the latest observation."),
        )


def test_prompt_description_labels_dynamic_context() -> None:
    agent = Agent(plugins=[ContextValue("user", "Prefers Python")])
    assert str(agent.prompt) == (
        "System prompt (plain, 0 sections)\n"
        "Context (dynamic, outside the system prompt)\n"
        "  - user: 'Prefers Python'"
    )


@pytest.mark.parametrize(
    ("format", "expected"),
    [
        (PromptFormat.PLAIN, "Role.\n\nDOM snapshot.\n\nUse the latest observation."),
        (
            PromptFormat.MARKDOWN,
            (
                "Role.\n\n## Page state\n\nDOM snapshot."
                "\n\n## Freshness\n\nUse the latest observation."
            ),
        ),
        (
            PromptFormat.XML,
            (
                '<section>\nRole.\n</section>\n\n<section title="Page state">'
                '\nDOM snapshot.\n</section>\n\n<section title="Freshness">'
                "\nUse the latest observation.\n</section>"
            ),
        ),
    ],
)
async def test_explicit_collection_renders_complete_blocks(format, expected):
    agent = Agent(
        instructions="Role.",
        plugins=[Observe()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=format),
    )
    assert await agent.prompt.render() == expected
    assert "Observe.instructions" in str(agent.prompt)
    async with Agent(
        model=ScriptedModel([]),
        instructions="Role.",
        plugins=[Observe()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED, format=format),
    ).session() as session:
        assert await session.prompt.render() == expected


def test_plugin_order_defaults_to_configured_order_and_is_validated():
    assert SystemPrompt.from_plugins().plugin_order is PluginOrder.AS_LISTED
    with pytest.raises(ValueError):
        SystemPrompt.from_plugins(order="setup")


@pytest.mark.parametrize("prompt", [None, SystemPrompt("Application text.")])
def test_missing_collection_fails_before_configuration_providers_run(prompt):
    class Feature(Plugin):
        def instructions(self) -> SystemPrompt:
            pytest.fail("No implicit instruction collection")

    with pytest.raises(ConfigError, match="from_plugins.*Feature"):
        Agent(instructions="Role.", plugins=[Feature()], prompt=prompt)


def test_only_selected_configuration_is_evaluated_once():
    calls = []

    class Feature(Plugin):
        def instructions(self) -> SystemPrompt:
            calls.append(self)
            return SystemPrompt(str(len(calls)))

    feature = Feature()
    agent = Agent(plugins=[feature], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))
    assert calls == [feature]
    assert agent.prompt.entries[0].section.text == "1"


async def test_configuration_is_not_recomputed_per_session():
    calls = []

    class Feature(Plugin):
        def instructions(self) -> SystemPrompt:
            calls.append(self)
            return SystemPrompt("Configured.")

    agent = Agent(
        model=ScriptedModel([]),
        plugins=[Feature()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    async with agent.session() as first:
        assert await first.prompt.render() == "Configured."
    async with agent.session() as second:
        assert await second.prompt.render() == "Configured."
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["text", None, Instructions(text="text"), 42])
def test_plugin_provider_must_return_a_document(value):
    class Invalid(Plugin):
        def instructions(self):
            return value

    with pytest.raises(TypeError, match="Invalid.instructions.*SystemPrompt"):
        Agent(plugins=[Invalid()], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))


def test_plugin_provider_cannot_request_recursive_collection():
    class Invalid(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED)

    with pytest.raises(ValueError, match="must return content"):
        Agent(plugins=[Invalid()], prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))


async def test_none_is_normalized_and_nested_documents_inherit_outer_format():
    agent = Agent(
        prompt=SystemPrompt(
            None,
            SystemPrompt(Instructions(title="Nested", text='a < b & "c"'), format=PromptFormat.XML),
            None,
            format=PromptFormat.MARKDOWN,
        )
    )
    assert len(agent.prompt.entries) == 1
    assert await agent.prompt.render() == '## Nested\n\na < b & "c"'
    assert SystemPrompt(None, "A", None).sections == ("A",)


async def test_xml_escaping_and_empty_sections():
    agent = Agent(
        prompt=SystemPrompt(
            Instructions(title='A & "B"', text='a < b & "c"'),
            Instructions(title="Empty", text=""),
            "Untitled.",
            format=PromptFormat.XML,
        )
    )
    assert await agent.prompt.render() == (
        '<section title=\'A &amp; "B"\'>\na &lt; b &amp; "c"\n</section>'
        "\n\n<section>\nUntitled.\n</section>"
    )


def test_collection_only_belongs_to_outer_document():
    with pytest.raises(ValueError, match="outermost"):
        SystemPrompt(SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED))


@pytest.mark.parametrize("value", [42, ("text",), ["text"]])
def test_invalid_parts_fail_at_document_construction(value):
    with pytest.raises(TypeError, match="SystemPrompt sections"):
        SystemPrompt(value)


def test_instruction_validation_and_removed_constructor_options():
    with pytest.raises(TypeError):
        Instructions("text")
    with pytest.raises(TypeError):
        Instructions(text=42)
    with pytest.raises(ValueError):
        Instructions(title="Two\nlines")
    with pytest.raises(TypeError):
        SystemPrompt(exclude=())
    with pytest.raises(TypeError):
        SystemPrompt(unplaced="append")
    with pytest.raises(TypeError):
        Agent(system_prompt="Old keyword")
    with pytest.raises(TypeError, match="instructions"):
        Agent(instructions=SystemPrompt("Wrong type"))


async def test_content_values_have_no_reference_identity_or_implicit_deduplication():
    section = Instructions(text="Same.")
    agent = Agent(prompt=SystemPrompt(section, section, "Same."))
    assert await agent.prompt.render() == "Same.\n\nSame.\n\nSame."


async def test_prompt_order_is_independent_of_dependency_setup_order():
    started = []

    class Backend:
        pass

    class Consumer(Plugin, needs=(Backend,)):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Consumer first.")

        async def setup(self) -> None:
            started.append("consumer")

    class Provider(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt("Provider second.")

        async def setup(self, scope: Scope) -> None:
            scope.provide(Backend, Backend())
            started.append("provider")

    agent = Agent(
        model=ScriptedModel([]),
        plugins=[Consumer(), Provider()],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    async with agent.session() as session:
        assert started == ["provider", "consumer"]
        assert await session.prompt.render() == "Consumer first.\n\nProvider second."


def test_async_instruction_providers_are_rejected_at_class_definition():
    with pytest.raises(TypeError, match="synchronous"):

        class Invalid(Plugin):
            async def instructions(self) -> SystemPrompt:
                return SystemPrompt("Invalid.")


def test_instruction_provider_name_is_reserved_for_content():
    with pytest.raises(TypeError, match="reserved"):

        class Invalid(Plugin):
            @tool(readonly=True)
            def instructions(self) -> SystemPrompt:
                return SystemPrompt("Not a tool.")


async def test_additive_plugin_instances_keep_all_blocks_in_list_order():
    class Named(Plugin):
        def instructions(self) -> SystemPrompt:
            return SystemPrompt(self.name)

    agent = Agent(
        plugins=[Named(name="one"), Named(name="two"), Named(name="one")],
        prompt=SystemPrompt.from_plugins(order=PluginOrder.AS_LISTED),
    )
    assert await agent.prompt.render() == "one\n\ntwo\n\none"
