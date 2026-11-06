#!/usr/bin/env python3
"""spec-receipt — a deterministic, local drift receipt
for a repository: does the code still declare the
surfaces its design spec promises, and what did it
grow that the spec never mentioned?

Pure Python standard library. READ-ONLY and local
only: it reads the repo you point it at, edits
nothing, writes nothing, and NEVER executes anything
in the repo — every file is analysed statically
(Markdown parsing for the spec, `ast` for the code).

THE QUESTION. A spec is a promise made at design
time; code drifts. Flags get deleted, defaults get
changed, forbidden flags sneak back in, features
appear that no document mentions. Review catches
some of it, late, by eye. spec-receipt prints the
receipt instead: every extractable spec item gets a
verdict, every indexed code surface gets a verdict,
and every verdict cites file:line on both sides.

THE HONESTY LINE (the whole product):

  * Verdicts rest on hard evidence only. MISSING is
    issued solely when the item's surface class was
    COMPLETELY enumerated in the code index and the
    item is absent from it. CONTRADICTED needs direct
    conflicting evidence on both sides (a stated
    default vs a different code default, a stated
    choice set vs a different one, a "there is no
    --x" line vs the flag's AST node).
  * Natural-language requirements are never judged
    implemented or absent. They are TRACED (an anchor
    exists: the ID verbatim in code/tests, or >= 2
    distinctive statement terms co-occurring in one
    code/test file) or UNTRACED (no anchor found).
    UNTRACED is not MISSING, and the receipt says so.
  * A class that cannot be fully enumerated (flags
    built from variables, an unparseable file)
    degrades absence verdicts to UNTRACED. Absence is
    never upgraded to MISSING on partial evidence.

STAGE 3 SCOPE. This build indexes five surface
classes: CLI flags and commands (`argparse` and
`click`, via AST), HTTP routes (Flask `@*.route` and
method-shortcut decorators, FastAPI method
decorators and `api_route`, Django `path()` /
`re_path()` in `urls.py`, all via AST), config keys
(example config files plus `config[...]` /
`config.get(...)` reads), and environment variables
(`os.environ[...]`, `os.getenv(...)`,
`os.environ.get(...)` with constant-string names).
Spec items extracted: CLI flag declarations (with
stated defaults / choice sets), CLI command
declarations (headings, backticked invocation
lines, and invocation lines in fenced blocks), flag
negations, HTTP endpoint declarations (`GET /path`
forms and Endpoints/API table rows), endpoint
exclusivity items ("only `GET` on `/items`"),
config keys (backticked keys in a Config section,
keys in fenced TOML/INI blocks), environment
variables (backticked SCREAMING_CASE names) and env
negations ("never reads `AWS_*`"), and requirement
IDs (FR-### / REQ-<word> / R-##) for tracing.

Stage 3 tightens EXTRACTION SCOPING (the verdict
engine is unchanged): illustrative content — example
/ definition sections, example lines, template
placeholders, sample output — never becomes an
item, and requirement anchors are scoped to the
requirement's own spec (its feature and tree), so
an ID is never traced to another feature's anchor
or to a comment that lists it as unanchored. The
receipt's Limits list states every rule.

Route choices where the frameworks leave room: a
Flask route declared without `methods` counts as
GET (the framework default); the automatic
HEAD/OPTIONS a framework adds at runtime are not
declarations and are not indexed. Django URL routes
declare no method, so they are indexed as
ANY-method surfaces: they can satisfy a
method-specific spec item but never contradict one.
FastAPI `include_router` prefixes are applied only
when constant and declared in the same module.
Paths are compared in a normalised form: leading
slash, no trailing slash, and path parameters
(`<int:id>`, `{id}`, Django `<id>` / `(?P<id>...)`)
all written `:id`.

Exit codes: 0 = no failing verdict, 1 = drift found
(>= 1 MISSING or CONTRADICTED; UNDOCUMENTED and
UNTRACED fail only when promoted via --fail-on),
2 = input error. In --json mode the output is
exactly one JSON object, errors included.
"""

import argparse
import ast
import json
import os
import re
import sys

TOOL = "spec-receipt"
VERSION = "1.0.0"

LIMITS = [
    "Stage 3 indexes five surface classes: CLI flags "
    "and commands (argparse + click, via AST), HTTP "
    "routes (Flask, FastAPI and Django URL "
    "declarations, via AST), config keys, and "
    "environment variables. Declaration forms outside "
    "those patterns are not indexed and never judged.",
    "Routes: only declared methods are indexed - a "
    "Flask route declared without methods counts as "
    "GET (its framework default); the automatic "
    "HEAD/OPTIONS a framework adds at runtime are not "
    "declarations and are not indexed. Django URL "
    "routes declare no method, so they are indexed as "
    "ANY-method surfaces: they can satisfy, but never "
    "contradict, a method-specific spec item. "
    "Blueprint/APIRouter constructor prefixes, "
    "register_blueprint prefixes and include_router "
    "prefixes are applied only when constant and "
    "declared in the same module; add_url_rule "
    "registrations are not indexed, and their "
    "presence marks the routes class partial.",
    "Config keys: presence only - values in example "
    "configs are never compared with anything. "
    "Indexed: keys of example/sample/config-named "
    ".toml, .ini and .json files (pyproject.toml and "
    "the manifest excluded) and config[...] / "
    "config.get(...) reads with constant-string "
    "keys, on a receiver named config. YAML configs "
    "cannot be parsed with the standard library and "
    "are not indexed; their presence marks the "
    "config class partial.",
    "Environment variables: only os.environ[...], "
    "os.getenv(...) and os.environ.get(...) with "
    "constant-string names (including the "
    "from-import spellings) are indexed. Names built "
    "at runtime mark the env class partial.",
    "Extraction scope: sections whose headings "
    "announce examples or definitions of the notation "
    "itself (Examples, Definitions, Glossary, "
    "Notation, 'spec item' forms) and lines that "
    "present an example (e.g. / for example) or "
    "describe the notation (a backticked token, of "
    "the form, written as, a placeholder) are "
    "illustrative, not declarative: no items are "
    "extracted from them. Text inside straight "
    "double quotes is quotation — the document "
    "talking about wording, not wording that "
    "declares — and is likewise never extracted. "
    "Template placeholders are "
    "not declarations either: an endpoint path whose "
    "every segment is a placeholder word (path, x, "
    "foo, ...) and a token naming a capitalisation "
    "style (SCREAMING_CASE) name the notation, not a "
    "surface. A name the document itself defines as "
    "one of its terms — in a definition table with a "
    "Meaning column, or in an enumerated list of "
    "terms with glosses — is vocabulary, not an "
    "environment-variable declaration.",
    "Fenced blocks: an untagged block counts as "
    "config only when it contains at least one "
    "key = value assignment, and a [section] header "
    "declares a key only when it stands alone on its "
    "line — sample output quoted in a spec is not "
    "configuration. Invocation lines in fenced blocks "
    "do declare commands, but a line invoking a "
    "generic shell or package tool (pip, pipx, "
    "python, git, ...) names that tool's own operand, "
    "never a command of the tool being spec'd.",
    "TRACED means a requirement ID has an anchor in "
    "code or tests; it never means the requirement is "
    "correctly implemented. Anchors are scoped to the "
    "requirement's own spec: an anchor must carry the "
    "spec's feature qualifier, sit in a file of the "
    "feature, or — when the ID is declared by only "
    "one spec in its tree — sit in that tree. A "
    "comment block that lists an ID as unanchored, "
    "or enumerates several IDs, is a denial or a "
    "listing, never an anchor — and its words are "
    "not term evidence either. An ID found only "
    "outside its scope is UNTRACED, not traced to "
    "the wrong place. UNTRACED is not MISSING: "
    "natural language cannot prove a behaviour absent.",
    "MISSING is issued only when the item's surface "
    "class was completely enumerated and the item is "
    "absent from it; on partial enumeration an absent "
    "item is UNTRACED.",
    "Static evidence only: nothing in the target repo "
    "is executed, imported or run. Surfaces built "
    "purely at runtime may not appear in the index.",
    "Python code only. A repo with no Python "
    "surfaces reports its classes enumerated to empty.",
]


class ToolError(Exception):
    """A clean input error (exit 2, never a traceback)."""


# ------------------------------------------------------------------
# Spec files: designation + parsing
# ------------------------------------------------------------------

MANIFEST_NAME = ".spec-receipt.toml"

# Directories never walked for code or specs.
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "site-packages", ".tox", ".mypy_cache",
}


def _read_text(path, what):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ToolError("cannot read %s %s: %s"
                        % (what, path, exc))


def _parse_manifest(text, path):
    """Read the manifest's `specs = [...]` list.

    A deliberately small subset of TOML (one array of
    quoted strings) so the tool runs on Python 3.10,
    which has no tomllib. Anything else in the file is
    ignored at this stage; a manifest with no usable
    specs list is an input error, not a silent miss.
    """
    m = re.search(r"(?m)^\s*specs\s*=\s*\[(.*?)\]",
                  text, re.DOTALL)
    if not m:
        raise ToolError(
            "manifest %s has no specs = [...] list" % path)
    names = []
    for dq, sq in re.findall(r'"([^"]*)"|\'([^\']*)\'',
                             m.group(1)):
        names.append(dq or sq)
    names = [n for n in names if n]
    if not names:
        raise ToolError(
            "manifest %s lists no spec files" % path)
    return names


def find_spec_files(repo, spec_args):
    """Designate the spec file(s), in the contract's
    priority order: explicit --spec, then the manifest,
    then auto-discovery. Returns absolute Paths as
    strings; raises ToolError when nothing is found.
    """
    repo = os.path.abspath(repo)

    def resolve(name):
        cand = name if os.path.isabs(name) \
            else os.path.join(repo, name)
        if os.path.isfile(cand):
            return cand
        if os.path.isfile(name):
            return os.path.abspath(name)
        raise ToolError("spec file not found: %s" % name)

    if spec_args:
        return [resolve(a) for a in spec_args]

    manifest = os.path.join(repo, MANIFEST_NAME)
    if os.path.isfile(manifest):
        text = _read_text(manifest, "manifest")
        return [resolve(n)
                for n in _parse_manifest(text, manifest)]

    def tier_root(names):
        return [os.path.join(repo, n) for n in names
                if os.path.isfile(os.path.join(repo, n))]

    def tier_glob(subdir, pattern):
        base = os.path.join(repo, subdir)
        out = []
        if os.path.isdir(base):
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(
                    d for d in dirnames if d not in SKIP_DIRS)
                for fn in sorted(filenames):
                    if _glob_match(fn, pattern):
                        out.append(os.path.join(dirpath, fn))
        return sorted(out)

    # Auto-discovery tiers, first non-empty tier wins.
    tiers = [
        tier_root(["SPEC.md", "spec.md"]),
        tier_glob("docs", "spec*.md") + tier_root(
            [os.path.join("docs", "SPEC.md")]),
        tier_glob("specs", "spec.md"),
        tier_root(["DESIGN.md", "API.md"]),
    ]
    for tier in tiers:
        found = sorted(set(tier))
        if found:
            return found
    raise ToolError(
        "no spec file found in %s (looked for --spec, "
        "%s, SPEC.md, docs/spec*.md, specs/**/spec.md, "
        "DESIGN.md, API.md)" % (repo, MANIFEST_NAME))


