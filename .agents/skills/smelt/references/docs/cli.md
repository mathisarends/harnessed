# CLI and reports

See the [changelog](../CHANGELOG.md) for new capabilities, fixes and migration notes
by release, including changes to agent workflows and rule defaults.
For the schema 1 to 2 transition, see the [upgrade checklist and parser example](upgrading.md#json-report-consumers).

Most rules are opt-in: a rule runs only when `rules` names it or `--select`
selects it. Declared policies describe what a rule checks and do not enable it;
a policy whose rule is named nowhere is a configuration warning. Only
architecture/unknown-layer and suppression/debt diagnostics run by default.
See [rule activation and migration](rule-selection.md) and the
[full catalog](rules/README.md) for the decisions and precedence rules.

Use `smelt explain visibility` to list the visibility family. Select individual
checks by name or by stable code, such as `VIS001`.

```bash
smelt init                           # draft smelt.yml from the code (also uv workspaces)
smelt init --maintainability         # write source/test reviews by individual name
smelt init --member packages/new    # draft one member under an existing uv workspace root
smelt init --config other.yml       # write a config and editor schema in that directory
smelt check                          # whole project
smelt check --changed                # only what the working tree introduced since HEAD
smelt check --staged --fail-on warning  # review the actual commit, including new warnings
smelt check --staged --no-cache       # recompute to verify the cached result
smelt check --changed --base origin/main --format json
smelt check --changed --format concise  # path:line:col CODE family/finding message -> repair
smelt check --statistics              # findings per rule, hints included
smelt check --statistics --by member  # findings per uv workspace member, hints included
smelt check --strict-coverage         # enable architecture/uncovered-source for this run
smelt check --show-coverage --format json  # include the per-file policy inventory
smelt check --select TYP003 --fix --unsafe-fixes  # use None-only defaults after review
smelt check --diff                                # preview the safe fixes
smelt check --diff --suggestions                  # also show suggested edits --fix never applies
smelt context user                   # what applies to a feature, module or path
smelt context app/orders/refund.py --guide  # how to write it: one instruction per active rule
smelt explain ARC001                 # how to enable it, options, status, rationale and examples
smelt rules                          # full catalog with project status and severity
smelt debt                           # record today's violations as known debt
smelt debt --prune                   # drop debt entries that were fixed
smelt debt --show src/app            # inspect stored debt for a file/directory
smelt debt --show --format json      # inspect the grouped version-2 baseline
smelt config show                    # the resolved config
smelt config show --sources --format json  # policy inputs and explicit value origins
smelt config schema                  # the JSON schema of smelt.yml
smelt skill install                  # install the release's official skill in this project
smelt skill install --force          # replace an older installed skill completely
```

In a uv workspace, `init` writes shared root policy and an independent architecture
draft in every Python member. `init --member PATH` writes just that member's
draft. Paths are workspace-relative; `--force` authorizes overwriting the selected
config targets. All targets are checked before any write.

`debt` from a workspace root maintains one baseline per member plus root debt;
from a member it maintains only that member's baseline. `--prune` never creates a
new member scope. Workspace-wide `debt --show --format json` returns one object
with a `files` map when several baselines are selected; one selected file retains
the normal version-2 document. Stored paths remain workspace-relative.

Normal checks are read-only. A finding can carry a fix: concrete text edits
with an applicability. `--fix` applies the `safe` fixes of visible findings,
`--fix --unsafe-fixes` also applies `unsafe` ones, and `display` fixes are
suggestions that are never applied. `--diff` prints the same fixes as a unified
diff without writing and exits `1` when there is one; `--diff --suggestions`
adds the `display` fixes for review. The concise and text formats mark fixable
findings with `[fix]`, `[unsafe fix]` or `[suggestion]` and count them in the
summary; JSON findings include `fix` with `applicability`, `description` and
`edits` (`line`, `column`, `end_line`, `end_column`, `original`, `replacement`,
positions as in the finding). An agent can apply an edit as a string
replacement of `original`. `smelt rules --format json` reports per rule whether
its findings carry a fix (`fix`: `always`, `sometimes` or `none`).

Fixes respect selection, scope, suppressions and accepted debt. Each fix is
applied completely or not at all; a fix whose source changed since the check is
skipped, and the result must parse. A fix that overlaps another one is applied
after the next check, then Smelt checks again. JSON reports include `fixes` with
the number of applied fixes, the modified files and skipped fixes with reasons.
Which rules carry fixes, and when a fix is unsafe or only a suggestion, is part
of each rule page. For example, opt-in [TYP003](rules/TYP003.md) rewrites `parameter or fallback`
to an explicit `is not None` conditional only with `--unsafe-fixes`: falsy
values will be preserved and custom truthiness will no longer be called. Review
intent first. `--unsafe-fixes` alone performs no writes; `--staged` cannot be
combined with `--fix` or `--diff`, and `--suggestions` requires `--diff`.

Exit codes: `0` clean, `1` violations at or above `--fail-on`, `2` config or usage error.
A config entry that points at nothing (a mistyped package, a missing test root) is a
config error, not a silently disabled rule.
Explicit check paths must exist and contain analyzed source or test files; unknown
`--select`/`--ignore` selectors are usage errors. JSON includes the reporting scope, file
count and active rule codes. `--config PATH` works before or after a subcommand.
A stored JSON report explains itself: `scope.mode` is `full`, `changed` or `staged`, and
`comparison` names the requested `base`, the resolved `merge_base`, the
`compared_revision` and `head`, so `passed` from `--changed` reads as "nothing new since
that commit", not as a clean inventory. JSON check reports use `schema_version: 2`.
By default `coverage` contains only the mode, completeness, counts, policy hash,
architecture presence, discovery errors and omitted workspace members. Use
`--show-coverage` for the full policy and per-file inventory. These details include the source and test roots,
workspace members that are not analyzed, module counts per classification, the
composition-root and wiring exemptions, third-party policies per layer, the import
settings (`type_checking`, `transitive`, `cycles`), cross-feature allowances as fields,
and a `policy_hash` that is equal for reports checked under the same resolved config.
The report's `project` names the config, project root and repository root. JSON
finding paths are project-relative; SARIF and GitHub annotation paths are rebased
to the repository root, including when using a nested `--config`.
Findings also carry `member`: a uv workspace member directory, `.` for workspace
root files, or `null` outside a workspace or for findings without a file location.
In text and concise output, `--statistics --by member` counts every finding,
including hints, under its member. Unattributed findings use `<project>`.
`--by member` requires `--statistics`; the default is `--by rule`.
`smelt context TARGET --format json` carries the same import settings,
allowances and hash. `style` describes effective source conventions for the
target. `rule_details` describes resolved rules, including their severity,
activation source and any exception reason. For production targets, the text
view focuses on the source policy. It presents package APIs separately from
style settings. Use `smelt config show --sources` to trace inheritance and
review the root and member files together.

`smelt context TARGET --guide` turns the same policy into instructions for
writing the target, without analyzing the project. Each active rule contributes
one line, rendered from the options that govern the target: the layers it may
import, its positional-parameter limit, the required dataclass options and so
on. Rules whose policy does not reach the target are left out. Errors, warnings
and hints are listed separately; hints are review prompts that apply only where
their case occurs. A source target also lists the rules for its mirrored test
file, and a test path lists only test rules. `--format json` returns `target`,
`layer`, `test_path` and `rules`, each with `code`, `rule`, `family`,
`severity`, `applies_to` (`source` or `tests`) and `instruction`.
With `--format json`, an exit `2` prints a JSON document as well, so an agent needs no
text parser: `{"schema_version": 2, "status": "error", "error": {...}}` with `kind`
(`usage`, `config` or `analysis`), `message`, the rejected `input` (`option`, `value`)
and, for config errors, the `config` file with each issue's YAML `location`. That covers
argument errors (`--fail-on bogus`, an unknown flag) whenever `--format json` is on the
command line; an invalid `--format` value itself is reported as argparse text.

Schema 2 moves repeated visibility-analysis boundaries from each finding's
`expected.usage_scope` into the report's `usage_scopes` array; findings reference
it with `expected.usage_scope_index`. Finding identities and debt fingerprints are
unchanged by presentation grouping. Text groups related late-import LAY004
findings with their affected lines; JSON retains every finding and adds an
`ordering` group. Text and GitHub output show usage limits once; SARIF retains
them in run properties as `usageScopes`. Text omits empty detail fields.
Text and concise output list a repair repeated verbatim by several findings of
one rule once, below the findings; JSON keeps the hint on every finding.
Projects without architecture configuration have no policy-completeness banner
unless strict coverage is requested. Coverage checks and exit codes still apply.
Concise output omits usage limits and shows the policy-coverage line only when
coverage is incomplete.

Shared TST008 helper findings add `shared_helper` groups with the helper identity,
the exact operation location, one repair and every affected test/call line. Text
and concise output show the repair once. JSON retains the individual findings;
totals, exit behavior, debt identities and suppression scope do not change.
Only visible, compatible findings form a group. Distinct operations in one helper
remain separate, including operations on the same physical line.

TST006 includes `helper_review` call locations and known inference-limit reasons.
An imported body being unassessed does not mean it lacks assertions. Existing
`tests.assertion_helpers` can express a trusted assertion-helper contract; no
additional helper bodies are inferred. ARC004 includes `graph_kind` and
`witness_edges` with each selected import's phase. These are additive schema-2
fields; existing finding identities and default rule activation are preserved.

Valid configurations can also produce warnings, such as cyclic `may_depend_on`
policies. Reports retain these separately from rule violations in JSON
`configuration_diagnostics` (config path, key, severity and message) and SARIF
`toolConfigurationNotifications`. Text and GitHub output include them too.
They count toward the warning total and cause exit `1` with `--fail-on warning`.
Configuration warnings describe the current policy even in scoped/changed checks;
rule selection, source suppressions and accepted debt do not remove them.

`--changed` checks the base commit (HEAD, or the merge-base with `--base`) with today's
config and reports only violations that are new, wherever they show up. Old violations in
an edited file stay quiet, a new import cycle does not.
Changing an import's comment, formatting or alias does not make its existing boundary
violation new; an additional identical violating import still counts as new.

## Agent skill

Every wheel and source distribution includes the official `smelt` skill, its
offline user guides and complete generated rule reference. No
source checkout or documentation download is needed:

```bash
smelt skill install
smelt skill install --destination .codex/skills/smelt
smelt skill install --force
smelt skill install --destination .codex/skills/smelt --force
```

`--destination` is the exact skill directory, relative to the current directory
or absolute; the default is `.agents/skills/smelt`. Installation does not require
`smelt.yml`. It creates missing parents and writes a `VERSION` file identifying
the installing CLI release. This is a project-local install by default; choose
your agent's user-level skill directory explicitly if you want a global install.

An existing destination is an error unless `--force` is supplied. A forced update
replaces the whole skill, including local edits and obsolete references, without
changing sibling skills. It refuses linked destinations and existing paths that
are not skill directories (a directory containing `SKILL.md`). The new bundle is
copied before replacement, and a failed replacement restores the previous skill.
Keep project-specific additions in the project's own instructions.

After upgrading `smelt-cli`, rerun the installation command with `--force` and the
same destination. Package upgrades do not silently rewrite installed skills.
Successful installation exits `0`; destination and filesystem errors exit `2`.
Use `smelt config schema` to print the complete editor schema from the installed
release, or `smelt init` to write it alongside a new policy.

See the [upgrade guide](upgrading.md) for `smelt config migrate`.
