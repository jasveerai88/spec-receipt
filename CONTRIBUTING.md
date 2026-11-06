# Contributing

Thanks for looking. spec-receipt has a small scope on
purpose: it reads one repo as static text and static
AST, extracts what the spec declares and what the
code declares, and prints a per-item drift receipt.
Contributions that keep that scope sharp are welcome.

## Ground rules

- **Standard library only.** No third-party
  dependencies, no network calls, ever. If a change
  seems to need one, it is probably out of scope.
- **Deterministic, always.** No LLM, no randomness,
  no timestamps in the receipt: the same repo must
  produce a byte-identical report on every run, on
  every machine.
- **Read-only, and it executes nothing — always.**
  The check must never write to a repo, and never
  run, import, or evaluate anything in one. Tests
  prove fixture trees are byte-identical after runs
  and that a booby-trapped fixture module never
  runs; keep them passing.
- **The evidence discipline is the product.**
  MISSING is issued only when the item's surface
  class was completely enumerated and the item is
  absent from it. CONTRADICTED needs hard evidence
  on both sides. A requirement item is never
  MISSING and never judged implemented — it is
  TRACED (has an anchor) or UNTRACED (has none),
  and UNTRACED is never upgraded on partial
  evidence. A change that flips a verdict on
  suspicion or similarity will not be merged.
- **The verdict table is the contract.** Every item
  form, surface class and verdict lives in SKILL.md
  and README.md with its exact evidence basis. A
  new item form or surface class needs: the
  extraction patterns, the completeness rule for
  its class, and both directions tested (evidence
  present flips the verdict; absent, it does not).
- **Honesty in both directions.** Receipts must say
  what the evidence shows and no more; the limits
  section must keep saying what static analysis
  cannot prove — including which surface classes
  the current stage does not yet index.
- **Fixtures stay fictional.** Example repos are
  constructed for this repo; expected verdict
  multisets are asserted exactly, and a
  CONTRADICTED on the clean fixture is a release
  blocker, never a fixture edit away from green.
- **No tracebacks, ever.** Bad input is a clean
  `{"error": ...}` with exit 2 — including argument
  errors in `--json` mode.

## Running the tests

```bash
python3 -m unittest discover -s tests
```

All tests must pass on a clean checkout with nothing
but Python 3.10+.

## Changing the output

The text receipt and the `--json` schema are both
public surface: scripts and agents consume them.
Keep summary counts recounting exactly against the
items and surfaces lists, keep ordering
deterministic, and note any schema change in
CHANGELOG.md.