def _glob_match(name, pattern):
    # Only the two shapes the contract uses: "spec*.md"
    # (prefix + suffix) and an exact name ("spec.md").
    if "*" not in pattern:
        return name == pattern
    pre, _, post = pattern.partition("*")
    return name.startswith(pre) and name.endswith(post)


# --- spec item extraction -------------------------------------

# A flag item is a backticked span whose FIRST token is
# the flag (`--mode`, or `--fail-on undocumented` — a
# flag shown with its value). The span's remainder is
# the flag's own statement scope, never another item.
FLAG_TOKEN_RE = re.compile(
    r"`--([A-Za-z0-9][A-Za-z0-9_-]*)[^`\n]*`")
DEFAULT_RE = re.compile(
    r"default\s*(?::|=|is)\s*`?\"?'?([A-Za-z0-9_.+-]+)`?\"?'?",
    re.IGNORECASE)
PIPE_CHOICES_RE = re.compile(
    r"`?([A-Za-z0-9_+-]+(?:\|[A-Za-z0-9_+-]+)+)`?")
REQ_ID_RE = re.compile(
    r"\b(FR-\d{1,4}|REQ-[A-Za-z][A-Za-z0-9_-]*|R-\d{1,4})\b")
COMMAND_HEADING_RE = re.compile(
    r"^#{1,6}\s+Command\s*:\s*`?([A-Za-z][A-Za-z0-9_-]*)`?\s*$",
    re.IGNORECASE)
BACKTICK_SPAN_RE = re.compile(r"`([^`\n]+)`")
WORD_RE = re.compile(r"[a-z][a-z0-9_-]*")
NEGATION_BEFORE_RE = re.compile(
    r"\b(no|never|not|none)\b", re.IGNORECASE)
NEGATION_AFTER_RES = [
    re.compile(r"^\s+(is|are)\s+not\b", re.IGNORECASE),
    re.compile(r"^\s+does\s+not\s+exist\b", re.IGNORECASE),
    re.compile(r"^\s+not\s+supported\b", re.IGNORECASE),
    re.compile(r"^\s+is\s+forbidden\b", re.IGNORECASE),
]
HTTP_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH",
                "HEAD", "OPTIONS", "TRACE", "CONNECT")
ENDPOINT_SPAN_RE = re.compile(
    r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|"
    r"CONNECT)\s+(/\S+)$")
ENDPOINT_BARE_RE = re.compile(
    r"^\s*(?:[-*+]\s+)?`?(GET|POST|PUT|DELETE|PATCH|"
    r"HEAD|OPTIONS|TRACE|CONNECT)`?\s+`?(/[^\s`]+)`?")
ONLY_ENDPOINT_RE = re.compile(
    r"\bonly\s+`?(GET|POST|PUT|DELETE|PATCH|HEAD|"
    r"OPTIONS|TRACE|CONNECT)`?\s+(?:on|for)\s+"
    r"`?(/[^\s`]+)`?", re.IGNORECASE)
CONFIG_KEY_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*$")
ENV_TOKEN_RE = re.compile(r"^([A-Z][A-Z0-9_]{1,})(\*)?$")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
FENCE_RE = re.compile(r"^```(\w*)\s*$")

# --- stage 3: extraction scoping ------------------------------
# Illustrative content never becomes an item. A section
# whose heading announces examples, or definitions of
# the notation itself, is a meta-section: it shows the
# FORMS items take, it does not declare surfaces.
ILLUSTRATIVE_HEADING_RE = re.compile(
    r"example|definition|glossary|notation|"
    r"extractable|spec item")
# A single line can be illustrative on its own: it
# presents an example (e.g. / for example) or talks
# about the notation rather than using it (a backticked
# token, of the form, written as, a placeholder).
ILLUSTRATIVE_LINE_RE = re.compile(
    r"\be\.g\.|\bfor example\b|\bbackticked\b|"
    r"\bof the form\b|\bwritten as\b|\bplaceholder\b",
    re.IGNORECASE)
# Endpoint paths whose every concrete segment is one of
# these words are template text (`GET /path`,
# `POST /x`), not declarations of a route.
PLACEHOLDER_SEGMENTS = {
    "path", "x", "y", "foo", "bar", "example",
    "placeholder",
}
# Generic shell / package / VCS tools: an invocation of
# one (`pipx install ...`, `git commit ...`) names that
# tool's own operand, never a command of the tool being
# spec'd.
GENERIC_HEADS = frozenset({
    "pip", "pip3", "pipx", "python", "python3", "py",
    "git", "cd", "ls", "mkdir", "rm", "cp", "mv",
    "echo", "cat", "curl", "wget", "npm", "npx",
    "yarn", "pnpm", "docker", "kubectl", "sudo",
    "brew", "apt", "apt-get", "chmod", "chown",
    "touch", "export", "source", "set", "env", "make",
    "cargo", "go", "gem", "bundle", "composer",
    "poetry", "conda", "virtualenv", "venv", "tar",
    "unzip", "zip", "ssh", "scp", "rsync", "grep",
    "sed", "awk", "find", "xargs", "which", "open",
    "code", "node", "deno", "bun", "java", "javac",
    "dotnet", "terraform", "ansible", "helm",
    "systemctl", "service", "psql", "mysql",
    "sqlite3", "redis-cli", "mongosh",
})
DOUBLE_QUOTED_RE = re.compile(r'"[^"\n]*"')
# A fenced block with no language tag counts as config
# only when it holds at least one assignment line;
# sample output full of [BRACKETED] lines does not.
CONFIG_ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*[A-Za-z0-9_-]+"
    r"(?:\s*\.\s*[A-Za-z0-9_-]+)*\s*=")
# Header words that mark a table as defining terms.
DEF_TABLE_HEADER_WORDS = {"meaning", "definition",
                          "description"}


def _norm_path(raw):
    """Normalise a declared route path for comparison:
    leading slash, no trailing slash (except root),
    and every path parameter written :name, whatever
    the framework's spelling (<int:id>, {id},
    Django's <id>)."""
    p = raw.strip().strip("`")
    if not p.startswith("/"):
        p = "/" + p

    def _angle(m):
        return ":" + m.group(1).rsplit(":", 1)[-1].strip()

    p = re.sub(r"<([^>]+)>", _angle, p)
    p = re.sub(r"\{([^}]+)\}",
               lambda m: ":" + m.group(1).split(":")[0]
               .split("=")[0].strip(), p)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def _config_text_keys(text, base_line=1):
    """Enumerate keys in TOML/INI-shaped text: [section]
    headers and `key = value` lines. Returns a list of
    (dotted_key, line) for every declared path — a
    section header declares its own path, a key line
    declares section + key. Keys only: values are
    never read, so a line parser is enough (and keeps
    the tool working on Python 3.10, which has no
    tomllib)."""
    out = []
    seen = set()
    prefix = []
    triple = None
    for off, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if triple:
            if triple in line:
                triple = None
            continue
        if line.startswith('"""') or line.startswith("'''"):
            q = line[:3]
            if line.count(q) < 2:
                triple = q
            continue
        if not line or line.startswith("#") \
                or line.startswith(";"):
            continue
        # A section header declares its path only when
        # it stands alone on the line (a trailing
        # comment is fine). `[SUPPORTED] capability
        # — ...` in quoted sample output is a line of
        # output, not a section.
        m = re.match(
            r"\[\[?([^\]]+)\]\]?\s*(?:[#;].*)?$", line)
        if m:
            prefix = [p.strip().strip("\"'")
                      for p in m.group(1).split(".")]
            key = ".".join(prefix)
            if key and key not in seen:
                seen.add(key)
                out.append((key, base_line + off))
            continue
        m = re.match(
            r"([A-Za-z0-9_-]+(?:\s*\.\s*[A-Za-z0-9_-]+)*)"
            r"\s*=", line)
        if m:
            parts = prefix + [p.strip() for p in
                              m.group(1).split(".")]
            key = ".".join(parts)
            if key and key not in seen:
                seen.add(key)
                out.append((key, base_line + off))
    return out

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on",
    "for", "with", "without", "when", "then", "than",
    "that", "this", "it", "its", "is", "are", "be", "by",
    "as", "at", "from", "into", "must", "shall", "will",
    "should", "can", "may", "only", "any", "all", "each",
    "user", "users", "system", "tool",
}


class SpecItem(object):
    def __init__(self, kind, name, rel, line, text="",
                 default=None, choices=None,
                 method=None, path=None, wildcard=False):
        self.kind = kind            # flag|command|negation|requirement|
                                    # endpoint|endpoint-only|config|
                                    # env|env-negation
        self.name = name            # --x stripped to x | command | ID |
                                    # "POST /items" | config key | env name
        self.rel = rel              # spec file, repo-relative
        self.line = line
        self.text = text            # statement (requirements)
        self.default = default      # stated default (str) or None
        self.choices = choices      # stated choices (list) or None
        self.method = method        # endpoints: HTTP method
        self.path = path            # endpoints: normalised path
        self.wildcard = wildcard    # env-negation: prefix match


def _parse_choices(line, flag_end):
    """Extract a stated choice set for a flag from its
    declaration line: a pipe list (`low|high`, in or out
    of backticks) or an `one of a, b, c` list."""
    rest = line[flag_end:]
    m = re.search(r"(?:one\s+of|choices?\s*(?::|=))\s*(.+)$",
                  rest, re.IGNORECASE)
    segment = m.group(1) if m else rest
    pm = PIPE_CHOICES_RE.search(segment)
    if pm:
        return [c.strip() for c in pm.group(1).split("|")
                if c.strip()]
    if m:
        parts = re.findall(r"`([A-Za-z0-9_+-]+)`", segment)
        if len(parts) >= 2:
            return parts
        parts = [p.strip() for p in
                 re.split(r",|\bor\b", segment) if p.strip()]
        parts = [p.strip("`\"' .") for p in parts]
        parts = [p for p in parts
                 if re.fullmatch(r"[A-Za-z0-9_+-]+", p or " ")]
        if len(parts) >= 2:
            return parts
    return None


