# Changelog

## v1.0.0 — 2026-11-07 (first release)

First public release: GitHub + PyPI via Trusted
Publishing. No engine changes since v0.3.0 — this
entry is the ship record of the three build stages
and the pre-release dogfood gate, told whole,
including the round the tool failed.

- **Stage 1 (v0.1.0)** — spec designation, spec item
  extraction (flags with stated defaults/choices,
  commands, flag negations, requirement IDs), the
  argparse/click AST index, and the verdict engine:
  IMPLEMENTED / MISSING (only on complete
  enumeration) / CONTRADICTED (hard evidence both
  sides) / TRACED / UNTRACED per item, DOCUMENTED /
  UNDOCUMENTED per surface, `--json` one-object
  contract, exit codes 0/1/2.
- **Stage 2 (v0.2.0)** — the remaining three surface
  classes on both sides: HTTP routes (Flask,
  FastAPI, Django; prefix resolution; endpoint
  identity as the (method, path) pair), config keys
  (example configs + `config[...]` / `config.get`
  reads, presence only), environment variables
  (constant names, exact and wildcard negations).
- **The dogfood gate — run 1 FAILED precision.**
  Before release the tool was run on three real
  spec'd repos (its own design document,
  skill-claims, and the public spec-kit-layout
  project chenhz01/spec-drift), every CONTRADICTED
  and TRACED citation hand-checked. Coverage passed
  (mean 67.5% of items deterministically judged,
  against the 50% kill line), but all 3 CONTRADICTED
  verdicts were false in intent: the extractor had
  read the design document's *examples of spec-item
  forms* as declarations and formed verdicts against
  pairs that should never have existed. Two MISSINGs
  were placeholder tokens from definition sentences,
  and TRACED anchors were matched repo-wide —
  one feature's `FR-001` was traced to another
  feature's tests, and two IDs to a comment block
  that lists them as having *no* anchor. The verdict
  engine never misfired on a genuine declaration;
  extraction formed the false pairs upstream of it.
- **Stage 3 (v0.3.0)** — the fix, one class
  (extraction scoping): illustrative sections and
  lines, template placeholders, the document's own
  defined terms, quoted mentions, and sample output
  no longer form items; fenced invocation lines do
  form commands; requirement anchors are scoped to
  the ID's own spec and a comment block that denies
  anchoring is never an anchor. Every failure shape
  became a regression test (87 total).
- **The rerun passed the ship bar.** Same three
  targets, same commands, same hand-checks: zero
  false CONTRADICTED on any target, every TRACED
  citation correctly scoped or honestly UNTRACED,
  mean coverage 92.6%. Two requirements on the
  external target are now honestly UNTRACED where
  run 1 had traced them to a denial block — lower
  mechanical coverage, correct receipts.
- Packaging for release: launch README with the
  fixtures' verbatim receipts, demo GIF and social
  preview rendered from real tool output, launch
  kit. Python 3.10+, standard library only, no
  dependencies, MIT.

## v0.3.0 — 2026-11-07 (stage 3, pre-release)

Stage 3 is a fix stage driven by the dogfood gate
(DOGFOOD-SPEC-RECEIPT.md: PASS-WITH-FIXES, one fix
class). The verdict engine, code index and taxonomy
are unchanged; what changed is what gets extracted
and how requirement anchors are scoped. Every false
verdict in the gate traced to extraction forming
pairs that should never have existed.

- **Illustrative content is not declarative.**
  Sections whose headings announce examples or
  definitions of the notation (Examples,
  Definitions, Glossary, Notation, "spec item"
  forms) yield no items; so do lines presenting an
  example (e.g. / for example) or describing the
  notation (a backticked token, of the form,
  written as, a placeholder). Template placeholders
  are filtered: endpoint paths whose every segment
  is a placeholder word (`/path`, `/x`,
  `/path/:id`) and tokens naming a capitalisation
  style (`SCREAMING_CASE`).
- **Defined terms are vocabulary.** A name the
  document itself defines — first column of a
  definition table (Meaning / Definition /
  Description column), or a gloss enumeration of
  three or more glossed terms — is never extracted
  as an environment variable.
