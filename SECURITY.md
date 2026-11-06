# Security

## spec-receipt's own posture

spec-receipt is itself an audit tool, and its own
attack surface is deliberately minimal:

- **No network code, no subprocess code.** The
  source imports only `argparse`, `ast`, `json`,
  `os`, `re` and `sys` — a test parses its imports
  and pins that set. It cannot phone home because
  it has no way to.
- **It never executes what it scans.** No repo file
  is run, imported, compiled, or evaluated;
  everything is read as Markdown text or Python
  AST. Pointing spec-receipt at a hostile repo is
  safe by construction, and the test suite proves
  it (a booby-trapped fixture's marker file never
  appears; fixture trees are byte-identical after
  a run).
- **No dependencies.** Python 3.10+ standard
  library only — there is no supply chain beyond
  Python itself.

## Reporting a problem

If you find a way to make spec-receipt execute
repo content, write outside its read-only
behaviour, or crash with a traceback on malformed
input, please report it privately through GitHub's
private vulnerability reporting on the repository.