def _statement_terms(text):
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    return sorted({w for w in words
                   if len(w) >= 4 and w not in STOPWORDS})


def _logical_lines(text):
    """The spec's lines as (lineno, line) pairs, with
    one repair: a prose line whose backtick spans are
    left open (an odd backtick count) is joined with
    the following prose lines until the spans close,
    so a token split across a line break — a wrapped
    `--fail-on undocumented,untraced` span — is
    extracted whole, cited at its first line. Joining
    never crosses a fence delimiter, a blank line or
    a heading, and never happens inside a fence."""
    raw = text.splitlines()
    out = []
    in_fence = False
    i = 0
    while i < len(raw):
        line = raw[i]
        lineno = i + 1
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            out.append((lineno, line))
            i += 1
            continue
        if in_fence:
            out.append((lineno, line))
            i += 1
            continue
        joined = line
        while joined.count("`") % 2 == 1 \
                and i + 1 < len(raw):
            nxt = raw[i + 1]
            if FENCE_RE.match(nxt.strip()) \
                    or not nxt.strip() \
                    or HEADING_RE.match(nxt.strip()):
                break
            joined = joined + " " + nxt.strip()
            i += 1
        out.append((lineno, joined))
        i += 1
    return out


def _defined_terms(text):
    """Names the document itself defines as its own
    terminology, collected in a pre-pass so a mention
    anywhere is read as vocabulary, not declaration:
    the first-column entries of a definition table (a
    table whose header names a Meaning / Definition /
    Description column), and the terms of a gloss
    enumeration — three or more backticked
    SCREAMING_CASE tokens in one paragraph, each
    followed by a parenthetical gloss (`SUPPORTED`
    (every claim supported), `NO_CLAIMS` (no
    checkable claim), ...). A defined term is not an
    environment variable."""
    terms = set()
    in_fence = False
    in_def_table = False
    paragraph = []

    def scan_paragraph(lines):
        joined = " ".join(lines)
        glossed = []
        for sm in BACKTICK_SPAN_RE.finditer(joined):
            m = ENV_TOKEN_RE.match(sm.group(1).strip())
            if m and not m.group(2):
                rest = joined[sm.end():].lstrip()
                if rest.startswith("("):
                    glossed.append(m.group(1))
        if len(glossed) >= 3:
            terms.update(glossed)

    for line in text.splitlines():
        stripped = line.strip()
        if FENCE_RE.match(stripped):
            scan_paragraph(paragraph)
            paragraph = []
            in_fence = not in_fence
            in_def_table = False
            continue
        if in_fence:
            continue
        if stripped.startswith("|"):
            scan_paragraph(paragraph)
            paragraph = []
            cells = [c.strip().strip("`*_ ")
                     for c in stripped.strip("|")
                     .split("|")]
            if any(c.lower() in DEF_TABLE_HEADER_WORDS
                   for c in cells):
                in_def_table = True
                continue
            if in_def_table and cells:
                m = ENV_TOKEN_RE.match(cells[0])
                if m and not m.group(2):
                    terms.add(m.group(1))
            continue
        in_def_table = False
        if not stripped or HEADING_RE.match(stripped):
            scan_paragraph(paragraph)
            paragraph = []
            continue
        paragraph.append(stripped)
    scan_paragraph(paragraph)
    return terms


def _invocation_command(text):
    """The subcommand named by an invocation-shaped
    line or backticked span (`tool export <file>`),
    or None. The head must be a bare program name
    that is not a generic shell / package tool
    (GENERIC_HEADS), and the line must not carry
    sample-output punctuation (—, ·): `pipx install
    ...` names pipx's operand, and `environment-
    variable reads — none found` is output, not an
    invocation."""
    line = text.strip()
    if line.startswith("$ "):
        line = line[2:].strip()
    if not line or line.startswith("#"):
        return None
    if "—" in line or "·" in line:
        return None
    toks = line.split()
    if len(toks) < 2:
        return None
    head, second = toks[0], toks[1]
    if head.startswith("-") or "/" in head \
            or "=" in head:
        return None
    if head.lower() in GENERIC_HEADS:
        return None
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*",
                        head):
        return None
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", second):
        return None
    return second


def parse_spec_text(text, rel):
    """Extract spec items from one spec file's text.
    Deterministic forms only; everything else is prose
    the tool does not pretend to judge."""
    items = []
    seen_flags = {}
    seen_commands = {}
    seen_reqs = {}
    seen_endpoints = {}
    seen_only = {}
    seen_config = {}
    seen_env = {}
    in_config_section = False
    in_endpoints_section = False
    fence_lang = None
    fence_lines = []
    fence_start = 0
    fence_illustrative = False
    illustrative_level = None
    defined_terms = _defined_terms(text)

    def add_config(key, lineno):
        if key not in seen_config:
            item = SpecItem("config", key, rel, lineno)
            seen_config[key] = item
            items.append(item)

    def add_endpoint(method, raw_path, lineno, only=False):
        path = _norm_path(raw_path.rstrip(".,;)"))
        segments = [s for s in path.split("/") if s]
        concrete = [s for s in segments
                    if not s.startswith(":")]
        if concrete and all(
                s.lower() in PLACEHOLDER_SEGMENTS
                for s in concrete):
            # A template path (`GET /path`,
            # `POST /x`): every concrete segment is
            # a placeholder word. Not a declaration.
            return
        seen = seen_only if only else seen_endpoints
        if (method, path) in seen:
            return
        item = SpecItem(
            "endpoint-only" if only else "endpoint",
            "%s %s" % (method, path), rel, lineno,
            method=method, path=path)
        seen[(method, path)] = item
        items.append(item)

    for lineno, line in _logical_lines(text):
        # Fenced blocks are code, not prose: their
        # lines feed config-key extraction (a fenced
        # TOML/INI block in the spec declares the
        # keys it shows) and invocation mining (a
        # fenced usage line declares the command it
        # runs), never the prose forms.
        fm = FENCE_RE.match(line.strip())
        if fm:
            if fence_lang is None:
                fence_lang = fm.group(1)
                fence_lines = []
                fence_start = lineno
                fence_illustrative = \
                    illustrative_level is not None
            else:
                if not fence_illustrative:
                    block = "\n".join(fence_lines)
                    if fence_lang in ("toml", "ini") or (
                            fence_lang == "" and
                            CONFIG_ASSIGNMENT_RE
                            .search(block)):
                        for key, ln in _config_text_keys(
                                block, fence_start + 1):
                            add_config(key, ln)
                    if fence_lang not in ("toml", "ini"):
                        for off, fline in \
                                enumerate(fence_lines):
                            name = _invocation_command(
                                fline)
                            if name and \
                                    name not in \
                                    seen_commands:
                                item = SpecItem(
                                    "command", name, rel,
                                    fence_start + 1 + off)
                                seen_commands[name] = item
                                items.append(item)
                fence_lang = None
            continue
        if fence_lang is not None:
            fence_lines.append(line)
            continue

        # Headings set the section context the
        # section-scoped forms (config keys, endpoint
        # table rows) depend on — and the illustrative
        # scope: a heading announcing examples or
        # definitions of the notation opens a
        # meta-section (closed by the next heading of
        # the same or a higher level) from which no
        # items are extracted.
        hd = HEADING_RE.match(line.strip())
        if hd:
            htext = hd.group(1).strip().lower()
            level = len(line.strip()) \
                - len(line.strip().lstrip("#"))
            if ILLUSTRATIVE_HEADING_RE.search(htext):
                illustrative_level = level
            elif illustrative_level is not None \
                    and level <= illustrative_level:
                illustrative_level = None
            in_config_section = "config" in htext
            in_endpoints_section = (
                "endpoint" in htext or htext == "api"
                or htext.startswith("api ")
                or " api" in htext)

        # An illustrative line — inside a meta-section,
        # or presenting an example / describing the
        # notation itself — declares nothing.
        if illustrative_level is not None \
                or ILLUSTRATIVE_LINE_RE.search(line):
            continue

        # Text inside straight double quotes is
        # quotation: the document talking about
        # wording (a form it defines, another spec's
        # statement it describes), not wording that
        # declares. Extraction reads the line with
        # quoted regions removed.
        line = DOUBLE_QUOTED_RE.sub("", line)

        # Commands from headings.
        hm = COMMAND_HEADING_RE.match(line.strip())
        if hm:
            name = hm.group(1)
            if name not in seen_commands:
                item = SpecItem("command", name, rel, lineno)
                seen_commands[name] = item
                items.append(item)

        # Endpoint exclusivity items: "only `GET` on
        # `/items`" — the only endpoint form that can
        # be contradicted by presence alone.
        om = ONLY_ENDPOINT_RE.search(line)
        if om:
            add_endpoint(om.group(1).upper(), om.group(2),
                         lineno, only=True)

        # Backticked spans: endpoints (`GET /items`),
        # config keys (`server.port`, in a Config
        # section), env vars (`SCREAMING_CASE`, and
        # env negations like `AWS_*` on a negated
        # line), and commands from invocation lines
        # (`tool export <file>` — first token the
        # tool, second a bare command word).
        for sm in BACKTICK_SPAN_RE.finditer(line):
            span = sm.group(1)
            em = ENDPOINT_SPAN_RE.match(span.strip())
            if em:
                add_endpoint(em.group(1), em.group(2),
                             lineno)
                continue
            if in_config_section and \
                    CONFIG_KEY_RE.match(span.strip()):
                add_config(span.strip(), lineno)
                continue
            vm = ENV_TOKEN_RE.match(span.strip())
            if vm and not (vm.group(1) in HTTP_METHODS
                           and not vm.group(2)):
                base = vm.group(1)
                star = bool(vm.group(2))
                if base in defined_terms \
                        or base.endswith("_CASE"):
                    # The document's own vocabulary
                    # (a verdict name it defines), or a
                    # token naming a capitalisation
                    # style — notation, not a variable.
                    continue
                if base not in seen_env:
                    prefix = line[:sm.start(1) - 1]
                    suffix = line[sm.end(1) + 1:]
                    clause = prefix.rsplit(";", 1)[-1]
                    negated = (
                        "`" not in clause and
                        NEGATION_BEFORE_RE.search(clause)
                    ) or any(r.match(suffix)
                             for r in NEGATION_AFTER_RES)
                    if negated:
                        item = SpecItem(
                            "env-negation",
                            base + ("*" if star else ""),
                            rel, lineno, wildcard=star)
                        seen_env[base] = item
                        items.append(item)
                    elif not star:
                        item = SpecItem("env", base,
                                        rel, lineno)
                        seen_env[base] = item
                        items.append(item)
                    # A bare `AWS_*` pattern mention with
                    # no negation declares no single
                    # variable; it is not an item.
                continue
            name = _invocation_command(span)
            if name and name not in seen_commands:
                item = SpecItem("command", name, rel, lineno)
                seen_commands[name] = item
                items.append(item)

        # Endpoint rows in tables under an
        # Endpoints/API heading, and bare (unbackticked)
        # endpoint lines in the same sections.
        if in_endpoints_section:
            if "|" in line:
                cells = [c.strip().strip("`")
                         for c in line.strip()
                         .strip("|").split("|")]
                method = next(
                    (c for c in cells
                     if c in HTTP_METHODS), None)
                path = next(
                    (c for c in cells
                     if c.startswith("/")), None)
                combined = next(
                    (ENDPOINT_SPAN_RE.match(c)
                     for c in cells
                     if ENDPOINT_SPAN_RE.match(c)), None)
                if combined:
                    add_endpoint(combined.group(1),
                                 combined.group(2), lineno)
                elif method and path:
                    add_endpoint(method, path, lineno)
            else:
                bm = ENDPOINT_BARE_RE.match(line)
                if bm:
                    add_endpoint(bm.group(1), bm.group(2),
                                 lineno)

        # Flags (and flag negations). A flag's stated
        # parameters are read only from its own scope:
        # the text from its token to the next flag
        # token on the line (or end of line), so two
        # flags sharing a line never cross-contaminate.
        flag_matches = list(FLAG_TOKEN_RE.finditer(line))
        for fi, fm in enumerate(flag_matches):
            name = fm.group(1)
            prefix = line[:fm.start()]
            suffix = line[fm.end():]
            scope_end = flag_matches[fi + 1].start() \
                if fi + 1 < len(flag_matches) else len(line)
            scope = line[fm.start():scope_end]
            negated = False
            # A negation word earlier in the same clause,
            # with no other flag named before it.
            clause = prefix.rsplit(";", 1)[-1]
            if "`--" not in clause and \
                    NEGATION_BEFORE_RE.search(clause):
                negated = True
            if any(r.match(suffix)
                   for r in NEGATION_AFTER_RES):
                negated = True
            if negated:
                if name not in seen_flags:
                    item = SpecItem("negation", name,
                                    rel, lineno)
                    seen_flags[name] = item
                    items.append(item)
                continue
            if name in seen_flags:
                continue
            dm = DEFAULT_RE.search(scope)
            item = SpecItem(
                "flag", name, rel, lineno,
                default=dm.group(1) if dm else None,
                choices=_parse_choices(
                    scope, fm.end() - fm.start()))
            seen_flags[name] = item
            items.append(item)

        # Requirement IDs.
        for rm in REQ_ID_RE.finditer(line):
            rid = rm.group(1)
            if rid in seen_reqs:
                continue
            statement = line[rm.end():].strip(" \t—-:·")
            item = SpecItem("requirement", rid, rel, lineno,
                            text=statement)
            seen_reqs[rid] = item
            items.append(item)

    return items


