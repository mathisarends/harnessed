# smelt

**Your Python style guide, enforced by code.**

Every team has a style guide: application code doesn't import the database,
tests mirror the source tree, internals stay private, `None` checks are
explicit. Usually it lives in a wiki or an `AGENTS.md`, and code review enforces
it by hand. Coding agents skim it, so reviewers write the same comment again and
again.

Smelt turns that guide into checks. Each convention is one line in `smelt.yml`,
and Smelt enforces it before an edit, after it, at commit time and in CI, with
a finding that says what broke and how to fix it.

**The style guide in prose:**

```text
Application code must not depend on infrastructure. Use a port.
```

**The same rule in `smelt.yml`:**

```yaml
architecture:
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}

rules:
  architecture/layer-boundary: error
```

**And what an agent sees when it breaks it:**

```text
src/shop/application/checkout.py:2:42  ARC001 architecture/layer-boundary  [error]
  application must not depend on infrastructure
    from shop.infrastructure.database import SqlOrderRepository
  Allowed: application → domain
  Hint: Move what application needs into a layer it may depend on (domain), or invert
        the dependency behind an abstraction.
```

## Why Smelt?

- **The config is the style guide.** A rule runs only when `smelt.yml` names it,
  so the file is the complete, reviewable list of your conventions.
- **Architecture, tests and code style.** Layers, features, package APIs,
  mirrored tests, test quality, typing, visibility and modeling conventions.
- **Built for agents.** `smelt context` tells an agent the rules for a file
  before it writes it; `--changed` and `--staged` report only what it introduced.
- **Adoptable.** A debt baseline lets an existing codebase fail only on new
  violations.

## Get started

```bash
uv tool install smelt-cli   # Python 3.12+
smelt init                  # draft smelt.yml from your layout
smelt check                 # enforce it
```

Upgrading to 0.16.1? See the [upgrade guide](docs/upgrading.md#016-to-0161).

| You want to… | Command |
| --- | --- |
| Check the project | `smelt check --fail-on warning` |
| Check only your changes | `smelt check --changed` |
| Check the commit | `smelt check --staged --fail-on warning` |
| Get the rules for a file before writing it | `smelt context src/shop/application/refunds.py --guide` |
| Understand a finding | `smelt explain ARC001` |
| Find rules you haven't decided on | `smelt rules --unconfigured` |
| Accept existing findings | `smelt debt` |
| Give agents the offline reference | `smelt skill install` |

## Enforce it at commit time

With [prek](https://prek.j178.dev/installation/), add this to
`.pre-commit-config.yaml` and run `prek install`:

```yaml
repos:
  - repo: local
    hooks:
      - id: smelt-staged
        name: smelt
        entry: smelt check --staged --fail-on warning
        language: python
        additional_dependencies: [smelt-cli==0.16.1]
        pass_filenames: false
        always_run: true
        require_serial: true
```

Run `smelt check --fail-on warning` in CI. See [integrations](docs/integrations.md).

## Documentation

| Level | Guide | You learn |
| --- | --- | --- |
| 1 | [First check](https://github.com/mathisarends/smelt/blob/main/docs/01-first-check.md) | Write the first rules, read and act on findings |
| 2 | [Architecture boundaries](https://github.com/mathisarends/smelt/blob/main/docs/02-architecture-boundaries.md) | Layers, features, composition root, third-party imports |
| 3 | [Tests that mirror the source](https://github.com/mathisarends/smelt/blob/main/docs/03-test-layout.md) | Test layout and test quality rules |
| 4 | [The agent loop](https://github.com/mathisarends/smelt/blob/main/docs/04-agent-loop.md) | `context`, changed and staged checks, hooks, CI |
| 5 | [Existing codebases](https://github.com/mathisarends/smelt/blob/main/docs/05-existing-codebases.md) | Debt baselines and strict policy coverage |
| 6 | [Scaling the policy](https://github.com/mathisarends/smelt/blob/main/docs/06-scaling-the-policy.md) | Package APIs, shared configs, uv workspaces |

Reference: [configuration](docs/configuration.md), [rule catalog](docs/rules/README.md),
[CLI](docs/cli.md), [all docs](https://github.com/mathisarends/smelt/blob/main/docs/README.md), [changelog](CHANGELOG.md),
[contributing](https://github.com/mathisarends/smelt/blob/main/CONTRIBUTING.md).

Smelt analyzes source statically and never runs your code. Use it alongside
tests, a type checker and Ruff. See [scope and limits](https://github.com/mathisarends/smelt/blob/main/docs/vision.md).
