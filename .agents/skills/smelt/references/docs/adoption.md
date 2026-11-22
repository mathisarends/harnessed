# Adopting Smelt in an existing project

A first check often reports dozens of findings that come down to a few decisions. Each
import finding carries an `edge` in JSON (`"billing.application -> voice.domain"`; for
cycles, the cycle), and the text output ends with the edges behind several findings.
Settle each edge once: fix the code, or record an intended dependency as policy, e.g. a
`cross_feature.allow` entry.
JSON also lists `groups`, sorted by size: a `facade` group collects the indirect
findings one module relays (its count is what narrowing that facade's exports would
address), an `edge` group counts `direct` and `transitive` findings of one dependency, a
`cycle` group carries the witness import path, and a `test_move` group the TST001 files
moving between the same directories. Groups cite findings by their `id`; every finding
stays in `violations`, so a group is one decision, not one more defect.

Accept what remains with a baseline:

`smelt debt` records today's violations in `.smelt/debt.json` and sets `debt:` in
`smelt.yml`. `smelt check` then fails only on new violations, and MET003 reports entries
that were fixed and can leave the file (`smelt debt --prune`).
Existing debt files remain readable. Run `smelt debt --prune` once before editing imports
covered by an older baseline to upgrade their fingerprints; pruning accepts no new debt.
New import fingerprints survive comments, aliases and formatting changes.

### Workspace debt

In a uv workspace, each member can set `debt: .smelt/debt.json` in its own
`smelt.yml`. The file path is member-relative; paths and fingerprints inside it
remain workspace-relative. A member baseline accepts findings only inside that
member. The closest member baseline takes precedence over root debt; root debt
covers the remaining files. An entry copied to another member's file cannot
suppress that member's finding.

`smelt debt` at the root records one baseline per Python member plus a root
baseline. From a member directory, or with its config selected, it records only
that member's debt and adds the member-relative `debt` key there. Missing member
configs are created as policy fragments inheriting the root. This remains an
explicit acceptance operation; it never records hints or strict-coverage gaps.

`--prune` operates on configured scopes and accepts no new findings. It never
creates an unconfigured member baseline, and preserves entries for inactive or
hint-only rules. A member invocation does not prune other members or root debt.
`--show` is read-only; a member without its own baseline can inspect its entries
in legacy root debt. Workspace-wide JSON inspection groups multiple selected
documents under `files`, keyed by workspace-relative baseline filename.

### Grouped baselines and inspection

New writes use debt JSON version 2. Rule, path, message and an optional definition
scope appear once per group; `fingerprints` contains every accepted matching token.
One aggregate finding can have several tokens: ARC008 tracks individual importers,
LAY004 individual ordering inversions, and ARC004 individual cyclic edges. Repeated
fingerprints retain their multiplicity. A group is not a blanket allowance for
future imports or inversions, and replacing an accepted edge with a new one still
reports the new edge. Report JSON remains schema 2; configuration remains version 1.

```json
{
  "version": 2,
  "violations": [
    {
      "code": "ARC008",
      "path": "app/browser/base.py",
      "message": "BrowserEvents is missing from the package API",
      "scope": "app.browser.base.BrowserEvents",
      "fingerprints": ["accepted-importer-a", "accepted-importer-b"]
    }
  ]
}
```

The identifiers above are illustrative; Smelt writes stable hashed fingerprints.
Version-1 files load with their original counts. `smelt debt --prune` writes the
grouped format while preserving unassessed entries for inactive or hint-only rules.
Older Smelt versions cannot read version 2; update local, hook and CI installations
together before committing an upgraded baseline.

Inspect stored debt without analyzing source or changing the baseline/configuration:

```bash
smelt debt --show                     # compact text, without hashes
smelt debt --show app/browser         # filter by file or directory
smelt debt --show app/browser --format json
```

Paths are relative to the invocation directory. Text lists one rule/path/scope
and message per group, with its accepted-entry count. JSON exposes the complete
grouped data and multiplicity. This is the stored baseline, including historical
messages and unassessed rules; use `smelt check app/browser --format concise` for
current findings. `--show` and `--prune` are mutually exclusive. Paths and `--format`
are inspection options only, so recording/pruning always assesses the full project.

TYP003 now includes the enclosing function's qualified name in its identity,
distinguishing methods in different classes and nested functions while surviving
line shifts. A fixed occurrence cannot pay for an identical new occurrence in
another function. Old unscoped TYP003 fingerprints cannot establish which function
was accepted, so those findings require review and explicit re-recording with
`smelt debt`. `--prune` removes the old tokens without accepting the current findings.

### Architecture edge identities

Cycle debt records each cyclic architecture edge, so a new edge cannot hide behind
an existing shortest-cycle witness. Indirect import findings identify the final
forbidden target, rather than only the facade. Older cycle and indirect-import debt
may therefore reappear for review: the old fingerprints cannot establish the full
dependency they accepted. `smelt debt --prune` removes stale entries without accepting
these findings; review them before explicitly recording new debt.

Override severities by rule name or code, e.g. `rules: {architecture/unclassified-module: off}`.

Silence a single finding inline, always with a reason:

```python
from gateway.infra.sql import Repo  # smelt: ignore[architecture/layer-boundary] -- migration tracked in #123
```

Rule names keep the exception readable in review; stable codes such as `ARC001`
and code prefixes work too.

Comments within a logical statement cover that statement,
including its continuation lines, so formatting a long call or signature does
not invalidate the exception. The scope ends before the next statement; a
comment on a function signature does not cover its body. Comments beside an
individual name inside a multiline import stay local to that name's line, so
neighboring private imports still receive review.

Every rule has a page under [docs/rules](https://github.com/mathisarends/smelt/blob/main/docs/rules). `smelt init` writes a local
editor schema with completion, validation and bad/good hover examples derived
from that same catalog. See [editor setup](configuration.md#editor-completion-hover-and-validation).