# ------------------------------------------------------------------
# Code index: CLI surfaces via AST (argparse + click)
# ------------------------------------------------------------------

class Surface(object):
    def __init__(self, kind, name, rel, line,
                 default=None, has_default=False,
                 choices=None, default_unresolvable=False,
                 choices_unresolvable=False,
                 method=None, path=None):
        self.kind = kind            # flag | command |
                                    # endpoint | config | env
        self.name = name
        self.rel = rel
        self.line = line
        self.method = method        # endpoints: method,
                                    # "ANY" (Django), or
                                    # None (unresolvable set)
        self.path = path            # endpoints: normalised path
        self.default = default
        self.has_default = has_default
        # choices: list | None. None with
        # choices_unresolvable=False means the code
        # provably imposes NO choice restriction;
        # choices_unresolvable=True means a restriction
        # expression exists but is not statically
        # resolvable, so a stated set is never judged
        # against it (hard-evidence discipline).
        self.choices = choices
        self.default_unresolvable = default_unresolvable
        self.choices_unresolvable = choices_unresolvable


class CodeIndex(object):
    def __init__(self):
        self.surfaces = []          # Surface, discovery order
        self.routes = []            # endpoint Surfaces
        self.config_keys = []       # config Surfaces
        self.env_vars = []          # env Surfaces
        self.flags_complete = True
        self.commands_complete = True
        self.routes_complete = True
        self.config_complete = True
        self.env_complete = True
        self.notes = []
        self.corpus = {}            # rel -> text of every .py file
        self.py_files = 0

    def mark_all_partial(self, rel, why):
        self.flags_complete = False
        self.commands_complete = False
        self.routes_complete = False
        self.config_complete = False
        self.env_complete = False
        self.notes.append(
            "%s: %s — all classes partially enumerated"
            % (rel, why))


def _const(node):
    """(is_constant, value) for an AST node."""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = []
        for elt in node.elts:
            ok, v = _const(elt)
            if not ok:
                return False, None
            vals.append(v)
        return True, vals
    return False, None


def _norm(value):
    return str(value).strip().lower()


def _kwargs(call):
    out = {}
    for kw in call.keywords:
        if kw.arg is not None:
            out[kw.arg] = kw.value
    return out


def _choices_from(node):
    """Resolve a choices expression: a list/tuple of
    constants, or click.Choice([...]). Returns a list of
    strings, or None when not statically resolvable."""
    if node is None:
        return None
    if isinstance(node, ast.Call):
        func = node.func
        is_choice = (isinstance(func, ast.Attribute)
                     and func.attr == "Choice") or \
            (isinstance(func, ast.Name)
             and func.id == "Choice")
        if is_choice and node.args:
            return _choices_from(node.args[0])
        return None
    ok, vals = _const(node)
    if ok and isinstance(vals, list):
        return [str(v) for v in vals]
    return None


def _is_plain_type(node):
    """True for click's unrestricted scalar types
    (STRING / TEXT / UNPROCESSED, as attribute or name,
    or a bare string constant): these provably impose
    no choice restriction. Any other type expression
    (IntRange, a custom ParamType, ...) is a
    restriction whose value set is not statically
    resolvable here."""
    if isinstance(node, ast.Attribute):
        return node.attr in ("STRING", "TEXT", "UNPROCESSED")
    if isinstance(node, ast.Name):
        return node.id in ("STRING", "TEXT", "UNPROCESSED")
    ok, v = _const(node)
    return ok and isinstance(v, str)


def _index_argparse_call(call, rel, idx):
    """One `.add_argument(...)` call."""
    flags = []
    dynamic = False
    for arg in call.args:
        ok, v = _const(arg)
        if ok and isinstance(v, str):
            if v.startswith("-"):
                flags.append(v)
            # a bare word is a positional name — not a
            # --flag surface at this stage.
        else:
            dynamic = True
    if call.args and not flags and not dynamic:
        return  # positional-only declaration
    if dynamic:
        idx.flags_complete = False
        idx.notes.append(
            "%s:%d: add_argument with a non-constant "
            "option string — flags class partially "
            "enumerated" % (rel, call.lineno))
        return
    if not flags:
        return
    kwargs = _kwargs(call)
    default = None
    has_default = False
    default_unresolvable = False
    if "default" in kwargs:
        ok, v = _const(kwargs["default"])
        if ok:
            default, has_default = v, True
        else:
            default_unresolvable = True
    elif "action" in kwargs:
        # argparse's implicit defaults for the boolean
        # actions are provable statically.
        ok, v = _const(kwargs["action"])
        if ok and v in ("store_true", "store_false"):
            default = (v == "store_false")
            has_default = True
    choices = None
    choices_unresolvable = False
    if "choices" in kwargs:
        choices = _choices_from(kwargs["choices"])
        if choices is None:
            choices_unresolvable = True
    for flag in flags:
        if flag.startswith("--"):
            idx.surfaces.append(Surface(
                "flag", flag[2:], rel, call.lineno,
                default=default, has_default=has_default,
                choices=choices,
                default_unresolvable=default_unresolvable,
                choices_unresolvable=choices_unresolvable))


def _index_add_parser(call, rel, idx):
    if not call.args:
        return
    ok, v = _const(call.args[0])
    if ok and isinstance(v, str):
        idx.surfaces.append(
            Surface("command", v, rel, call.lineno))
    else:
        idx.commands_complete = False
        idx.notes.append(
            "%s:%d: add_parser with a non-constant name "
            "— commands class partially enumerated"
            % (rel, call.lineno))


def _decorator_base_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _index_click_function(node, rel, idx):
    """click surfaces from one function's decorators."""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        base = _decorator_base_name(dec.func)
        if base in ("option", "argument"):
            if base == "argument":
                continue  # positionals are not --flags
            flags = []
            dynamic = False
            for arg in dec.args:
                ok, v = _const(arg)
                if ok and isinstance(v, str):
                    if v.startswith("-"):
                        flags.append(v)
                else:
                    dynamic = True
            if dynamic:
                idx.flags_complete = False
                idx.notes.append(
                    "%s:%d: click option with a "
                    "non-constant option string — flags "
                    "class partially enumerated"
                    % (rel, dec.lineno))
                continue
            kwargs = _kwargs(dec)
            default = None
            has_default = False
            default_unresolvable = False
            if "default" in kwargs:
                ok, v = _const(kwargs["default"])
                if ok:
                    default, has_default = v, True
                else:
                    default_unresolvable = True
            choices = None
            choices_unresolvable = False
            if "type" in kwargs:
                choices = _choices_from(kwargs["type"])
                if choices is None and not _is_plain_type(
                        kwargs["type"]):
                    choices_unresolvable = True
            for flag in flags:
                if flag.startswith("--"):
                    idx.surfaces.append(Surface(
                        "flag", flag[2:], rel, dec.lineno,
                        default=default,
                        has_default=has_default,
                        choices=choices,
                        default_unresolvable=
                        default_unresolvable,
                        choices_unresolvable=
                        choices_unresolvable))
        elif base in ("command", "group"):
            name = None
            kwargs = _kwargs(dec)
            if "name" in kwargs:
                ok, v = _const(kwargs["name"])
                if ok and isinstance(v, str):
                    name = v
            if name is None and dec.args:
                ok, v = _const(dec.args[0])
                if ok and isinstance(v, str):
                    name = v
            if name is None:
                name = node.name.replace("_", "-")
            idx.surfaces.append(
                Surface("command", name, rel, dec.lineno))


