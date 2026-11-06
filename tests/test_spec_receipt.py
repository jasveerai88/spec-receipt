"""Tests for spec-receipt (stage 3: CLI surfaces, web
routes, config keys, environment variables, plus
extraction scoping and scoped requirement tracing).

Run: python3 -m unittest discover -s tests
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PACK, "src"))

import spec_receipt as sr  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
CLEAN = os.path.join(FIXTURES, "clean")
DRIFTED = os.path.join(FIXTURES, "drifted")


def run_main(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = sr.main(argv)
    return code, buf.getvalue()


def make_repo(files):
    """files: {relpath: text} -> temp dir path."""
    root = tempfile.mkdtemp(prefix="spec-receipt-test-")
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


def tree_hash(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__"]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = \
                    hashlib.sha256(fh.read()).hexdigest()
    return out


class SpecParsingTests(unittest.TestCase):
    def items(self, text):
        return sr.parse_spec_text(text, "SPEC.md")

    def test_flag_with_default(self):
        items = self.items("- `--mode` (default: `fast`)\n")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].kind, "flag")
        self.assertEqual(items[0].name, "mode")
        self.assertEqual(items[0].default, "fast")
        self.assertIsNone(items[0].choices)

    def test_flag_with_pipe_choices(self):
        items = self.items("- `--level` one of `low|high`\n")
        self.assertEqual(items[0].choices, ["low", "high"])

    def test_flag_with_list_choices(self):
        items = self.items(
            "- `--level` one of `low`, `high`\n")
        self.assertEqual(items[0].choices, ["low", "high"])

    def test_flag_plain(self):
        items = self.items("- `--verbose` — extra logging.\n")
        self.assertEqual(items[0].kind, "flag")
        self.assertIsNone(items[0].default)
        self.assertIsNone(items[0].choices)

    def test_negation_before(self):
        items = self.items("- There is no `--legacy` flag.\n")
        self.assertEqual(items[0].kind, "negation")
        self.assertEqual(items[0].name, "legacy")

    def test_negation_after(self):
        items = self.items("- `--legacy` is not supported.\n")
        self.assertEqual(items[0].kind, "negation")

    def test_no_false_negation_from_note(self):
        items = self.items(
            "- Note: `--verbose` prints more output.\n")
        self.assertEqual(items[0].kind, "flag")

    def test_command_heading(self):
        items = self.items("## Command: export\n")
        self.assertEqual(items[0].kind, "command")
        self.assertEqual(items[0].name, "export")

    def test_command_invocation_line(self):
        items = self.items(
            "Run `tool export <file>` to export.\n")
        cmds = [i for i in items if i.kind == "command"]
        self.assertEqual([c.name for c in cmds], ["export"])

    def test_requirement_ids(self):
        items = self.items(
            "- FR-001 — Does a thing.\n"
            "- REQ-auth — Checks a token.\n"
            "- R-12 — Watches a folder.\n")
        self.assertEqual([i.name for i in items],
                         ["FR-001", "REQ-auth", "R-12"])
        self.assertTrue(all(
            i.kind == "requirement" for i in items))

    def test_duplicate_flag_deduped(self):
        items = self.items("- `--mode`\n- `--mode`\n")
        self.assertEqual(len(items), 1)


class CodeIndexTests(unittest.TestCase):
    def index(self, source):
        root = make_repo({"app.py": source})
        self.addCleanup(shutil.rmtree, root, True)
        return sr.index_code(root)

    def test_argparse_flags_defaults_choices(self):
        idx = self.index(
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--mode', default='fast')\n"
            "p.add_argument('--level', choices=['low', 'high'])\n")
        flags = {s.name: s for s in idx.surfaces
                 if s.kind == "flag"}
        self.assertEqual(flags["mode"].default, "fast")
        self.assertEqual(flags["level"].choices,
                         ["low", "high"])
        self.assertTrue(idx.flags_complete)

    def test_argparse_subcommand(self):
        idx = self.index(
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "s = p.add_subparsers()\n"
            "s.add_parser('export')\n")
        cmds = [s.name for s in idx.surfaces
                if s.kind == "command"]
        self.assertEqual(cmds, ["export"])

    def test_dynamic_flag_marks_partial(self):
        idx = self.index(
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "FLAG = '--computed'\n"
            "p.add_argument(FLAG)\n")
        self.assertFalse(idx.flags_complete)

    def test_dynamic_subcommand_marks_partial(self):
        idx = self.index(
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "s = p.add_subparsers()\n"
            "s.add_parser(NAME)\n")
        self.assertFalse(idx.commands_complete)

    def test_click_option_and_command(self):
        idx = self.index(
            "import click\n\n"
            "@click.command()\n"
            "@click.option('--mode', default='fast')\n"
            "def cli(mode):\n    pass\n")
        flags = {s.name: s for s in idx.surfaces
                 if s.kind == "flag"}
        cmds = [s.name for s in idx.surfaces
                if s.kind == "command"]
        self.assertEqual(flags["mode"].default, "fast")
        self.assertEqual(cmds, ["cli"])
        self.assertTrue(idx.flags_complete)

    def test_click_choice(self):
        idx = self.index(
            "import click\n\n"
            "@click.command()\n"
            "@click.option('--level', type=click.Choice(['low', 'high']))\n"
            "def cli(level):\n    pass\n")
        flags = {s.name: s for s in idx.surfaces
                 if s.kind == "flag"}
        self.assertEqual(flags["level"].choices,
                         ["low", "high"])

    def test_unparseable_file_marks_partial(self):
        idx = self.index("def broken(:\n")
        self.assertFalse(idx.flags_complete)
        self.assertFalse(idx.commands_complete)


class FixtureTests(unittest.TestCase):
    def test_clean_fixture_exact(self):
        # Stage-2 multiset (SPEC section 6, fixture 1):
        # the stage-1 seven items plus 2 endpoints,
        # 5 config keys, 2 env vars and 1 env negation
        # — every item IMPLEMENTED or TRACED, every
        # surface DOCUMENTED.
        code, out = run_main(["check", CLEAN, "--json"])
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual(r["verdict"], "NO_DRIFT")
        self.assertEqual(r["summary"], {
            "items": 17, "implemented": 15, "missing": 0,
            "contradicted": 0, "traced": 2, "untraced": 0,
            "surfaces": 13, "documented": 13,
            "undocumented": 0})
        # The release blocker (stages 1 and 2): no
        # CONTRADICTED may ever appear on the clean
        # fixture.
        self.assertFalse(any(
            i["verdict"] == "CONTRADICTED"
            for i in r["items"]))

    def test_clean_fixture_text_run(self):
        code, out = run_main(["check", CLEAN])
        self.assertEqual(code, 0)
        self.assertIn("Verdict: NO_DRIFT", out)

    def test_drifted_fixture_exact_multiset(self):
        # Stage-2 multiset (SPEC section 6, fixture 2):
        # the stage-1 drift plus the endpoint whose
        # method changed POST -> GET (CONTRADICTED);
        # config and env surfaces match the frozen
        # spec exactly, so they add IMPLEMENTED items
        # and DOCUMENTED surfaces only.
        code, out = run_main(["check", DRIFTED, "--json"])
        self.assertEqual(code, 1)
        r = json.loads(out)
        self.assertEqual(r["verdict"], "DRIFT")
        self.assertEqual(r["summary"], {
            "items": 17, "implemented": 11, "missing": 1,
            "contradicted": 3, "traced": 1, "untraced": 1,
            "surfaces": 13, "documented": 12,
            "undocumented": 1})
        by_name = {(i["kind"], i["name"]): i["verdict"]
                   for i in r["items"]}
        self.assertEqual(by_name[("flag", "mode")],
                         "CONTRADICTED")
        self.assertEqual(by_name[("flag", "level")],
                         "MISSING")
        self.assertEqual(by_name[("negation", "legacy")],
                         "CONTRADICTED")
        self.assertEqual(
            by_name[("requirement", "FR-002")], "UNTRACED")
        self.assertEqual(
            by_name[("endpoint", "POST /items")],
            "CONTRADICTED")
        self.assertEqual(
            by_name[("endpoint", "GET /items")],
            "IMPLEMENTED")
        self.assertEqual(
            by_name[("env-negation", "AWS_*")],
            "IMPLEMENTED")
        # Both citations on every CONTRADICTED/MISSING.
        for i in r["items"]:
            self.assertIn("file", i["spec"])
            self.assertIn("line", i["spec"])
            if i["verdict"] == "CONTRADICTED":
                self.assertIsNotNone(i["code"])
        undoc = [s for s in r["surfaces"]
                 if s["verdict"] == "UNDOCUMENTED"]
        self.assertEqual([s["name"] for s in undoc],
                         ["extra"])

    def test_read_only_proof(self):
        for fixture in (CLEAN, DRIFTED):
            before = tree_hash(fixture)
            code, _ = run_main(["check", fixture, "--json"])
            after = tree_hash(fixture)
            self.assertEqual(before, after)
            self.assertNotIn("EXECUTED_MARKER", after)


class VerdictRuleTests(unittest.TestCase):
    def test_partial_enumeration_degrades_missing(self):
        root = make_repo({
            "SPEC.md": "- `--ghost` — promised.\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument(FLAG)\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "UNTRACED")
        self.assertEqual(code, 0)  # UNTRACED never fails

    def test_spec_only_repo(self):
        with open(os.path.join(DRIFTED, "SPEC.md"),
                  encoding="utf-8") as fh:
            spec = fh.read()
        root = make_repo({"SPEC.md": spec, "src/README": ""})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(code, 1)
        by_kind = {}
        for i in r["items"]:
            by_kind.setdefault(i["kind"], set()).add(
                i["verdict"])
        self.assertEqual(by_kind["flag"], {"MISSING"})
        self.assertEqual(by_kind["command"], {"MISSING"})
        self.assertEqual(by_kind["requirement"],
                         {"UNTRACED"})
        # The negation holds vacuously: absence is proven
        # because the empty class enumerates completely.
        self.assertEqual(by_kind["negation"],
                         {"IMPLEMENTED"})
        # Stage 2 (SPEC section 6, fixture 3): the new
        # structured classes behave the same on an
        # empty-but-complete enumeration.
        self.assertEqual(by_kind["endpoint"], {"MISSING"})
        self.assertEqual(by_kind["config"], {"MISSING"})
        self.assertEqual(by_kind["env"], {"MISSING"})
        self.assertEqual(by_kind["env-negation"],
                         {"IMPLEMENTED"})

    def test_default_mismatch_contradicted(self):
        root = make_repo({
            "SPEC.md": "- `--mode` (default: `fast`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default='slow')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "CONTRADICTED")
        self.assertEqual(code, 1)

    def test_choices_mismatch_contradicted(self):
        root = make_repo({
            "SPEC.md": "- `--level` one of `low|high`\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--level', choices=['low', 'medium', 'high'])\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "CONTRADICTED")

    def test_traced_by_terms_without_verbatim_id(self):
        root = make_repo({
            "SPEC.md": "- FR-009 — Zebra quasar lantern "
                       "beacon assembles quietly.\n",
            "tests/test_beacon.py":
                "def test_zebra_quasar_lantern():\n"
                "    assert True\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"], "TRACED")

    def test_fail_on_undocumented_promotes(self):
        root = make_repo({
            "SPEC.md": "- `--mode` (default: `fast`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default='fast')\n"
                      "p.add_argument('--surprise')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, _ = run_main(["check", root, "--json"])
        self.assertEqual(code, 0)
        code, _ = run_main(
            ["check", root, "--json",
             "--fail-on", "undocumented"])
        self.assertEqual(code, 1)

    def test_two_flags_one_line_no_cross_contamination(self):
        # Regression (audit round 1): parameters are
        # scoped to each flag's own segment of the line.
        items = sr.parse_spec_text(
            "- `--mode` (default: `fast`) pairs with "
            "`--level` one of `low|high`\n", "SPEC.md")
        by_name = {i.name: i for i in items
                   if i.kind == "flag"}
        self.assertEqual(by_name["mode"].default, "fast")
        self.assertIsNone(by_name["mode"].choices)
        self.assertIsNone(by_name["level"].default)
        self.assertEqual(by_name["level"].choices,
                         ["low", "high"])

    def test_nonconstant_choices_never_contradicted(self):
        # Regression (audit round 3): a choices
        # expression that exists but is not statically
        # resolvable must not be read as "no
        # restriction".
        root = make_repo({
            "SPEC.md": "- `--level` one of `low|high`\n",
            "app.py": "import argparse\n"
                      "LEVELS = ['low', 'high']\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--level', choices=LEVELS)\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "IMPLEMENTED")
        self.assertIn("not judged",
                      r["items"][0]["basis"])

    def test_nonconstant_default_never_contradicted(self):
        root = make_repo({
            "SPEC.md": "- `--mode` (default: `fast`)\n",
            "app.py": "import argparse\n"
                      "MODE = 'fast'\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default=MODE)\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "IMPLEMENTED")

    def test_store_true_implicit_default_matches(self):
        root = make_repo({
            "SPEC.md": "- `--verbose` (default: `false`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--verbose', action='store_true')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["items"][0]["verdict"],
                         "IMPLEMENTED")

    def test_fail_on_untraced_promotes(self):
        root = make_repo({
            "SPEC.md": "- FR-777 — Zebra quasar lantern "
                       "beacon assembles quietly.\n",
            "app.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, _ = run_main(["check", root, "--json"])
        self.assertEqual(code, 0)
        code, _ = run_main(
            ["check", root, "--json",
             "--fail-on", "untraced"])
        self.assertEqual(code, 1)

    def test_empty_spec_file_zero_items(self):
        root = make_repo({"SPEC.md": "# Notes\n\nProse.\n",
                          "app.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(r["summary"]["items"], 0)

    def test_fail_on_bad_value_is_input_error(self):
        root = make_repo({"SPEC.md": "- `--mode`\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(
            ["check", root, "--json", "--fail-on", "bogus"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))


class DesignationAndErrorTests(unittest.TestCase):
    def test_no_spec_is_exit_2_json(self):
        root = make_repo({"app.py": "x = 1\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        self.assertEqual(code, 2)
        obj = json.loads(out)  # exactly one JSON object
        self.assertIn("error", obj)

    def test_missing_repo_is_exit_2(self):
        code, out = run_main(
            ["check", "/nonexistent-xyz", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(out))

    def test_discovery_docs_spec(self):
        root = make_repo({
            "docs/spec.md": "- `--mode` (default: `fast`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default='fast')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(r["spec_files"],
                         [os.path.join("docs", "spec.md")])

    def test_manifest_designation(self):
        root = make_repo({
            ".spec-receipt.toml":
                'specs = ["docs/plan.md"]\n',
            "docs/plan.md": "- `--mode` (default: `fast`)\n",
            "SPEC.md": "- `--other` (default: `x`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default='fast')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        _, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["spec_files"],
                         [os.path.join("docs", "plan.md")])
        self.assertEqual(r["summary"]["implemented"], 1)

    def test_explicit_spec_flag(self):
        root = make_repo({
            "custom.md": "- `--mode` (default: `fast`)\n",
            "app.py": "import argparse\n"
                      "p = argparse.ArgumentParser()\n"
                      "p.add_argument('--mode', default='fast')\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, _ = run_main(
            ["check", root, "--spec", "custom.md"])
        self.assertEqual(code, 0)

    def test_json_contract_summary_recounts(self):
        _, out = run_main(["check", DRIFTED, "--json"])
        r = json.loads(out)
        sm = r["summary"]
        self.assertEqual(sm["items"], len(r["items"]))
        self.assertEqual(
            sm["implemented"] + sm["missing"]
            + sm["contradicted"] + sm["traced"]
            + sm["untraced"], sm["items"])
        self.assertEqual(sm["surfaces"], len(r["surfaces"]))
        self.assertEqual(
            sm["documented"] + sm["undocumented"],
            sm["surfaces"])

    def test_argparse_error_in_json_mode(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                sr.main(["check", "--json"])
        self.assertEqual(cm.exception.code, 2)
        obj = json.loads(buf.getvalue())
        self.assertIn("error", obj)

    def test_imports_are_stdlib_only(self):
        import ast as _ast
        with open(sr.__file__, encoding="utf-8") as fh:
            tree = _ast.parse(fh.read())
        mods = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                mods.update(a.name.split(".")[0]
                            for a in node.names)
            elif isinstance(node, _ast.ImportFrom):
                if node.module:
                    mods.add(node.module.split(".")[0])
        self.assertEqual(
            mods, {"argparse", "ast", "json", "os", "re",
                   "sys"})


class Stage2SpecParsingTests(unittest.TestCase):
    def items(self, text):
        return sr.parse_spec_text(text, "SPEC.md")

    def by_kind(self, text, kind):
        return [i for i in self.items(text)
                if i.kind == kind]

    def test_backticked_endpoint_anywhere(self):
        eps = self.by_kind(
            "The API exposes `GET /items` and "
            "`POST /items`.\n", "endpoint")
        self.assertEqual(
            sorted((i.method, i.path) for i in eps),
            [("GET", "/items"), ("POST", "/items")])

    def test_endpoint_table_and_bare_forms(self):
        text = ("## Endpoints\n\n"
                "| Method | Path |\n"
                "| --- | --- |\n"
                "| GET | /a |\n"
                "| POST | /b |\n\n"
                "- DELETE /c\n")
        eps = self.by_kind(text, "endpoint")
        self.assertEqual(
            sorted((i.method, i.path) for i in eps),
            [("DELETE", "/c"), ("GET", "/a"),
             ("POST", "/b")])

    def test_endpoint_only_form(self):
        only = self.by_kind(
            "The service allows only `GET` on `/items`.\n",
            "endpoint-only")
        self.assertEqual(len(only), 1)
        self.assertEqual((only[0].method, only[0].path),
                         ("GET", "/items"))
        # The exclusivity line is not also a plain
        # endpoint item.
        self.assertEqual(
            self.by_kind("The service allows only `GET` "
                         "on `/items`.\n", "endpoint"), [])

    def test_config_keys_section_scoped(self):
        text = ("## Configuration\n\n"
                "- `server.host` — bind address.\n\n"
                "## Usage\n\n"
                "- `other.key` — not a config claim.\n")
        keys = self.by_kind(text, "config")
        self.assertEqual([i.name for i in keys],
                         ["server.host"])

    def test_config_keys_from_fenced_toml(self):
        text = ("## Configuration\n\n"
                "```toml\n"
                "[server]\n"
                "host = \"x\"\n"
                "```\n")
        keys = self.by_kind(text, "config")
        self.assertEqual([i.name for i in keys],
                         ["server", "server.host"])

    def test_env_vars_and_negation(self):
        text = ("- `APP_TOKEN` — the token.\n"
                "- The tool never reads `AWS_*`.\n")
        env = self.by_kind(text, "env")
        neg = self.by_kind(text, "env-negation")
        self.assertEqual([i.name for i in env],
                         ["APP_TOKEN"])
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0].name, "AWS_*")
        self.assertTrue(neg[0].wildcard)

    def test_env_negation_exact_name(self):
        neg = self.by_kind(
            "- The tool does not read "
            "`LEGACY_SECRET`.\n", "env-negation")
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg[0].name, "LEGACY_SECRET")
        self.assertFalse(neg[0].wildcard)

    def test_http_method_token_is_not_env(self):
        # A lone backticked method name must not be
        # claimed as an environment variable.
        self.assertEqual(
            self.by_kind("Use `GET` for reads.\n", "env"),
            [])


class Stage2IndexTests(unittest.TestCase):
    def index_files(self, files):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root, True)
        return sr.index_code(root)

    def test_flask_routes_and_default_method(self):
        idx = self.index_files({"app.py":
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            "@app.route('/a')\n"
            "def a():\n    pass\n\n"
            "@app.route('/b', methods=['GET', 'POST'])\n"
            "def b():\n    pass\n"})
        got = sorted((s.method, s.path) for s in idx.routes)
        self.assertEqual(got, [("GET", "/a"),
                               ("GET", "/b"),
                               ("POST", "/b")])
        self.assertTrue(idx.routes_complete)

    def test_fastapi_routes_and_router_prefix(self):
        idx = self.index_files({"app.py":
            "from fastapi import FastAPI, APIRouter\n"
            "app = FastAPI()\n"
            "router = APIRouter()\n"
            "app.include_router(router, prefix='/api')\n\n"
            "@app.get('/health')\n"
            "def h():\n    pass\n\n"
            "@router.post('/things')\n"
            "def t():\n    pass\n"})
        got = sorted((s.method, s.path) for s in idx.routes)
        self.assertEqual(got, [("GET", "/health"),
                               ("POST", "/api/things")])
        self.assertTrue(idx.routes_complete)

    def test_django_routes_are_any_method(self):
        idx = self.index_files({"urls.py":
            "from django.urls import path, re_path\n"
            "urlpatterns = [\n"
            "    path('items/<int:item_id>/', v),\n"
            "    re_path(r'^legacy/(?P<slug>[\\w-]+)/$', w),\n"
            "]\n"})
        got = sorted((s.method, s.path) for s in idx.routes)
        self.assertEqual(got, [("ANY", "/items/:item_id"),
                               ("ANY", "/legacy/:slug")])
        self.assertTrue(idx.routes_complete)

    def test_blueprint_prefixes(self):
        idx = self.index_files({"app.py":
            "from flask import Flask, Blueprint\n"
            "app = Flask(__name__)\n"
            "bp = Blueprint('api', __name__, "
            "url_prefix='/api')\n"
            "app.register_blueprint(bp)\n\n"
            "@bp.route('/things')\n"
            "def t():\n    pass\n\n"
            "bp2 = Blueprint('v2', __name__)\n"
            "app.register_blueprint(bp2, "
            "url_prefix='/v2')\n\n"
            "@bp2.get('/x')\n"
            "def x():\n    pass\n"})
        got = sorted((s.method, s.path) for s in idx.routes)
        self.assertEqual(got, [("GET", "/api/things"),
                               ("GET", "/v2/x")])
        self.assertTrue(idx.routes_complete)

    def test_apirouter_constructor_prefix(self):
        idx = self.index_files({"app.py":
            "from fastapi import FastAPI, APIRouter\n"
            "app = FastAPI()\n"
            "router = APIRouter(prefix='/api')\n"
            "app.include_router(router, prefix='/v1')\n\n"
            "@router.get('/things')\n"
            "def t():\n    pass\n"})
        got = [(s.method, s.path) for s in idx.routes]
        self.assertEqual(got, [("GET", "/api/v1/things")])

    def test_nonconstant_prefix_marks_partial(self):
        idx = self.index_files({"app.py":
            "from flask import Blueprint\n"
            "bp = Blueprint('a', __name__, "
            "url_prefix=PRE)\n\n"
            "@bp.route('/t')\n"
            "def t():\n    pass\n"})
        self.assertFalse(idx.routes_complete)
        self.assertEqual(idx.routes, [])

    def test_dynamic_route_path_marks_partial(self):
        idx = self.index_files({"app.py":
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "PATH = '/x'\n\n"
            "@app.route(PATH)\n"
            "def x():\n    pass\n"})
        self.assertFalse(idx.routes_complete)
        self.assertEqual(idx.routes, [])

    def test_add_url_rule_marks_partial(self):
        idx = self.index_files({"app.py":
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "app.add_url_rule('/x', view_func=f)\n"})
        self.assertFalse(idx.routes_complete)
        self.assertEqual(idx.routes, [])

    def test_env_reads_all_spellings(self):
        idx = self.index_files({"app.py":
            "import os\n"
            "from os import environ, getenv\n"
            "a = os.environ['A']\n"
            "b = os.getenv('B')\n"
            "c = os.environ.get('C')\n"
            "d = environ['D']\n"
            "e = getenv('E')\n"})
        self.assertEqual(
            sorted(s.name for s in idx.env_vars),
            ["A", "B", "C", "D", "E"])
        self.assertTrue(idx.env_complete)

    def test_dynamic_env_name_marks_partial(self):
        idx = self.index_files({"app.py":
            "import os\n"
            "x = os.environ[NAME]\n"})
        self.assertFalse(idx.env_complete)
        self.assertEqual(idx.env_vars, [])

    def test_config_reads_and_example_file(self):
        idx = self.index_files({
            "app.py":
                "def f(config):\n"
                "    a = config['server']['host']\n"
                "    b = config.get('server.port')\n"
                "    c = config.get('db.url')\n"
                "    return a, b, c\n",
            "config.example.toml":
                "[server]\nhost = \"x\"\nport = 1\n"})
        self.assertEqual(
            sorted({s.name for s in idx.config_keys}),
            ["db.url", "server", "server.host",
             "server.port"])
        self.assertTrue(idx.config_complete)

    def test_dynamic_config_key_marks_partial(self):
        idx = self.index_files({"app.py":
            "def f(config):\n"
            "    return config[KEY]\n"})
        self.assertFalse(idx.config_complete)

    def test_yaml_config_marks_partial(self):
        idx = self.index_files({
            "config.example.yaml": "server:\n  host: x\n"})
        self.assertFalse(idx.config_complete)
        self.assertEqual(idx.config_keys, [])

    def test_pyproject_is_not_a_config_surface(self):
        idx = self.index_files({
            "pyproject.toml":
                "[project]\nname = \"x\"\n"})
        self.assertEqual(idx.config_keys, [])
        self.assertTrue(idx.config_complete)


class Stage2VerdictTests(unittest.TestCase):
    def check(self, files):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        return code, json.loads(out)

    def verdicts(self, r, kind):
        return {i["name"]: i["verdict"] for i in r["items"]
                if i["kind"] == kind}

    def test_endpoint_method_mismatch_contradicted(self):
        code, r = self.check({
            "SPEC.md": "- `POST /items`\n",
            "app.py":
                "from flask import Flask\n"
                "app = Flask(__name__)\n\n"
                "@app.route('/items', methods=['GET'])\n"
                "def x():\n    pass\n"})
        self.assertEqual(code, 1)
        self.assertEqual(
            self.verdicts(r, "endpoint"),
            {"POST /items": "CONTRADICTED"})

    def test_endpoint_missing_and_partial(self):
        spec = "- `GET /gone`\n"
        code, r = self.check({"SPEC.md": spec,
                              "app.py": "x = 1\n"})
        self.assertEqual(
            self.verdicts(r, "endpoint"),
            {"GET /gone": "MISSING"})
        self.assertEqual(code, 1)
        code, r = self.check({
            "SPEC.md": spec,
            "app.py":
                "from flask import Flask\n"
                "app = Flask(__name__)\n"
                "app.add_url_rule('/x', view_func=f)\n"})
        # Partial enumeration: absence unprovable.
        self.assertEqual(
            self.verdicts(r, "endpoint"),
            {"GET /gone": "UNTRACED"})
        self.assertEqual(code, 0)

    def test_django_any_satisfies_but_never_contradicts(self):
        code, r = self.check({
            "SPEC.md": "- `DELETE /items`\n",
            "urls.py":
                "from django.urls import path\n"
                "urlpatterns = [path('items/', v)]\n"})
        self.assertEqual(
            self.verdicts(r, "endpoint"),
            {"DELETE /items": "IMPLEMENTED"})

    def test_endpoint_only_contradicted_and_holds(self):
        spec = "Only `GET` on `/items`.\n"
        flask = ("from flask import Flask\n"
                 "app = Flask(__name__)\n\n")
        code, r = self.check({
            "SPEC.md": spec,
            "app.py": flask +
                "@app.route('/items', methods=['GET', 'POST'])\n"
                "def x():\n    pass\n"})
        self.assertEqual(
            self.verdicts(r, "endpoint-only"),
            {"GET /items": "CONTRADICTED"})
        self.assertEqual(code, 1)
        code, r = self.check({
            "SPEC.md": spec,
            "app.py": flask +
                "@app.route('/items')\n"
                "def x():\n    pass\n"})
        self.assertEqual(
            self.verdicts(r, "endpoint-only"),
            {"GET /items": "IMPLEMENTED"})
        self.assertEqual(code, 0)

    def test_config_and_env_verdicts(self):
        spec = ("## Configuration\n\n"
                "- `server.host`\n"
                "- `missing.key`\n\n"
                "## Environment variables\n\n"
                "- `PRESENT_VAR`\n"
                "- `ABSENT_VAR`\n"
                "- The tool never reads `AWS_*`.\n")
        code, r = self.check({
            "SPEC.md": spec,
            "app.py":
                "import os\n"
                "def f(config):\n"
                "    a = os.environ['PRESENT_VAR']\n"
                "    b = os.environ['AWS_SECRET']\n"
                "    return config['server']['host']\n"})
        self.assertEqual(code, 1)
        self.assertEqual(self.verdicts(r, "config"),
                         {"server.host": "IMPLEMENTED",
                          "missing.key": "MISSING"})
        self.assertEqual(self.verdicts(r, "env"),
                         {"PRESENT_VAR": "IMPLEMENTED",
                          "ABSENT_VAR": "MISSING"})
        self.assertEqual(
            self.verdicts(r, "env-negation"),
            {"AWS_*": "CONTRADICTED"})
        # The contradicted negation still DOCUMENTS the
        # surface it names.
        env_surfaces = {s["name"]: s["verdict"]
                        for s in r["surfaces"]
                        if s["kind"] == "env"}
        self.assertEqual(env_surfaces["AWS_SECRET"],
                         "DOCUMENTED")
        self.assertEqual(env_surfaces["PRESENT_VAR"],
                         "DOCUMENTED")

    def test_undocumented_route_surface(self):
        code, r = self.check({
            "SPEC.md": "- `GET /known`\n",
            "app.py":
                "from flask import Flask\n"
                "app = Flask(__name__)\n\n"
                "@app.route('/known')\n"
                "def a():\n    pass\n\n"
                "@app.route('/secret')\n"
                "def b():\n    pass\n"})
        undoc = [s["name"] for s in r["surfaces"]
                 if s["verdict"] == "UNDOCUMENTED"]
        self.assertEqual(undoc, ["GET /secret"])
        # UNDOCUMENTED alone never fails the run.
        self.assertEqual(code, 0)


class Stage3ExtractionScopeTests(unittest.TestCase):
    """Regression tests for the dogfood gate
    (DOGFOOD-SPEC-RECEIPT.md): every false verdict
    traced to extraction scoping — illustrative
    content extracted as declarations, and fenced
    invocations never heard."""

    def items(self, text):
        return sr.parse_spec_text(text, "SPEC.md")

    def by_kind(self, text, kind):
        return [i for i in self.items(text)
                if i.kind == kind]

    def test_example_section_yields_no_items(self):
        # Dogfood target 1: the design doc's item-form
        # examples (a flag with a default, a quoted
        # negation, a quoted exclusivity) sat in a
        # "What counts as a spec item" section and
        # were extracted as declarations.
        text = ("## What counts as a spec item\n\n"
                "- CLI flag: `--name` (optionally with "
                "a stated default or choices: `--mode` "
                "(default: `fast`))\n"
                "- \"there is no `--legacy` flag\", "
                "\"only `GET` on `/items`\"\n\n"
                "## Flags\n\n"
                "- `--real` — the real flag.\n")
        self.assertEqual(
            [i.name for i in self.by_kind(text, "flag")],
            ["real"])
        self.assertEqual(self.by_kind(text, "negation"),
                         [])
        self.assertEqual(
            self.by_kind(text, "endpoint-only"), [])

    def test_eg_line_is_illustrative(self):
        # Dogfood target 1: the verdict table's worked
        # evidence line carries the same shapes behind
        # an "e.g.".
        text = ("| CONTRADICTED | Hard evidence: e.g. "
                "spec \"no `--legacy`\" vs the flag's "
                "AST node; spec `POST /x` only vs code "
                "registering `GET /x` only |\n")
        self.assertEqual(self.items(text), [])

    def test_definitional_lines_yield_no_items(self):
        # Lines that describe the notation itself.
        text = ("- Environment variable: a backticked "
                "`SCREAMING_CASE` name.\n"
                "- A flag token is written as `--name` "
                "in the spec.\n"
                "- `--real` — real.\n")
        self.assertEqual(self.by_kind(text, "env"), [])
        self.assertEqual(
            [i.name for i in self.by_kind(text, "flag")],
            ["real"])

    def test_placeholder_env_token(self):
        # Dogfood target 1: the literal placeholder
        # `SCREAMING_CASE` in a definition sentence
        # became an env item and a MISSING. A token
        # naming a capitalisation style is notation.
        text = "- `SCREAMING_CASE` names a variable.\n"
        self.assertEqual(self.by_kind(text, "env"), [])

    def test_placeholder_endpoint_paths(self):
        # Dogfood target 1: `GET /path`, `POST
        # /path/:id`, `GET /x` are template text.
        text = ("The API exposes `GET /path`, "
                "`POST /path/:id` and `GET /x` as "
                "templates, plus `GET /items`.\n")
        eps = self.by_kind(text, "endpoint")
        self.assertEqual(
            [(i.method, i.path) for i in eps],
            [("GET", "/items")])
        only = self.by_kind(
            "only `GET` on `/path`\n", "endpoint-only")
        self.assertEqual(only, [])

    def test_illustrative_items_never_contradict(self):
        # End to end: code that WOULD contradict the
        # examples cannot produce a CONTRADICTED,
        # because the examples never become items.
        root = make_repo({
            "SPEC.md":
                "## Examples\n\n"
                "- `--mode` (default: `fast`)\n"
                "- \"there is no `--legacy` flag\"\n"
                "- \"only `GET` on `/items`\"\n",
            "app.py":
                "import argparse\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--mode', "
                "default='slow')\n"
                "p.add_argument('--legacy')\n"
                "from flask import Flask\n"
                "app = Flask(__name__)\n"
                "@app.route('/items', "
                "methods=['POST'])\n"
                "def x():\n    pass\n"})
        self.addCleanup(shutil.rmtree, root, True)
        code, out = run_main(["check", root, "--json"])
        r = json.loads(out)
        self.assertEqual(r["summary"]["items"], 0)
        self.assertEqual(code, 0)

    def test_sample_output_fence_is_not_config(self):
        # Dogfood target 2a: sample output quoted in
        # a spec — [SUPPORTED] / [CONTRADICTED] lines
        # — was read as INI sections, and its words
        # became config keys.
        text = ("## Output\n\n"
                "```\n"
                "$ tool check demo\n"
                "Verdict rule: SUPPORTED = the "
                "evidence backs it.\n"
                "[SUPPORTED] capability — \"Formats "
                "tables\"\n"
                "[CONTRADICTED] offline — \"Works "
                "offline\"\n"
                "```\n")
        self.assertEqual(self.by_kind(text, "config"),
                         [])

    def test_wrapped_flag_token(self):
        # Dogfood target 1: `--fail-on` split across
        # a line break inside its backticked span was
        # never extracted, leaving the real flag
        # UNDOCUMENTED.
        text = ("- **1** if any item fails. "
                "UNDOCUMENTED and\n"
                "  UNTRACED fail only via `--fail-on\n"
                "  undocumented,untraced` (comma list) "
                "promotion.\n")
        flags = self.by_kind(text, "flag")
        self.assertEqual([i.name for i in flags],
                         ["fail-on"])
        self.assertIsNone(flags[0].default)
        self.assertIsNone(flags[0].choices)

    def test_fenced_invocation_declares_command(self):
        # Dogfood target 2: the real command lived in
        # a fenced usage line and was never heard,
        # while a prose `pipx install ...` span was
        # misheard as a command named `install`.
        text = ("## Usage\n\n"
                "```\n"
                "$ skill-claims audit examples/demo\n"
                "```\n\n"
                "```bash\n"
                "pipx install skill-claims\n"
                "```\n\n"
                "(Installed via `pipx install "
                "git+https://example.com/skill-claims`.)\n")
        cmds = self.by_kind(text, "command")
        self.assertEqual([c.name for c in cmds],
                         ["audit"])

    def test_quoted_mention_is_not_a_declaration(self):
        # Rerun finding (target 1): the design doc's
        # fixture description quotes another spec's
        # statement — a "no `--legacy`" flag added —
        # and the quotation was extracted as this
        # document's negation. Quoted text is the
        # document talking about wording.
        text = ("- a \"no `--legacy`\" flag added "
                "(→ CONTRADICTED); use \"`--fast` "
                "mode\" for speed.\n"
                "- There is no `--legacy` flag.\n")
        neg = self.by_kind(text, "negation")
        self.assertEqual([i.name for i in neg],
                         ["legacy"])
        self.assertEqual(self.by_kind(text, "flag"),
                         [])

    def test_defined_terms_are_not_env(self):
        # Dogfood target 2b: the document's own
        # verdict vocabulary (a definition table and
        # a gloss enumeration) became env items; a
        # plain declaration in the same document
        # still counts.
        text = ("| Verdict | Meaning |\n"
                "|---|---|\n"
                "| `SUPPORTED` | backed |\n"
                "| `CONTRADICTED` | broken |\n\n"
                "Overall verdicts: `CONTRADICTED` "
                "(broken → exit 1), `UNEVIDENCED` "
                "(no evidence → exit 0), `SUPPORTED` "
                "(all backed → exit 0), `NO_CLAIMS` "
                "(no claims → exit 0).\n\n"
                "- `APP_TOKEN` — the token.\n")
        self.assertEqual(
            [i.name for i in self.by_kind(text, "env")],
            ["APP_TOKEN"])


class Stage3TraceScopeTests(unittest.TestCase):
    """Scoped requirement tracing (dogfood target
    3): an ID must trace to its own feature's
    anchor, or honestly not trace at all."""

    def check(self, files, spec_args):
        root = make_repo(files)
        self.addCleanup(shutil.rmtree, root, True)
        argv = ["check", root, "--json"]
        for s in spec_args:
            argv += ["--spec", s]
        code, out = run_main(argv)
        return code, json.loads(out)

    def req_rows(self, r):
        return {(i["spec"]["file"], i["name"]):
                (i["verdict"],
                 (i["code"] or {}).get("file"))
                for i in r["items"]
                if i["kind"] == "requirement"}

    def test_cross_feature_id_collision(self):
        # Two features in one tree declare FR-001;
        # only beta carries an anchor, qualified
        # (002). Alpha must not steal it.
        code, r = self.check({
            "demo/specs/001-alpha/spec.md":
                "- FR-001 — Alpha assembles the "
                "alpha widget quietly.\n",
            "demo/specs/002-beta/spec.md":
                "- FR-001 — Beta assembles the beta "
                "widget quietly.\n",
            "demo/src/beta.py":
                "# FR-001 (002) — beta anchor\n"
                "x = 1\n",
            "demo/src/alpha.py": "y = 1\n"},
            ["demo/specs/001-alpha/spec.md",
             "demo/specs/002-beta/spec.md"])
        rows = self.req_rows(r)
        self.assertEqual(
            rows[("demo/specs/002-beta/spec.md",
                  "FR-001")],
            ("TRACED", "demo/src/beta.py"))
        self.assertEqual(
            rows[("demo/specs/001-alpha/spec.md",
                  "FR-001")][0],
            "UNTRACED")

    def test_same_id_two_trees_each_scoped(self):
        # The same ID under two trees is unambiguous
        # per tree: each spec traces to its own
        # tree's anchor.
        code, r = self.check({
            "clean/specs/001-health/spec.md":
                "- FR-001 — Health check responds.\n",
            "demo/specs/001-login/spec.md":
                "- FR-001 — Login verifies a "
                "password.\n",
            "clean/tests/test_health.py":
                "# anchors FR-001\n"
                "def test_h():\n    assert True\n",
            "demo/src/auth.py":
                "# FR-001 (001)\n"
                "def f():\n    pass\n"},
            ["clean/specs/001-health/spec.md",
             "demo/specs/001-login/spec.md"])
        rows = self.req_rows(r)
        self.assertEqual(
            rows[("clean/specs/001-health/spec.md",
                  "FR-001")],
            ("TRACED", "clean/tests/test_health.py"))
        self.assertEqual(
            rows[("demo/specs/001-login/spec.md",
                  "FR-001")],
            ("TRACED", "demo/src/auth.py"))

    def test_negating_comment_block_is_not_anchor(self):
        # Dogfood target 3: the only mention of
        # FR-003 sat in a comment block listing it as
        # having NO anchor — and was cited as its
        # anchor.
        code, r = self.check({
            "SPEC.md":
                "- FR-003 — Locks the account after "
                "failed attempts.\n",
            "tests/test_auth.py":
                "# The following spec items have NO "
                "anchor in this\n"
                "# test suite: FR-003, FR-004.\n"
                "def test_something():\n"
                "    assert True\n"},
            [])
        rows = self.req_rows(r)
        self.assertEqual(
            rows[("SPEC.md", "FR-003")][0], "UNTRACED")

    def test_terms_do_not_trace_through_denial(self):
        # Rerun finding (target 3): the denial block
        # restated the requirement's own words, and
        # the terms fallback traced the requirement
        # to the sentence denying its anchor. Refused
        # lines are blanked before terms are read.
        code, r = self.check({
            "SPEC.md":
                "- FR-003 — Locks the account after "
                "failed attempts.\n",
            "tests/test_auth.py":
                "# No anchor for the items below in "
                "this suite:\n"
                "#   FR-003, FR-004 — locks the "
                "account after failed attempts\n"
                "def test_something():\n"
                "    assert True\n"},
            [])
        rows = self.req_rows(r)
        self.assertEqual(
            rows[("SPEC.md", "FR-003")][0], "UNTRACED")

    def test_single_id_comment_is_an_anchor(self):
        # Control: an ordinary single-ID annotation
        # still traces.
        code, r = self.check({
            "SPEC.md":
                "- FR-003 — Locks the account after "
                "failed attempts.\n",
            "tests/test_auth.py":
                "# anchors FR-003\n"
                "def test_lockout():\n"
                "    assert True\n"},
            [])
        rows = self.req_rows(r)
        self.assertEqual(
            rows[("SPEC.md", "FR-003")],
            ("TRACED", "tests/test_auth.py"))


if __name__ == "__main__":
    unittest.main()
