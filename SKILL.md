# spec-receipt — agent skill

Receipt a repository's design spec against its
declared implementation surfaces before trusting
either. Use it whenever a task involves a repo that
carries a spec — reviewing whether an implementation
still matches its design, auditing drift before a
release, or checking a spec'd codebase you did not
write — when the question is not "is it dangerous?"
(skill-vet) or "does the package do what its
marketing claims?" (skill-claims) but "does the code
still have the surfaces the spec promises, and what
did it grow that the spec never mentioned?"

It pairs with
[skill-claims](https://github.com/jasveerai88/skill-claims):
claims audits a skill package's prose promises
against its static behaviour; receipt audits a
repo's engineering spec against its declared
implementation surfaces. Both are read-only, local,
stdlib-only, and never execute the target.

## The one command

```
spec-receipt check <repo-dir> [--spec PATH ...] [--json] [--fail-on LIST]
```

(From a source checkout, the same command is
`python3 src/spec_receipt.py check <repo-dir> …`.)

- The argument is the repo's root folder. One repo
  per run.
- Which file is the spec, in priority order:
  explicit `--spec` arguments; a
  `.spec-receipt.toml` manifest in the repo root
  listing `specs = [...]`; auto-discovery
  (`SPEC.md`/`spec.md` at root, `docs/spec*.md`,
  `docs/SPEC.md`, `specs/**/spec.md`, `DESIGN.md`,
  `API.md`). No spec found → exit 2.
- Exit codes: **0** no failing verdict, **1** drift
  (≥ 1 MISSING or CONTRADICTED; UNDOCUMENTED and
  UNTRACED fail only when promoted via
  `--fail-on undocumented,untraced`), **2** input
  error. In `--json` mode every error — including
  argument errors — is one JSON object with an
  `error` key. Never a traceback.
- **Read-only, and it executes nothing.** The repo
  is read as Markdown text and Python AST only: no
  file is run, imported, compiled, or evaluated.
  Its own source imports no network or subprocess
  module. Safe to point at a repo you actively
  distrust.

## Scope (v1.0.0)

This release indexes five surface classes: CLI flags
and commands (`argparse` and `click`); HTTP routes
(Flask and FastAPI decorators, Django `path()` /
`re_path()` in `urls.py` — Django routes index as
ANY-method, since the URL layer declares no
method); config keys (example/config-named
`.toml` / `.ini` / `.json` files plus `config[...]`
/ `config.get(...)` reads); and environment
variables (`os.environ[...]`, `os.getenv(...)`,
`os.environ.get(...)`). Spec items extracted: CLI
flag declarations (with stated defaults / choice
sets), CLI command declarations (`## Command: name`
headings, backticked invocation lines, invocation
lines in fenced blocks), flag negations,
requirement IDs (`FR-###` / `REQ-<word>`
/ `R-##`), endpoint declarations (backticked
`` `GET /path` `` spans, endpoint tables,
`only GET on /path` exclusivity statements),
config-key declarations (backticked keys under a
Configuration heading, keys in fenced TOML/INI
examples), env-var declarations and env negations
("never reads `AWS_*`"). Declaration forms outside
those patterns are not indexed and never judged —
the receipt's Limits section says so on every run,
including exactly which config/env patterns are
indexed and which are refused (e.g. YAML configs,
non-`config` receiver names).

Extraction is scoped as tightly as the verdicts.
Illustrative content never becomes an
item: sections whose headings announce examples or
definitions of the notation (Examples, Definitions,
Glossary, Notation, "spec item" forms), lines that
present an example (e.g. / for example) or describe
the notation (a backticked token, of the form,
written as, a placeholder), quoted text (quotation
is the document talking about wording, not wording
that declares), template placeholders
(endpoint paths made only of placeholder words;
tokens naming a capitalisation style), sample
output (an untagged fence counts as config only
with a real `key = value` assignment, and a
`[section]` header must stand alone on its line),
and names the document itself defines as its own
terms (definition tables, gloss enumerations).
Requirement anchors are scoped to the ID's own
spec: the anchor must carry the feature qualifier
(`FR-001 (001)`), sit in a feature file, or — when
one spec in the tree declares the ID — sit in that
tree; a comment block listing an ID as unanchored,
or enumerating several IDs, is never an anchor,
and its words are not term evidence either.

## The verdicts (the whole product)

Every extracted spec item gets exactly one verdict;
every indexed code surface gets exactly one.

Per spec item:

| Verdict | Meaning | Evidence required |
|---|---|---|
| `IMPLEMENTED` | The named surface exists in code as specified | The surface's AST node at file:line; any stated default/choices also match. For a negation item: the flag is absent from a completely enumerated flags class (the negation holds) |
| `MISSING` | A structured surface item has no counterpart in code | The surface's class was **completely enumerated** for this repo and the item is absent from it. Never issued for requirement items, never on partial enumeration |
| `CONTRADICTED` | Code proves the opposite of a spec statement | Hard evidence both sides: stated default vs different code default; stated choice set vs different code choices (or no code restriction); "there is no `--x`" vs the flag's AST node; a spec'd endpoint path registered with other methods only; an env negation ("never reads `AWS_*`") vs a matching read |
| `TRACED` | A requirement ID has ≥ 1 anchor | The ID verbatim in a code/test file, scoped to the ID's own spec (feature qualifier / feature file / its tree when unambiguous — see Scope), or ≥ 2 distinctive statement terms co-occurring in one in-scope file after stopword + document-frequency filtering. TRACED means "has an anchor", never "is correctly implemented" |
| `UNTRACED` | A requirement has no anchor, or a surface class was only partially enumerated | The honest residue. UNTRACED is **not** MISSING: natural language cannot prove a behaviour absent. Reported, counted, never fails by default |

Per code surface:

| Verdict | Meaning | Evidence required |
|---|---|---|
| `DOCUMENTED` | A spec item names this surface | The spec citation. A surface named only by a negation item is DOCUMENTED (named, as forbidden); the negation item itself carries CONTRADICTED |
| `UNDOCUMENTED` | Surface exists in code; no spec item names it | The code citation + the complete spec item list for that class was searched |

Rules that never bend:

- **MISSING only on complete enumeration.** If a
  class cannot be fully enumerated for a repo
  (flags built from variables, an unparseable
  file), the index marks it partial and absence
  verdicts degrade to UNTRACED. Absence is never
  upgraded on partial evidence.
- **CONTRADICTED needs a direct conflict**, cited
  on both sides. A stated parameter whose code
  value is not statically resolvable does not
  contradict — it is reported as matching on the
  surface, with the parameter unverifiable by
  construction (non-constant choices never
  contradict a stated set; an absent restriction
  does, because the code provably accepts more
  than the spec allows).
- **Requirements are traced, never judged.** The
  tool has no verdict for "this prose requirement
  is correctly implemented" and no verdict for
  "this prose requirement is absent".
- **No score.** The receipt is counts and
  citations, never a drift percentage or a grade.

## JSON schema (`--json`)

One object:

- `tool`, `version`, `command`
- `repo` (as passed, absolute), `repo_name`,
  `spec_files[]` (repo-relative)
- `classes`: per class (`flags`, `commands`,
  `routes`, `config`, `env`) — `complete` (bool),
  `surfaces` (count); `index_notes[]` explains
  every partial marking
- `items[]`: `kind`
  (`flag`|`command`|`negation`|`requirement`|
  `endpoint`|`endpoint-only`|`config`|`env`|
  `env-negation`), `name`, `spec` (`file`, `line`),
  `code` (`file`, `line`) or null, `verdict`,
  `basis`; flag items may carry `stated_default` /
  `stated_choices`; requirement items carry `text`;
  endpoint items carry `method` and `path`
- `surfaces[]`: `kind`, `name`, `code`
  (`file`, `line`), `spec` (`file`, `line`) or
  null, `verdict`, `basis`; endpoint surfaces carry
  `method` (`ANY` for Django, null when the method
  set is unresolvable) and `path`
- `summary`: counts that recount exactly against
  `items` and `surfaces`
- `verdict` (`DRIFT`|`NO_DRIFT`), `verdict_rule`,
  `fail_on[]`, `limits[]`

Items are ordered by spec file, then line;
surfaces are grouped by class (CLI, routes, config,
env) in code-discovery order within each class.
The whole report is byte-identical across runs of
the same repo.

## Deliberate non-goals (honesty)

- **It never executes anything.** No repo file is
  run, imported, compiled, or evaluated. Static
  evidence only.
- **No semantic judgement of behaviour.** Whether
  code correctly implements a prose requirement is
  not decidable statically; requirements get
  TRACED/UNTRACED, full stop.
- **Only the five indexed classes are judged.**
  Declaration forms outside the documented patterns
  (runtime-registered routes, YAML configs,
  non-`config` config receivers, dynamically built
  env names) are not half-scanned; the class is
  marked partial where the tool can see the gap,
  and the limits say so.
- **No git/diff mode, no test running, no
  auto-fix, no spec rewriting, no PR comments.**
  A receipt, not a repair tool.
- **No score, no LLM, no network, no telemetry.**
  Deterministic patterns in, the same receipt out,
  forever.
