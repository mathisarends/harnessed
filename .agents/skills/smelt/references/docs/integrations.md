# Agents, hooks and CI

## Python versions

Smelt runs on Python 3.12 to 3.14. Its native core parses the newest Python grammar
whichever Python runs smelt, so 3.14's `except A, B:` or t-strings check on 3.12 too.

The installed command checks in a compiled native core. A checkout or source
distribution requires Rust 1.98+ and a platform linker; release wheels include
the compiled core.


## Using Smelt with coding agents

Add this to your `AGENTS.md` or `CLAUDE.md`:

```md
Before implementing, run `smelt context <feature-or-path>` to see what applies
and `smelt context <path> --guide` for the instructions to write the file by.
After editing, run `smelt check --changed --format concise` and fix new findings.
Use `smelt explain <code>` when unsure, then run focused tests. Stage the intended
changes and run `smelt check --staged --fail-on warning` and `prek run --all-files`.
If a hook changes files or fails, inspect its output,
fix the cause, and rerun the checks until they pass. Review and stage any hook
changes before committing. Do not relax policy or record debt just to pass.
```

If your project adopts strict coverage, add `--strict-coverage` to the check.
It is a project-wide gate even when reporting only changed findings. Changed
checks default to comparison with `HEAD`, so they include staged and unstaged
work but do not compare existing commits with the target branch; use
`--changed --base origin/main` for that comparison and make sure the base is
available locally. CI should still run the full check. Use tests and a type
checker as appropriate for the change.


## prek hooks

Both published hooks run on every commit so edits to shared local policy files
are checked even when no Python or workspace config file changed. A base file
can affect unchanged modules. Keep `always_run: true` in local hook definitions
when using [shared configuration](configuration.md#shared-local-policy).

Install [prek](https://prek.j178.dev/installation/) and add the hook to
`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: smelt-staged
        name: smelt (new staged findings)
        entry: smelt check --staged --fail-on warning
        language: python
        additional_dependencies: [smelt-cli==0.18.0]
        pass_filenames: false
        always_run: true
        require_serial: true
```

Run `prek install` to enable the commit hook and `prek run --all-files`
to run the hooks. This isolated Python environment installs the pinned PyPI wheel,
which contains the native client, without building Smelt from Git. Wheels are
published for Linux x86_64/aarch64, Windows x86_64 and macOS x86_64/aarch64;
other platforms can require a source build and Rust. The staged hook checks the
Git index, including partial staging, policy edits and deletions, and fails on
new warnings and errors. It also runs with `--all-files`, but still checks the
index; run `smelt check` separately for a full working-tree check. Other hooks
can modify files; inspect their changes, rerun and stage the result.

For most projects, run the staged hook on each commit and the full check in CI.
A staged policy or API edit can introduce a finding in an unchanged caller, so
the hook compares findings across the project. If every existing warning must
block each commit, use the published `id: smelt` with `args: [--fail-on, warning]`
or change the local hook entry above to `smelt check --fail-on warning`. Keep
`pass_filenames: false` and `always_run: true` for both policies. To estimate
latency for your project, measure a full check after an API edit as well as an
identical retry. The [full-policy edit benchmark](https://github.com/mathisarends/smelt/blob/ceadc918696dbe4b11c9e9f0d1d6240977161c6a/docs/benchmarks/full-policy-edit-2026-11-19.json)
records these workloads on the Smelt corpus.

Projects already depending on Smelt can instead use a local `language: system`
hook with `entry: uv run --locked smelt check --staged --fail-on warning`.
Use the project environment when `style.third_party_reexports` lists packages
for IMP004. Smelt can inspect only the packages available to its interpreter.

To install from source, use `repo: https://github.com/mathisarends/smelt`,
`rev: v0.18.0` and `id: smelt-staged` (or `id: smelt` for full checks). This source
installation requires Rust 1.98+ and a platform linker. Use the release's
matching `v`-prefixed Git tag. See the [agent workflow](https://github.com/mathisarends/smelt/blob/main/docs/agent-workflow.md) for
performance and limitations.

prek uses the [pre-commit hook format](https://pre-commit.com/#creating-new-hooks).
The published `.pre-commit-hooks.yaml` manifest keeps the required filename.
prek and pre-commit both read `.pre-commit-config.yaml`; prek also accepts
`.yml`. Existing upstream pre-commit integrations can continue using the same
published hooks.


## GitHub Actions

After checkout, run the project's hooks and a full Smelt check. This
example pins Smelt to the same version as the hook above; update both together.
Install prek in the CI environment before running it (or use `uvx prek run`).

```yaml
- uses: astral-sh/setup-uv@v7
- run: prek run --all-files --show-diff-on-failure
- run: uvx --from smelt-cli==0.18.0 smelt check --format github
# optional: code scanning
- run: uvx --from smelt-cli==0.18.0 smelt check --format sarif > smelt.sarif || true
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: smelt.sarif
```
