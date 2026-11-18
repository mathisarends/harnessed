# Implemented ownership and composition boundaries

The plugin-recipe design is implemented. Future API proposals belong in
[the roadmap](roadmap.md), rather than this description of current boundaries.

| Layer | Responsibility |
| --- | --- |
| `kernel.dependencies` | Typed stable dependency descriptors |
| `kernel.declarations` / `spec` | Repeatable recipe construction; normalized requirements and observations |
| `kernel.plugin` / `scope` | Scoped binding visibility, activation replacement and owned cleanup |
| `runtime.plugin` | Generic typed configuration and frozen instances, independent event declarations, normalized prompt contributions |
| `runtime.composition` | PluginPack selection, override provenance and named fragment membership |
| `prompts` | Explicit instruction coverage, placement, rendering and runtime context delivery |
| `tools` | Declarative recipe references and activation-bound prepared calls |
| `events` | Domain event values and their enums, independent of runtime orchestration |
| `models` | Provider-independent request, message, usage and diagnostic contracts |
| `sessions` | Session log, projections, checkpoints and stores |
| `sessions.result` | Typed turn result and result assembly from durable records |
| `agent.py` | Resolve composition before collecting contributions and creating sessions |
| `testing.plugins` | Model-free typed mounts with fake services and cleanup |

The kernel remains standard-library-only. Higher layers use its public facade.
The root SDK remains lazy, with matching explicit runtime exports and type stubs.
Event consumers import the explicit `harnessed.events` facade. Internally, each
domain owns its durable and transient events. `events.unions` assembles the
durable catalog; `events.stream` assembles the live catalog, including the terminal
result event. Keeping these catalogs separate lets result validation
use the durable codec without a dependency cycle through the terminal event.
`runtime.streaming` projects records for UIs; `runtime.stream_approvals`
owns live approval waits. Neither mechanism belongs to the event vocabulary.
See [implementation decisions](plugin-recipes.md) for the concrete ownership and
fragment rules and [Smelt policy](smelt.md) for their executable constraints.