- **Quotation is not declaration.** Text inside
  straight double quotes — a form being quoted,
  another spec's statement being described — is
  removed before extraction. (Found by the stage-3
  rerun: the design doc's fixture description
  quotes a "no `--legacy`" statement that lives in
  the fixture's spec, not in the design doc.)
- **Sample output is not configuration.** An
  untagged fence counts as config only with at
  least one `key = value` assignment, and a
  `[section]` header in config text declares a key
  only when it stands alone on its line.
- **Fenced invocations declare commands.** Usage
  lines in fenced blocks (`tool audit <folder>`)
  are now heard; generic shell / package tools
  (pip, pipx, python, git, ...) are stoplisted, so
  `pipx install ...` no longer mishears as a
  command named `install` — in fences or in prose
  spans.
- **Wrapped spans join.** A backticked span split
  across a line break (the `--fail-on` case) is
  joined before extraction and cited at its first
  line; a flag span may carry its value
  (`` `--fail-on undocumented,untraced` ``).
- **Scoped requirement tracing.** An anchor must
  carry the spec's feature qualifier
  (`FR-001 (001)`), sit in a feature file, or —
  when a single spec in the tree declares the ID —
  sit in that tree. Comment blocks that deny
  anchoring or enumerate several IDs are never
  anchors. An ID found only outside its scope is
  UNTRACED rather than traced to another feature.
  Term evidence is read with refused lines
  blanked, so a denial that restates the
  requirement's words cannot trace it either.
  (Also found by the rerun.)
- 16 new regression tests (87 total), one per
  dogfood failure shape; all stage-1/2 fixture
  expectations unchanged.

## v0.2.0 — 2026-11-06 (stage 2, pre-release)

Stage 2 adds the three deferred surface classes —
HTTP routes, config keys, environment variables —
on both sides (spec extraction and code index),
under the same verdict engine and evidence bars.

- **Route index (AST):** Flask `@*.route(...)` with
  explicit `methods=[...]` (no `methods` = GET, the
  framework default; automatic HEAD/OPTIONS are not
  declarations), FastAPI `@*.get/post/put/delete/
  patch/head/options/trace` and `api_route`,
  Django `path()` / `re_path()` in `urls.py`
  (indexed as ANY-method surfaces — Django declares
  no method at the URL layer). Paths are normalised
  across frameworks (parameters written `:name`).
  Blueprint/APIRouter constructor prefixes,
  `register_blueprint` prefixes and `include_router`
  prefixes are applied when constant and declared
  in the same module. `add_url_rule` registrations
  are not indexed; their presence, non-constant
  paths/prefixes and unresolvable method sets mark
  the routes class partial (an unresolvable method
  set is recorded as a surface whose method is never
  judged).
- **Config index:** keys of example/sample/
  config-named `.toml` / `.ini` / `.json` files
  (every declared dotted path; `pyproject.toml` and
  the `.spec-receipt.toml` manifest excluded) and
  `config[...]` subscript chains / `config.get(...)`
  reads with constant-string keys on a receiver
  named `config`. Presence only — values are never
  compared. YAML config files cannot be parsed with
  the standard library: not indexed, and their
  presence marks the class partial, as do
  non-constant keys and unreadable/unparseable
  config files.
- **Env index:** `os.environ[...]`, `os.getenv(...)`
  and `os.environ.get(...)` with constant-string
  names, including the `from os import environ,
  getenv` spellings. Non-constant names mark the
  class partial.
- **Spec extraction, new forms:** endpoint items
  from backticked `` `GET /path` `` spans anywhere,
  table rows and bare lines under Endpoints/API
  headings, and endpoint exclusivity items ("only
  `GET` on `/items`" — the one endpoint form that
  presence alone can contradict); config items from
  backticked dotted keys under a Configuration
  heading and from keys in fenced TOML/INI blocks;
  env items from backticked `SCREAMING_CASE` names;
  env negations, exact or wildcard ("never reads
  `AWS_*`"), which a matching read contradicts.
  Endpoint identity is the (method, path) pair: a
  spec'd path registered with other methods only is
  CONTRADICTED (the spec's own example); a spec'd
  path absent entirely is MISSING on complete
  enumeration, UNTRACED on partial.
- **Receipt:** the classes block now reports all
  five classes; surface rows cover routes, config
  keys and env vars (a surface named by a negation —
  flag or env — is DOCUMENTED; the negation item
  carries the CONTRADICTED). Limits text rewritten
  for stage 2. JSON contract, exit codes and the
  read-only guarantee unchanged.
- **Fixtures + tests:** both fixtures extended to
  the spec's full section-6 design — the clean
  fixture gains a Flask route pair, an example TOML
  config (5 keys) and 2 env vars plus the `AWS_*`
  negation, all in exact agreement (17 items: 15
  IMPLEMENTED, 2 TRACED; 13 surfaces, all
  DOCUMENTED; exit 0); the drifted fixture's code
  changes the create handler from POST to GET,
  adding exactly one CONTRADICTED (17 items: 11
  IMPLEMENTED, 1 MISSING, 3 CONTRADICTED, 1 TRACED,
  1 UNTRACED; 13 surfaces: 12 DOCUMENTED, 1
  UNDOCUMENTED; exit 1). 28 new tests cover every
  stage-2 extractor, the prefix resolution, the
  partial-enumeration degradations and the new
  verdict rules; the spec-only fixture test now
  also asserts the new classes on empty enumeration.

## v0.1.0 — 2026-11-06 (stage 1, pre-release)

First build. Receipts one repository — without
executing anything in it — and reports, per spec
item, whether the code still declares the surfaces
its design spec promises, and which code surfaces
the spec never mentioned.

- **Spec designation** in contract priority order:
  explicit `--spec` arguments, a `.spec-receipt.toml`
  manifest (`specs = [...]`), then auto-discovery
  (`SPEC.md`/`spec.md`, `docs/spec*.md`,
  `docs/SPEC.md`, `specs/**/spec.md`, `DESIGN.md`,
  `API.md`). No spec found is a clean exit 2.
- **Spec item extraction** (deterministic forms
  only): CLI flag declarations in backticks with
  stated defaults (`(default: fast)`) and choice
  sets (``one of `low|high` ``, backticked lists);
  CLI commands from `## Command: name` headings and
  backticked invocation lines (`` `tool export
  <file>` ``); flag negations ("there is no
  `--legacy` flag", "`--x` is not supported");
  requirement IDs (`FR-###`, `REQ-<word>`, `R-##`)
  with their statement text as trace vocabulary.
- **Code index (CLI surfaces only):** `argparse`
  (`add_argument` flags with defaults and choices,
  `add_parser` subcommands) and `click`
  (`@click.option` with defaults and
  `click.Choice(...)`, `@click.command` / `group`)
  extracted via `ast`. A class that cannot be fully
  enumerated (non-constant option strings or names,
  unreadable/unparseable files) is marked partial.
- **Verdicts:** per item — IMPLEMENTED, MISSING
  (only on complete enumeration), CONTRADICTED
  (default mismatch, choice-set mismatch,
  negation-presence), TRACED (ID verbatim in
  code/tests, or ≥ 2 distinctive statement terms
  co-occurring in one file after stopword +
  document-frequency filtering), UNTRACED (the honest
  residue; never upgraded to MISSING, never fails
  by default). Per code surface — DOCUMENTED /
  UNDOCUMENTED. `--fail-on undocumented,untraced`
  promotes either report-only verdict to failing.
- **Contracts:** `--json` prints exactly one
  object; errors in `--json` mode are
  `{"error": ...}` JSON, argparse errors included.
  Exit codes 0 (no failing verdict), 1 (drift),
  2 (input error). Reports are deterministic and
  recount exactly.
- **Fixtures + tests:** a clean repo fixture
  (every item IMPLEMENTED/TRACED, every surface
  DOCUMENTED, exit 0; any CONTRADICTED there is a
  release blocker) and a drifted repo fixture with
  the spec's exact expected verdict multiset,
  plus unit tests for every extractor and verdict
  rule, partial-enumeration degradation, and a
  read-only proof (fixture trees byte-identical
  after runs; a booby-trapped fixture module never
  executes).

Not in this stage (later stages, deliberately):
HTTP route indexing (Flask/FastAPI/Django),
config-key and environment-variable surfaces, and
their spec declarations.