# --- stage 2 indexers: routes, config keys, env vars ------

ROUTE_METHOD_DECORATORS = {
    "get", "post", "put", "delete", "patch", "head",
    "options", "trace",
}
ROUTE_DECORATORS = ROUTE_METHOD_DECORATORS | {
    "route", "api_route"}


def _add_route(idx, method, path, rel, line):
    label = method if method is not None else "ANY"
    idx.routes.append(Surface(
        "endpoint", "%s %s" % (label, path), rel, line,
        method=method, path=path))


def _prefix_kwarg(call, rel, idx, kwarg, what):
    """(state, value) for a prefix kwarg: "absent",
    "ok" (constant string), or "bad" (present but
    non-constant — deployed paths are then unknowable
    statically, so the routes class goes partial)."""
    kwargs = _kwargs(call)
    if kwarg not in kwargs:
        return "absent", ""
    ok, v = _const(kwargs[kwarg])
    if ok and isinstance(v, str):
        return "ok", v
    idx.routes_complete = False
    idx.notes.append(
        "%s:%d: %s with a non-constant %s — routes "
        "class partially enumerated"
        % (rel, call.lineno, what, kwarg))
    return "bad", ""


def _collect_route_prefixes(tree, rel, idx):
    """Same-module route prefixes, resolved from
    constants only:
    - constructor: bp = Blueprint(..., url_prefix=..)
      or APIRouter(prefix=..)
    - register_blueprint(bp, url_prefix=..) replaces
      the constructor prefix (Flask semantics)
    - include_router(router, prefix=..) appends to
      the constructor prefix (FastAPI semantics)
    Returns ({variable: effective_prefix}, bad) where
    bad holds variables whose prefix is non-constant:
    their routes cannot be honestly pathed at all."""
    ctor, reg, inc, bad = {}, {}, {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) \
                and isinstance(node.value, ast.Call) \
                and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            var = node.targets[0].id
            base = _decorator_base_name(node.value.func)
            kwarg = {"Blueprint": "url_prefix",
                     "APIRouter": "prefix"}.get(base)
            if kwarg:
                state, v = _prefix_kwarg(
                    node.value, rel, idx, kwarg, base)
                if state == "ok":
                    ctor[var] = v
                elif state == "bad":
                    bad.add(var)
        elif isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Attribute) \
                and node.func.attr in (
                    "register_blueprint", "include_router") \
                and node.args \
                and isinstance(node.args[0], ast.Name):
            var = node.args[0].id
            if node.func.attr == "register_blueprint":
                state, v = _prefix_kwarg(
                    node, rel, idx, "url_prefix",
                    "register_blueprint")
                target = reg
            else:
                state, v = _prefix_kwarg(
                    node, rel, idx, "prefix",
                    "include_router")
                target = inc
            if state == "ok":
                target[var] = v
            elif state == "bad":
                bad.add(var)
    out = {}
    for var in set(ctor) | set(reg) | set(inc):
        if var in bad:
            continue
        if var in reg:
            out[var] = reg[var]
        else:
            out[var] = ctor.get(var, "") + inc.get(var, "")
    return out, bad


def _index_route_decorators(node, rel, idx, prefixes):
    """Flask / FastAPI route decorators on one
    function. The receiver may be an app, a Blueprint
    or an APIRouter — the declaration shape is the
    same. A constant same-module prefix for the
    receiver (constructor / registration / include)
    is applied."""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute):
            continue
        attr = func.attr
        if attr not in ROUTE_DECORATORS:
            continue
        if not dec.args:
            continue
        ok, v = _const(dec.args[0])
        if not ok or not isinstance(v, str):
            idx.routes_complete = False
            idx.notes.append(
                "%s:%d: route declared with a "
                "non-constant path — routes class "
                "partially enumerated" % (rel, dec.lineno))
            continue
        if not v.startswith("/"):
            continue
        prefix = ""
        if isinstance(func.value, ast.Name):
            var = func.value.id
            if var in prefixes[1]:
                idx.notes.append(
                    "%s:%d: route on %s, whose prefix "
                    "is not constant — path not indexed"
                    % (rel, dec.lineno, var))
                continue
            prefix = prefixes[0].get(var, "")
        path = _norm_path(prefix + v)
        if attr in ("route", "api_route"):
            kwargs = _kwargs(dec)
            if "methods" in kwargs:
                methods = _choices_from(kwargs["methods"])
                if methods is None:
                    _add_route(idx, None, path, rel,
                               dec.lineno)
                    idx.notes.append(
                        "%s:%d: route %s declares a "
                        "method set that is not "
                        "statically resolvable — its "
                        "methods are not judged"
                        % (rel, dec.lineno, path))
                else:
                    for mth in methods:
                        _add_route(idx, str(mth).upper(),
                                   path, rel, dec.lineno)
            else:
                # Flask's and FastAPI's declared
                # default is GET; the automatic
                # HEAD/OPTIONS are not declarations.
                _add_route(idx, "GET", path, rel,
                           dec.lineno)
        else:
            _add_route(idx, attr.upper(), path, rel,
                       dec.lineno)


def _django_re_path(pattern):
    """Normalise a re_path regex to a comparable path,
    or return None when the pattern is not a literal
    path with named converters (anything richer is
    not honestly enumerable as a path)."""
    p = pattern
    if p.startswith("^"):
        p = p[1:]
    if p.endswith("$"):
        p = p[:-1]
    p = p.replace("\\/", "/")
    p = re.sub(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>[^)]*\)",
               r":\1", p)
    if re.search(r"[\[\](){}\\+*?|^$]", p):
        return None
    return _norm_path(p)


