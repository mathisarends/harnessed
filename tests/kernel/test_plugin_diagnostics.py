from harnessed.kernel import Key, PluginBase, PluginStatus, Scope, plugin


async def test_diagnostics_preserve_service_identity_and_allow_later_activation():
    first, second = Key[str]("backend"), Key[str]("backend")
    mounted = []

    @plugin(needs=(first, second))
    async def feature(scope: Scope):
        mounted.append((scope[first], scope[second]))
        yield

    async with Scope() as scope:
        (handle,) = await scope.install([feature()], settle=True)
        issues = scope.plugin_diagnostics()
        assert len(issues) == 1
        assert issues[0].plugin is handle.spec
        assert issues[0].missing == (first, second)
        assert handle.status is PluginStatus.WAITING and mounted == []

        scope.provide(first, "one")
        await scope.settle()
        assert scope.plugin_diagnostics()[0].missing == (second,)
        assert issues[0].missing == (first, second)

        scope.provide(second, "two")
        await scope.settle()
        assert scope.plugin_diagnostics() == ()
        assert handle.status is PluginStatus.ACTIVE and mounted == [("one", "two")]


async def test_diagnostics_include_nested_installs_and_respect_scope_visibility():
    backend = Key[str]("backend")

    @plugin(needs=(backend,))
    async def feature(scope: Scope):
        yield

    @plugin
    async def owner(scope: Scope):
        await scope.plugin(feature())
        yield

    async with Scope() as scope:
        (handle,) = await scope.install([owner()], settle=True)
        assert [issue.plugin.name for issue in scope.plugin_diagnostics()] == ["feature"]
        async with scope.extend() as child:
            child.provide(backend, "child only")
            await child.install([feature()], settle=True)
            assert child.plugin_diagnostics() == scope.plugin_diagnostics()
            assert len(child.plugin_diagnostics()) == 1
            assert scope.plugin_diagnostics()[0].missing == (backend,)
        await handle.aclose()
        await scope.settle()
        assert scope.plugin_diagnostics() == ()


async def test_diagnostics_include_transitively_waiting_declared_providers():
    backend = Key[str]("backend")
    capability = Key[object]("capability")

    class Provider(PluginBase, provides=capability, needs=(backend,)):
        pass

    class Consumer(PluginBase, needs=(capability,)):
        pass

    async with Scope() as scope:
        await scope.install([Provider(), Consumer()], settle=True)
        issues = scope.plugin_diagnostics()
        assert [(issue.plugin.name, issue.missing) for issue in issues] == [
            ("Provider", (backend,)),
            ("Consumer", (capability,)),
        ]
        scope.provide(backend, "ready")
        await scope.settle()
        assert scope.plugin_diagnostics() == ()
