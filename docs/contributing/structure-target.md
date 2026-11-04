# Implemented ownership and composition boundaries

The plugin-recipe design is implemented. Future API proposals belong in
[the roadmap](roadmap.md), rather than this description of current boundaries.

| Layer | Responsibility |
| --- | --- |
| `kernel.dependencies` | Typed stable dependency descriptors |
| `kernel.declarations` / `spec` | Repeatable recipe construction; normalized requirements and observations |
| `kernel.plugin` / `context` | Scoped binding visibility, activation replacement and owned cleanup |
| `core.plugin` | Generic typed configuration and frozen instances, independent event declarations, normalized prompt contributions |
| `core.composition` | PluginPack selection, override provenance and named fragment membership |
| `core.prompt` | Explicit instruction coverage, placement, rendering and runtime context delivery |
| `core.tools` | Declarative recipe references and activation-bound prepared calls |
| `agent.py` | Resolve composition before collecting contributions and creating sessions |
| `testing.plugins` | Model-free typed mounts with fake services and cleanup |

The kernel remains standard-library-only. Higher layers use its public facade.
The root SDK remains lazy, with matching explicit runtime exports and type stubs.
See [implementation decisions](plugin-recipes.md) for the concrete ownership and
fragment rules and [Smelt policy](smelt.md) for their executable constraints.