def _index_django(tree, rel, idx):
    """Django `path()` / `re_path()` calls (the caller
    restricts this to files named urls.py). Django
    routes declare no method: ANY-method surfaces."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        base = _decorator_base_name(node.func)
        if base not in ("path", "re_path") or not node.args:
            continue
        ok, v = _const(node.args[0])
        if not ok or not isinstance(v, str):
            idx.routes_complete = False
            idx.notes.append(
                "%s:%d: Django route with a non-constant "
                "pattern — routes class partially "
                "enumerated" % (rel, node.lineno))
            continue
        if base == "path":
            _add_route(idx, "ANY", _norm_path(v), rel,
                       node.lineno)
        else:
            path = _django_re_path(v)
            if path is None:
                idx.routes_complete = False
                idx.notes.append(
                    "%s:%d: re_path pattern is not a "
                    "literal path — routes class "
                    "partially enumerated"
                    % (rel, node.lineno))
            else:
                _add_route(idx, "ANY", path, rel,
                           node.lineno)


def _is_environ_expr(node, from_os):
    if isinstance(node, ast.Attribute) \
            and node.attr == "environ" \
            and isinstance(node.value, ast.Name) \
            and node.value.id == "os":
        return True
    return isinstance(node, ast.Name) \
        and node.id in from_os["environ"]


def _index_env_subscript(node, rel, idx):
    ok, v = _const(node.slice)
    if ok and isinstance(v, str):
        idx.env_vars.append(
            Surface("env", v, rel, node.lineno))
    else:
        idx.env_complete = False
        idx.notes.append(
            "%s:%d: os.environ[...] with a non-constant "
            "name — env class partially enumerated"
            % (rel, node.lineno))


def _index_env_call(node, rel, idx, from_os):
    """os.getenv(...), os.environ.get(...) and their
    from-import spellings, constant-string names."""
    func = node.func
    is_env_call = False
    if isinstance(func, ast.Attribute):
        if func.attr == "getenv" \
                and isinstance(func.value, ast.Name) \
                and func.value.id == "os":
            is_env_call = True
        elif func.attr == "get" and _is_environ_expr(
                func.value, from_os):
            is_env_call = True
    elif isinstance(func, ast.Name) \
            and func.id in from_os["getenv"]:
        is_env_call = True
    if not is_env_call:
        return
    if node.args:
        ok, v = _const(node.args[0])
        if ok and isinstance(v, str):
            idx.env_vars.append(
                Surface("env", v, rel, node.lineno))
            return
    idx.env_complete = False
    idx.notes.append(
        "%s:%d: environment read with a non-constant "
        "name — env class partially enumerated"
        % (rel, node.lineno))


def _is_config_root(node):
    if isinstance(node, ast.Name):
        return node.id == "config"
    if isinstance(node, ast.Attribute):
        return node.attr == "config"
    return False


def _config_expr_parts(node):
    """Key parts for an expression rooted at a
    `config` receiver: a subscript chain
    (config["a"]["b"]) or a .get chain
    (config.get("a").get("b")), or a bare dotted key
    from one .get("a.b"). Returns a list of parts,
    "dynamic" when a key is non-constant, or None
    when the expression is not config-rooted."""
    parts = []
    cur = node
    while True:
        if isinstance(cur, ast.Subscript):
            ok, v = _const(cur.slice)
            if not ok or not isinstance(v, str):
                return "dynamic" if _chain_root(cur) \
                    else None
            parts.append(v)
            cur = cur.value
        elif isinstance(cur, ast.Call) \
                and isinstance(cur.func, ast.Attribute) \
                and cur.func.attr == "get" and cur.args:
            ok, v = _const(cur.args[0])
            if not ok or not isinstance(v, str):
                return "dynamic" if _chain_root(cur) \
                    else None
            parts.extend(reversed(v.split(".")))
            cur = cur.func.value
        else:
            break
    if _is_config_root(cur):
        return list(reversed(parts))
    return None


def _chain_root(node):
    cur = node
    while True:
        if isinstance(cur, ast.Subscript):
            cur = cur.value
        elif isinstance(cur, ast.Call) \
                and isinstance(cur.func, ast.Attribute):
            cur = cur.func.value
        else:
            break
    return _is_config_root(cur)


def _index_config_expr(node, rel, idx):
    parts = _config_expr_parts(node)
    if parts is None:
        return
    if parts == "dynamic":
        idx.config_complete = False
        idx.notes.append(
            "%s:%d: config read with a non-constant key "
            "— config class partially enumerated"
            % (rel, node.lineno))
        return
    if parts:
        idx.config_keys.append(Surface(
            "config", ".".join(parts), rel, node.lineno))


CONFIG_NAME_HINTS = ("config", "settings", "example",
                     "sample")


def _is_config_file(fn):
    low = fn.lower()
    if low in ("pyproject.toml", MANIFEST_NAME):
        return False
    if not low.endswith((".toml", ".ini", ".json",
                         ".yaml", ".yml")):
        return False
    return any(h in low for h in CONFIG_NAME_HINTS)


def _json_config_keys(text):
    """(keys, ok) for a JSON config: every dotted path
    in the object tree, tables included."""
    try:
        data = json.loads(text)
    except ValueError:
        return [], False
    out = []

    def walk(obj, prefix):
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = prefix + (str(k),)
                out.append(".".join(path))
                walk(v, path)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, prefix)

    walk(data, ())
    return out, True


def _index_config_file(path, rel, idx):
    fn = os.path.basename(path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        idx.config_complete = False
        idx.notes.append(
            "%s: unreadable config file — config class "
            "partially enumerated" % rel)
        return
    if fn.lower().endswith((".yaml", ".yml")):
        idx.config_complete = False
        idx.notes.append(
            "%s: YAML config not parsed (no YAML parser "
            "in the standard library) — config class "
            "partially enumerated" % rel)
        return
    if fn.lower().endswith(".json"):
        keys, ok = _json_config_keys(text)
        if not ok:
            idx.config_complete = False
            idx.notes.append(
                "%s: unparseable JSON config — config "
                "class partially enumerated" % rel)
            return
        lines = text.splitlines()
        for key in keys:
            leaf = key.rsplit(".", 1)[-1]
            lineno = next(
                (i + 1 for i, ln in enumerate(lines)
                 if '"%s"' % leaf in ln), 1)
            idx.config_keys.append(
                Surface("config", key, rel, lineno))
        return
    for key, lineno in _config_text_keys(text, 1):
        idx.config_keys.append(
            Surface("config", key, rel, lineno))


def index_code(repo):
    idx = CodeIndex()
    py_paths = []
    config_paths = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            if fn.endswith(".py"):
                py_paths.append(full)
            elif _is_config_file(fn):
                config_paths.append(full)
    for path in sorted(config_paths):
        _index_config_file(
            path, os.path.relpath(path, repo), idx)
    for path in sorted(py_paths):
        rel = os.path.relpath(path, repo)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            idx.mark_all_partial(rel, "unreadable "
                                 "Python file")
            continue
        idx.corpus[rel] = text
        idx.py_files += 1
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError:
            idx.mark_all_partial(rel, "unparseable "
                                 "Python file")
            continue
        if "add_url_rule" in text:
            idx.routes_complete = False
            idx.notes.append(
                "%s: add_url_rule registrations are not "
                "indexed — routes class partially "
                "enumerated" % rel)
        from_os = {"environ": set(), "getenv": set()}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) \
                    and node.module == "os":
                for a in node.names:
                    if a.name in from_os:
                        from_os[a.name].add(
                            a.asname or a.name)
        prefixes = _collect_route_prefixes(tree, rel, idx)
        if os.path.basename(rel) == "urls.py":
            _index_django(tree, rel, idx)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "add_argument":
                        _index_argparse_call(node, rel, idx)
                    elif node.func.attr == "add_parser":
                        _index_add_parser(node, rel, idx)
                _index_env_call(node, rel, idx, from_os)
                _index_config_expr(node, rel, idx)
            elif isinstance(node, ast.Subscript):
                if _is_environ_expr(node.value, from_os):
                    _index_env_subscript(node, rel, idx)
                else:
                    _index_config_expr(node, rel, idx)
            if isinstance(node, (ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                _index_click_function(node, rel, idx)
                _index_route_decorators(node, rel, idx,
                                        prefixes)
    return idx


# --- requirement tracing ---------------------------------------

def _spec_scope(rel):
    """The scope a spec file's requirements are
    traced in: (tree, feature_dir, feature_key).
    tree is the spec's top-level directory ("" for a
    root spec — the whole repo); feature_dir is the
    innermost non-generic directory holding the spec
    (spec-kit's specs/001-user-login/spec.md →
    001-user-login) and feature_key its leading
    token (001) — the qualifier a scoped anchor
    carries, as in `FR-001 (001)`."""
    parts = [p for p in rel.replace(os.sep, "/")
             .split("/") if p not in ("", ".")]
    generic = {"specs", "spec", "docs", "doc",
               "documentation"}
    tree = ""
    if len(parts) >= 2 \
            and parts[0].lower() not in generic:
        tree = parts[0]
    feature_dir = None
    for d in reversed(parts[:-1]):
        if d.lower() not in generic:
            feature_dir = d
            break
    feature_key = None
    if feature_dir:
        tok = re.split(r"[-_]", feature_dir)[0]
        if len(tok) >= 2 and tok.isalnum():
            feature_key = tok
    return tree, feature_dir, feature_key


ANCHOR_NEGATION_RE = re.compile(
    r"\b(no|not|never|without)\b[^.\n]{0,60}"
    r"\b(anchor|anchors|anchored|trace|traced|"
    r"tracing|implement|implemented|implementation|"
    r"cover|covered|coverage)\b"
    r"|\b(unanchored|untraced|unimplemented)\b",
    re.IGNORECASE)


def _refused_anchor_lines(text):
    """Line numbers (1-based) in a code/test file
    that can never serve as a requirement anchor:
    comment blocks that deny anchoring (a block
    stating the listed items have NO anchor is a
    denial, not an anchor) and comment blocks that
    enumerate two or more requirement IDs (a listing
    is bookkeeping; an annotation names one
    requirement). A standalone line carrying the
    denial pattern is refused on its own."""
    refused = set()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            j = i
            block = []
            while j < len(lines) and \
                    lines[j].lstrip().startswith("#"):
                block.append(lines[j])
                j += 1
            btext = "\n".join(block)
            ids = set(REQ_ID_RE.findall(btext))
            if len(ids) >= 2 or \
                    ANCHOR_NEGATION_RE.search(btext):
                refused.update(range(i + 1, j + 1))
            i = j
        else:
            if ANCHOR_NEGATION_RE.search(lines[i]):
                refused.add(i + 1)
            i += 1
    return refused


def _carries_qualifier(line, item_name, feature_key,
                       feature_dir):
    """The line carries the spec's feature qualifier
    OUTSIDE the ID itself: `FR-001 (001)` qualifies,
    a bare `FR-001` does not — the ID's own digits
    are not a qualifier."""
    if feature_dir and feature_dir in line:
        return True
    if feature_key:
        rest = line.replace(item_name, " ")
        return re.search(
            r"(?<![A-Za-z0-9])" + re.escape(feature_key)
            + r"(?![A-Za-z0-9])", rest) is not None
    return False


def trace_requirement(item, idx, unique_in_tree=True):
    """Find an anchor for a requirement ID, scoped
    to the ID's own spec. Returns (traced_bool,
    basis, code_ref_or_None).

    Verbatim anchors are tiered: (0) a line carrying
    the spec's feature qualifier (`FR-001 (001)`);
    (1) a line in a file whose path names the
    feature; (2) any line in the spec's tree — only
    when the ID is declared by a single spec file in
    that tree (unique_in_tree), because an ID three
    features declare cannot be told apart unscoped.
    Lines in negating or ID-enumerating comment
    blocks are never anchors, and mentions outside
    the tree never count. Failing a verbatim anchor,
    >= 2 distinctive statement terms co-occurring in
    one in-tree file trace the requirement (terms
    filtered by document frequency: a term present
    in over half the scoped files is not
    distinctive) — read with the refused lines
    blanked, so a denial that restates the
    requirement's words is not term evidence."""
    tree, feature_dir, feature_key = _spec_scope(
        item.rel)

    def in_tree(rel):
        return not tree or rel == tree \
            or rel.startswith(tree + "/")

    refused = {}
    candidates = []
    saw_unusable = False
    for rel in sorted(idx.corpus):
        text = idx.corpus[rel]
        if rel not in refused:
            refused[rel] = _refused_anchor_lines(text)
        for lineno, line in enumerate(
                text.splitlines(), 1):
            if item.name not in line:
                continue
            if lineno in refused[rel] \
                    or not in_tree(rel):
                saw_unusable = True
                continue
            if _carries_qualifier(line, item.name,
                                  feature_key,
                                  feature_dir):
                tier = 0
            elif feature_dir and (
                    feature_dir in rel
                    or (feature_key
                        and feature_key in rel)):
                tier = 1
            elif unique_in_tree:
                tier = 2
            else:
                saw_unusable = True
                continue
            candidates.append((tier, rel, lineno))
    if candidates:
        candidates.sort()
        _, rel, lineno = candidates[0]
        return True, (
            "ID %s appears verbatim in code/tests"
            % item.name), (rel, lineno)
    terms = _statement_terms(item.text)
    if len(terms) < 2 or not idx.corpus:
        return False, (
            "no verbatim ID and fewer than two "
            "distinctive statement terms to trace"), None
    # Term evidence is read with refused lines
    # blanked: a denial comment that restates the
    # requirement's own words ("no anchor: locks the
    # account after failed attempts") is not
    # evidence the terms are carried by the code.
    scoped = {}
    for rel, text in idx.corpus.items():
        if not in_tree(rel):
            continue
        rlines = refused.get(rel)
        if rlines:
            ls = text.splitlines()
            for n in rlines:
                if 1 <= n <= len(ls):
                    ls[n - 1] = ""
            scoped[rel] = "\n".join(ls)
        else:
            scoped[rel] = text
    nfiles = len(scoped)
    df = {t: sum(1 for text in scoped.values()
                 if t in _file_terms(text)) for t in terms}
    distinctive = [t for t in terms
                   if df[t] <= max(1, nfiles // 2)]
    if len(distinctive) < 2:
        return False, (
            "no verbatim ID; statement terms are too "
            "common in this codebase to trace"), None
    for rel in sorted(scoped):
        present = [t for t in distinctive
                   if t in _file_terms(scoped[rel])]
        if len(present) >= 2:
            lines = scoped[rel].splitlines()
            lineno = next(
                (i + 1 for i, ln in enumerate(lines)
                 if any(t in ln.lower() for t in present)),
                1)
            return True, (
                "distinctive terms %s co-occur in one "
                "file" % ", ".join(present[:3])), (rel, lineno)
    if saw_unusable:
        return False, (
            "the ID appears only outside this spec's "
            "feature scope, or in contexts that deny "
            "the anchor; and no in-scope file carries "
            "two distinctive statement terms"), None
    return False, (
        "no verbatim ID and no file carries two "
        "distinctive statement terms"), None


def _file_terms(text):
    return set(re.findall(r"[a-z][a-z0-9]+", text.lower()))


# ------------------------------------------------------------------
# Verdict engine
# ------------------------------------------------------------------

def _surface_verdicts(item, surf, class_complete):
    """Verdict for a flag item against its found surface
    (or None). Returns (verdict, basis)."""
    if surf is None:
        if class_complete:
            return "MISSING", (
                "the flags class was completely "
                "enumerated and --%s is absent from it"
                % item.name)
        return "UNTRACED", (
            "the flags class was only partially "
            "enumerated, so absence cannot be proven — "
            "UNTRACED, never MISSING, on partial "
            "evidence")
    # Found. Parameter statements are hard checks —
    # but a parameter whose code value is not
    # statically resolvable is never judged (hard
    # evidence only); it is noted, not contradicted.
    unverifiable = []
    if item.default is not None:
        if surf.default_unresolvable:
            unverifiable.append("default")
        else:
            code_default = surf.default if surf.has_default \
                else None
            if _norm(code_default) != _norm(item.default):
                return "CONTRADICTED", (
                    "spec states default `%s`; code "
                    "default is `%s`"
                    % (item.default, code_default))
    if item.choices is not None:
        if surf.choices_unresolvable:
            unverifiable.append("choices")
        elif surf.choices is None:
            return "CONTRADICTED", (
                "spec restricts --%s to %s; the code "
                "imposes no choice restriction"
                % (item.name, "|".join(item.choices)))
        elif sorted(map(str, surf.choices)) != \
                sorted(map(str, item.choices)):
            return "CONTRADICTED", (
                "spec choices %s; code choices %s"
                % ("|".join(item.choices),
                   "|".join(map(str, surf.choices))))
    basis = "surface declared in code"
    if unverifiable:
        basis += ("; stated %s not statically "
                  "resolvable in code — not judged"
                  % " and ".join(unverifiable))
    elif item.default is not None or item.choices is not None:
        basis += "; stated default and choices match"
    return "IMPLEMENTED", basis


def _command_verdict(item, surf, class_complete):
    if surf is not None:
        return "IMPLEMENTED", "command declared in code"
    if class_complete:
        return "MISSING", (
            "the commands class was completely "
            "enumerated and command `%s` is absent from "
            "it" % item.name)
    return "UNTRACED", (
        "the commands class was only partially "
        "enumerated, so absence cannot be proven")


def _endpoint_verdict(item, routes, route_paths,
                      class_complete):
    """Verdict for an endpoint item. Returns
    (verdict, basis, surface_or_None). The pair
    (method, path) is the identity; a path registered
    with other methods only is a direct conflict
    (CONTRADICTED), a path absent entirely is
    MISSING — the SPEC's own evidence bars."""
    surf = routes.get((item.method, item.path))
    if surf is not None:
        return "IMPLEMENTED", (
            "route declared in code with this method"), surf
    any_surf = routes.get(("ANY", item.path))
    if any_surf is not None:
        return "IMPLEMENTED", (
            "declared as a method-agnostic (Django) "
            "route; the method is not restricted at "
            "the URL layer"), any_surf
    cohort = route_paths.get(item.path, [])
    unres = [s for s in cohort if s.method is None]
    if unres:
        return "IMPLEMENTED", (
            "the path is declared in code; its method "
            "set is not statically resolvable, so the "
            "method is not judged"), unres[0]
    if cohort:
        methods = sorted({s.method for s in cohort
                          if s.method})
        return "CONTRADICTED", (
            "spec states %s %s; the path is registered "
            "in code with %s only"
            % (item.method, item.path,
               "|".join(methods))), cohort[0]
    if class_complete:
        return "MISSING", (
            "the routes class was completely enumerated "
            "and %s %s is absent from it"
            % (item.method, item.path)), None
    return "UNTRACED", (
        "the routes class was only partially "
        "enumerated, so absence cannot be proven — "
        "UNTRACED, never MISSING, on partial "
        "evidence"), None


def _endpoint_only_verdict(item, route_paths,
                           class_complete):
    """Verdict for an endpoint exclusivity item
    ("only GET on /items"): contradicted by any
    other explicitly declared method on the path."""
    cohort = route_paths.get(item.path, [])
    offending = [s for s in cohort
                 if s.method not in (None, "ANY",
                                     item.method)]
    if offending:
        return "CONTRADICTED", (
            "spec allows only %s on %s; code also "
            "registers %s"
            % (item.method, item.path,
               offending[0].method)), offending[0]
    if any(s.method in (None, "ANY") for s in cohort):
        return "UNTRACED", (
            "the path is declared with a method set "
            "that cannot be enumerated (method-agnostic "
            "or unresolvable), so exclusivity cannot "
            "be proven"), cohort[0]
    if class_complete:
        return "IMPLEMENTED", (
            "exclusivity holds: no other method is "
            "registered on %s in the completely "
            "enumerated routes class" % item.path), \
            cohort[0] if cohort else None
    return "UNTRACED", (
        "the routes class was only partially "
        "enumerated, so exclusivity cannot be "
        "proven"), None


def _simple_surface_verdict(item, surfaces, complete,
                            class_label):
    """Verdict for a config-key or env item: presence
    in the class index, with the stage-1 absence
    discipline."""
    surf = surfaces.get(item.name)
    if surf is not None:
        return "IMPLEMENTED", (
            "declared in the %s index (example config "
            "or code read)" % class_label
            if class_label == "config" else
            "read in code"), surf
    if complete:
        return "MISSING", (
            "the %s class was completely enumerated "
            "and %s is absent from it"
            % (class_label, item.name)), None
    return "UNTRACED", (
        "the %s class was only partially enumerated, "
        "so absence cannot be proven — UNTRACED, "
        "never MISSING, on partial evidence"
        % class_label), None


def check_repo(repo, spec_args=None, fail_on=()):
    repo = os.path.abspath(repo)
    if not os.path.exists(repo):
        raise ToolError("no such folder: %s" % repo)
    if not os.path.isdir(repo):
        raise ToolError("not a folder: %s" % repo)

    spec_paths = find_spec_files(repo, spec_args)
    items = []
    spec_rels = []
    for sp in spec_paths:
        rel = os.path.relpath(sp, repo)
        spec_rels.append(rel)
        items.extend(parse_spec_text(
            _read_text(sp, "spec file"), rel))

    idx = index_code(repo)
    # How many spec files per (tree, ID) declare each
    # requirement: an ID declared by several specs in
    # one tree can only be traced to a scoped anchor.
    req_counts = {}
    for it in items:
        if it.kind == "requirement":
            key = (_spec_scope(it.rel)[0], it.name)
            req_counts[key] = req_counts.get(key, 0) + 1
    flags = {s.name: s for s in idx.surfaces
             if s.kind == "flag"}
    commands = {s.name: s for s in idx.surfaces
                if s.kind == "command"}
    routes = {}
    route_paths = {}
    for s in idx.routes:
        routes.setdefault((s.method, s.path), s)
        route_paths.setdefault(s.path, []).append(s)
    configs = {}
    for s in idx.config_keys:
        configs.setdefault(s.name, s)
    envs = {}
    for s in idx.env_vars:
        envs.setdefault(s.name, s)

    item_rows = []
    for item in items:
        row = {
            "kind": item.kind,
            "name": item.name,
            "spec": {"file": item.rel, "line": item.line},
            "code": None,
        }
        if item.kind == "flag":
            surf = flags.get(item.name)
            verdict, basis = _surface_verdicts(
                item, surf, idx.flags_complete)
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
            if item.default is not None:
                row["stated_default"] = item.default
            if item.choices is not None:
                row["stated_choices"] = item.choices
        elif item.kind == "command":
            surf = commands.get(item.name)
            verdict, basis = _command_verdict(
                item, surf, idx.commands_complete)
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
        elif item.kind == "negation":
            surf = flags.get(item.name)
            if surf is not None:
                verdict = "CONTRADICTED"
                basis = ("spec states there is no --%s; "
                         "the flag is declared in code"
                         % item.name)
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
            elif idx.flags_complete:
                verdict = "IMPLEMENTED"
                basis = ("negation holds: --%s is absent "
                         "from the completely enumerated "
                         "flags class" % item.name)
            else:
                verdict = "UNTRACED"
                basis = ("the flags class was only "
                         "partially enumerated, so the "
                         "negation cannot be proven to hold")
        elif item.kind == "requirement":
            row["text"] = item.text
            traced, basis, ref = trace_requirement(
                item, idx,
                unique_in_tree=req_counts.get(
                    (_spec_scope(item.rel)[0],
                     item.name), 1) == 1)
            verdict = "TRACED" if traced else "UNTRACED"
            if ref is not None:
                row["code"] = {"file": ref[0], "line": ref[1]}
        elif item.kind == "endpoint":
            row["method"] = item.method
            row["path"] = item.path
            verdict, basis, surf = _endpoint_verdict(
                item, routes, route_paths,
                idx.routes_complete)
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
        elif item.kind == "endpoint-only":
            row["method"] = item.method
            row["path"] = item.path
            verdict, basis, surf = _endpoint_only_verdict(
                item, route_paths, idx.routes_complete)
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
        elif item.kind == "config":
            verdict, basis, surf = \
                _simple_surface_verdict(
                    item, configs, idx.config_complete,
                    "config")
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
        elif item.kind == "env":
            verdict, basis, surf = \
                _simple_surface_verdict(
                    item, envs, idx.env_complete, "env")
            if surf is not None:
                row["code"] = {"file": surf.rel,
                               "line": surf.line}
        elif item.kind == "env-negation":
            if item.wildcard:
                row["wildcard"] = True
            prefix = item.name.rstrip("*")
            match = None
            for s in idx.env_vars:
                if s.name == item.name or (
                        item.wildcard
                        and s.name.startswith(prefix)):
                    match = s
                    break
            if match is not None:
                verdict = "CONTRADICTED"
                basis = ("spec states %s is never read; "
                         "%s is read in code"
                         % (item.name, match.name))
                row["code"] = {"file": match.rel,
                               "line": match.line}
            elif idx.env_complete:
                verdict = "IMPLEMENTED"
                basis = ("negation holds: no matching "
                         "variable is read in the "
                         "completely enumerated env class")
            else:
                verdict = "UNTRACED"
                basis = ("the env class was only "
                         "partially enumerated, so the "
                         "negation cannot be proven to hold")
        row["verdict"] = verdict
        row["basis"] = basis
        item_rows.append(row)

    named_flags = {i.name for i in items
                   if i.kind in ("flag", "negation")}
    named_commands = {i.name for i in items
                      if i.kind == "command"}
    named_endpoints = {(i.method, i.path) for i in items
                       if i.kind in ("endpoint",
                                       "endpoint-only")}
    named_endpoint_paths = {i.path for i in items
                            if i.kind in ("endpoint",
                                            "endpoint-only")}
    named_config = {i.name for i in items
                    if i.kind == "config"}
    named_env = {i.name for i in items if i.kind == "env"}
    env_negations = [i for i in items
                     if i.kind == "env-negation"]
    spec_cite = {}
    for i in items:
        if i.kind in ("flag", "negation"):
            spec_cite[("flag", i.name)] = i
        elif i.kind == "command":
            spec_cite[("command", i.name)] = i
        elif i.kind in ("endpoint", "endpoint-only"):
            spec_cite.setdefault(
                ("endpoint", i.method, i.path), i)
            spec_cite.setdefault(
                ("endpoint-path", i.path), i)
        elif i.kind == "config":
            spec_cite[("config", i.name)] = i
        elif i.kind == "env":
            spec_cite[("env", i.name)] = i
        elif i.kind == "env-negation":
            spec_cite.setdefault(("env-neg", i.name), i)

    def surface_documented(surf):
        """The spec citation for a surface, or None."""
        if surf.kind == "flag":
            return spec_cite.get(("flag", surf.name))
        if surf.kind == "command":
            return spec_cite.get(("command", surf.name))
        if surf.kind == "config":
            return spec_cite.get(("config", surf.name))
        if surf.kind == "env":
            src = spec_cite.get(("env", surf.name))
            if src is not None:
                return src
            for neg in env_negations:
                if surf.name == neg.name or (
                        neg.wildcard and surf.name.startswith(
                            neg.name.rstrip("*"))):
                    return neg
            return None
        if surf.kind == "endpoint":
            src = spec_cite.get(
                ("endpoint", surf.method, surf.path))
            if src is not None:
                return src
            if surf.method in ("ANY", None):
                # A method-agnostic or unresolvable
                # surface is named when any spec item
                # names its path.
                return spec_cite.get(
                    ("endpoint-path", surf.path))
            return None
        return None

    all_surfaces = list(idx.surfaces) \
        + list(routes.values()) + list(configs.values()) \
        + list(envs.values())
    surface_rows = []
    for surf in all_surfaces:
        row = {
            "kind": surf.kind,
            "name": surf.name,
            "code": {"file": surf.rel, "line": surf.line},
            "spec": None,
        }
        if surf.kind == "endpoint":
            row["method"] = surf.method
            row["path"] = surf.path
        src = surface_documented(surf)
        if src is not None:
            row["verdict"] = "DOCUMENTED"
            row["spec"] = {"file": src.rel, "line": src.line}
            row["basis"] = "named by a spec item"
        else:
            row["verdict"] = "UNDOCUMENTED"
            row["basis"] = (
                "declared in code; no spec item names "
                "this %s" % surf.kind)
        surface_rows.append(row)

    def count(rows, verdict):
        return sum(1 for r in rows
                   if r["verdict"] == verdict)

    summary = {
        "items": len(item_rows),
        "implemented": count(item_rows, "IMPLEMENTED"),
        "missing": count(item_rows, "MISSING"),
        "contradicted": count(item_rows, "CONTRADICTED"),
        "traced": count(item_rows, "TRACED"),
        "untraced": count(item_rows, "UNTRACED"),
        "surfaces": len(surface_rows),
        "documented": count(surface_rows, "DOCUMENTED"),
        "undocumented": count(surface_rows, "UNDOCUMENTED"),
    }

    failing = summary["missing"] > 0 \
        or summary["contradicted"] > 0
    if "undocumented" in fail_on and \
            summary["undocumented"] > 0:
        failing = True
    if "untraced" in fail_on and summary["untraced"] > 0:
        failing = True

    return {
        "tool": TOOL,
        "version": VERSION,
        "command": "check",
        "repo": repo,
        "repo_name": os.path.basename(repo),
        "spec_files": spec_rels,
        "classes": {
            "flags": {
                "complete": idx.flags_complete,
                "surfaces": len(flags),
            },
            "commands": {
                "complete": idx.commands_complete,
                "surfaces": len(commands),
            },
            "routes": {
                "complete": idx.routes_complete,
                "surfaces": len(routes),
            },
            "config": {
                "complete": idx.config_complete,
                "surfaces": len(configs),
            },
            "env": {
                "complete": idx.env_complete,
                "surfaces": len(envs),
            },
        },
        "index_notes": idx.notes,
        "items": item_rows,
        "surfaces": surface_rows,
        "summary": summary,
        "fail_on": sorted(fail_on),
        "verdict": "DRIFT" if failing else "NO_DRIFT",
        "verdict_rule": (
            "DRIFT when >= 1 item is MISSING or "
            "CONTRADICTED"
            + ("; UNDOCUMENTED/UNTRACED promoted to "
               "failing by --fail-on"
               if fail_on else
               "; UNDOCUMENTED and UNTRACED are reported "
               "but do not fail by default")),
        "limits": LIMITS,
    }


# ------------------------------------------------------------------
# Text receipt
# ------------------------------------------------------------------

def _cite(ref):
    return "%s:%d" % (ref["file"], ref["line"])


def _class_phrase(label, entry):
    n = entry["surfaces"]
    return "%s %s (%d surface%s)" % (
        label, "complete" if entry["complete"]
        else "PARTIAL", n, "s" if n != 1 else "")


def _item_label(it):
    if it["kind"] in ("flag", "negation"):
        return "--" + it["name"]
    if it["kind"] == "endpoint-only":
        return "only " + it["name"]
    return it["name"]


def render_text(r):
    lines = []
    lines.append("%s %s — spec drift receipt"
                 % (TOOL, r["version"]))
    lines.append("Repo: %s" % r["repo_name"])
    lines.append("Spec: %s"
                 % ", ".join(r["spec_files"]))
    lines.append("Classes: " + ", ".join(
        _class_phrase(label, r["classes"][key])
        for label, key in (("flags", "flags"),
                           ("commands", "commands"),
                           ("routes", "routes"),
                           ("config", "config"),
                           ("env", "env"))))
    for note in r["index_notes"]:
        lines.append("  note: %s" % note)
    lines.append("")
    lines.append("SPEC ITEMS")
    for it in r["items"]:
        label = _item_label(it)
        code = _cite(it["code"]) if it["code"] else "—"
        lines.append("%-12s %-13s %-22s spec %s · code %s"
                     % (it["verdict"], it["kind"], label,
                        _cite(it["spec"]), code))
        lines.append("             %s" % it["basis"])
    lines.append("")
    lines.append("CODE SURFACES")
    for s in r["surfaces"]:
        label = ("--" + s["name"]) if s["kind"] == "flag" \
            else s["name"]
        spec = _cite(s["spec"]) if s["spec"] else "—"
        lines.append("%-12s %-13s %-22s code %s · spec %s"
                     % (s["verdict"], s["kind"], label,
                        _cite(s["code"]), spec))
    lines.append("")
    sm = r["summary"]
    lines.append(
        "Summary: %d items (IMPLEMENTED %d, MISSING %d, "
        "CONTRADICTED %d, TRACED %d, UNTRACED %d); "
        "%d surfaces (DOCUMENTED %d, UNDOCUMENTED %d)"
        % (sm["items"], sm["implemented"], sm["missing"],
           sm["contradicted"], sm["traced"], sm["untraced"],
           sm["surfaces"], sm["documented"],
           sm["undocumented"]))
    lines.append("Verdict: %s" % r["verdict"])
    lines.append("")
    lines.append("Limits:")
    for lim in r["limits"]:
        lines.append("  - %s" % lim)
    return "\n".join(lines)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON
    in --json mode ({"error": ...} on stdout, same exit
    code 2), matching the house error contract."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            raise SystemExit(2)
        super().error(message)


def _parse_fail_on(raw):
    if not raw:
        return ()
    names = [n.strip().lower() for n in raw.split(",")
             if n.strip()]
    bad = [n for n in names
           if n not in ("undocumented", "untraced")]
    if bad:
        raise ToolError(
            "--fail-on accepts only: undocumented, "
            "untraced (got: %s)" % ", ".join(bad))
    return tuple(names)


def cmd_check(args):
    fail_on = _parse_fail_on(args.fail_on)
    result = check_repo(args.repo, args.spec, fail_on)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(render_text(result))
    return 1 if result["verdict"] == "DRIFT" else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    _JsonAwareParser.json_mode = "--json" in argv
    ap = _JsonAwareParser(
        prog=TOOL,
        description="Receipt a repo's design spec "
                    "against its declared implementation "
                    "surfaces: extract spec items, index "
                    "argparse/click surfaces, Flask/"
                    "FastAPI/Django routes, config keys "
                    "and environment reads from the code "
                    "by AST, and print a per-item verdict "
                    "— IMPLEMENTED, MISSING, CONTRADICTED, "
                    "TRACED or UNTRACED — plus DOCUMENTED / "
                    "UNDOCUMENTED per code surface, with "
                    "file-and-line evidence on both "
                    "sides. Deterministic, local, "
                    "read-only, executes nothing (stdlib "
                    "only). Stage 3.")
    sub = ap.add_subparsers(dest="command", required=True,
                            parser_class=_JsonAwareParser)

    p_check = sub.add_parser(
        "check", help="check one repo against its spec: "
                      "exit 1 on drift (MISSING or "
                      "CONTRADICTED), 0 when clean, 2 for "
                      "input errors")
    p_check.add_argument("repo",
                         help="the repo's root folder")
    p_check.add_argument(
        "--spec", action="append", default=None,
        metavar="PATH",
        help="spec file (repeatable); overrides the "
             "manifest and auto-discovery")
    p_check.add_argument(
        "--fail-on", default=None, metavar="LIST",
        help="comma list of report-only verdicts to "
             "promote to failing: undocumented,untraced")
    p_check.add_argument("--json", action="store_true",
                         help="print one JSON object "
                              "instead of text")
    p_check.set_defaults(func=cmd_check)

    a = ap.parse_args(argv)

    def fail(msg):
        if getattr(a, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print("ERROR: %s" % msg)
        return 2

    try:
        return a.func(a)
    except ToolError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
