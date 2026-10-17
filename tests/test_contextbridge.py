"""Stdlib unittest suite for ContextBridge."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextbridge import core
from contextbridge.cli import main as cb_main


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


class TempProject(unittest.TestCase):
    """Each test gets a fresh project dir with `cb init` already run."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cb-test-")
        self.old = os.getcwd()
        os.chdir(self.tmp)
        core.init_project(self.tmp, project="demo-proj")

    def tearDown(self):
        os.chdir(self.old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, summary="did things", **kw):
        kw.setdefault("auto_detect", False)
        return core.save_handoff(self.tmp, summary, **kw)


class TestInit(TempProject):
    def test_layout_created(self):
        self.assertTrue(os.path.isdir(".contextbridge/handoffs"))
        self.assertTrue(os.path.isfile(".contextbridge/config.yaml"))

    def test_config_project_name(self):
        cfg = core.read_config(self.tmp)
        self.assertEqual(cfg["project"], "demo-proj")

    def test_find_root_from_subdir(self):
        os.makedirs("a/b")
        self.assertEqual(core.find_root(os.path.join(self.tmp, "a", "b")), self.tmp)


class TestSaveRoundTrip(TempProject):
    def test_save_and_read_back(self):
        h = self.save(
            "wired up auth middleware",
            decisions=["use JWT, not sessions"],
            tasks=["add refresh-token rotation", "write login tests"],
            constraints=["no new dependencies"],
            files=[core.ReferencedFile(path="app/auth.py", note="middleware")],
            session_notes="pairing with copilot",
            timestamp="2026-10-16T10:00:00-07:00",
        )
        self.assertTrue(h.id.startswith("20261016-100000-"))
        reread = core.read_handoff(f".contextbridge/handoffs/{h.id}.md")
        self.assertEqual(reread.summary, "wired up auth middleware")
        self.assertEqual(reread.decisions, ["use JWT, not sessions"])
        self.assertEqual([t.text for t in reread.open_tasks],
                         ["add refresh-token rotation", "write login tests"])
        self.assertTrue(all(t.is_open() for t in reread.open_tasks))
        self.assertEqual(reread.constraints, ["no new dependencies"])
        self.assertEqual(reread.referenced_files[0].path, "app/auth.py")
        self.assertFalse(reread.referenced_files[0].auto_detected)
        self.assertEqual(reread.session_notes, "pairing with copilot")

    def test_latest_pointer_updated(self):
        h1 = self.save("first")
        h2 = self.save("second")
        latest = core.read_handoff(".contextbridge/latest.md")
        self.assertEqual(latest.id, h2.id)
        self.assertNotEqual(h1.id, h2.id)

    def test_multiline_and_quotes_survive(self):
        h = self.save('said "hello"\nmultiline summary',
                      decisions=['quote "x" and \\ backslash'],
                      timestamp="2026-10-16T10:00:00-07:00")
        reread = core.read_handoff(f".contextbridge/handoffs/{h.id}.md")
        self.assertEqual(reread.summary, 'said "hello"\nmultiline summary')
        self.assertEqual(reread.decisions, ['quote "x" and \\ backslash'])

    def test_empty_lists_round_trip(self):
        h = self.save("bare", timestamp="2026-10-16T10:00:00-07:00")
        reread = core.read_handoff(f".contextbridge/handoffs/{h.id}.md")
        self.assertEqual(reread.decisions, [])
        self.assertEqual(reread.open_tasks, [])
        self.assertEqual(reread.constraints, [])
        self.assertEqual(reread.referenced_files, [])


class TestGitAutoDetect(TempProject):
    def test_modified_files_auto_detected(self):
        _git("init", cwd=self.tmp)
        _git("config", "user.email", "t@t.t", cwd=self.tmp)
        _git("config", "user.name", "t", cwd=self.tmp)
        with open("main.py", "w") as fh:
            fh.write("x = 1\n")
        _git("add", "main.py", cwd=self.tmp)
        _git("commit", "-m", "init", cwd=self.tmp)
        with open("main.py", "a") as fh:
            fh.write("y = 2\n")
        with open("new.py", "w") as fh:
            fh.write("z = 3\n")
        h = core.save_handoff(self.tmp, "edited code",
                              timestamp="2026-10-16T10:00:00-07:00")
        paths = {f.path for f in h.referenced_files}
        self.assertIn("main.py", paths)
        self.assertIn("new.py", paths)
        self.assertTrue(all(f.auto_detected for f in h.referenced_files))

    def test_explicit_files_skip_auto_detect(self):
        _git("init", cwd=self.tmp)
        with open("main.py", "w") as fh:
            fh.write("x = 1\n")
        h = core.save_handoff(
            self.tmp, "edited", auto_detect=False,
            files=[core.ReferencedFile(path="README.md")],
            timestamp="2026-10-16T10:00:00-07:00")
        self.assertEqual([f.path for f in h.referenced_files], ["README.md"])
        self.assertFalse(h.referenced_files[0].auto_detected)

    def test_no_git_repo_yields_no_files(self):
        h = core.save_handoff(self.tmp, "no git here",
                              timestamp="2026-10-16T10:00:00-07:00")
        self.assertEqual(h.referenced_files, [])

    def test_contextbridge_dir_excluded_from_auto_detect(self):
        _git("init", cwd=self.tmp)
        h = core.save_handoff(self.tmp, "after init",
                              timestamp="2026-10-16T10:00:00-07:00")
        paths = [f.path for f in h.referenced_files]
        self.assertTrue(all(not p.startswith(".contextbridge") for p in paths))


class TestLoadFormats(TempProject):
    def setUp(self):
        super().setUp()
        self.h = self.save(
            "shipped parser",
            decisions=["use recursive descent"],
            tasks=["benchmark on 1MB inputs"],
            constraints=["stdlib only"],
            files=[core.ReferencedFile(path="parser.py", note="core")],
            timestamp="2026-10-16T10:00:00-07:00")

    def test_load_by_prefix(self):
        h = core.get_handoff(self.tmp, self.h.id[:12])
        self.assertEqual(h.id, self.h.id)

    def test_load_unknown_prefix_raises(self):
        with self.assertRaises(core.HandoffError):
            core.get_handoff(self.tmp, "zzzznomatch")

    def test_prompt_format_structure(self):
        out = core.render_prompt(self.h)
        for header in ("## CONTEXT HANDOFF", "### Session context",
                       "### Decisions made", "### Open tasks",
                       "### Constraints", "### Key files"):
            self.assertIn(header, out)
        self.assertIn("use recursive descent", out)
        self.assertIn("benchmark on 1MB inputs", out)
        self.assertIn("stdlib only", out)
        self.assertIn("`parser.py`", out)

    def test_short_format(self):
        out = core.render_short(self.h)
        self.assertIn("demo-proj", out)
        self.assertIn("shipped parser", out)
        self.assertIn("benchmark on 1MB inputs", out)

    def test_json_round_trip(self):
        blob = json.loads(json.dumps({
            "id": self.h.id, "decisions": self.h.decisions}))
        self.assertEqual(blob["decisions"], ["use recursive descent"])


class TestListOrdering(TempProject):
    def test_newest_first(self):
        a = self.save("alpha", timestamp="2026-10-14T09:00:00-07:00")
        b = self.save("beta", timestamp="2026-10-15T09:00:00-07:00")
        c = self.save("gamma", timestamp="2026-10-16T09:00:00-07:00")
        ids = [h.id for h in core.list_handoffs(self.tmp)]
        self.assertEqual(ids, [c.id, b.id, a.id])


class TestDoneDecide(TempProject):
    def test_done_marks_task(self):
        self.save("work", tasks=["write tests", "update docs"],
                  timestamp="2026-10-16T10:00:00-07:00")
        h = core.complete_task(self.tmp, "write tests")
        statuses = {t.text: t.status for t in h.open_tasks}
        self.assertEqual(statuses["write tests"], "done")
        self.assertEqual(statuses["update docs"], "open")
        # persisted + latest.md refreshed
        reread = core.read_handoff(".contextbridge/latest.md")
        self.assertEqual(reread.open_tasks[0].status, "done")

    def test_done_no_match_raises(self):
        self.save("work", tasks=["write tests"],
                  timestamp="2026-10-16T10:00:00-07:00")
        with self.assertRaises(core.HandoffError):
            core.complete_task(self.tmp, "nonexistent thing")

    def test_decide_appends(self):
        self.save("work", decisions=["first call"],
                  timestamp="2026-10-16T10:00:00-07:00")
        h = core.append_decision(self.tmp, "second call")
        self.assertEqual(h.decisions, ["first call", "second call"])
        reread = core.read_handoff(".contextbridge/latest.md")
        self.assertEqual(reread.decisions, ["first call", "second call"])


class TestCLIEndToEnd(TempProject):
    def test_cli_init_save_load_list_status(self):
        self.assertEqual(cb_main(["init", "--project", "cli-proj"]), 0)
        self.assertEqual(cb_main([
            "save", "--summary", "cli end to end",
            "--decision", "keep it simple",
            "--task", "ship it",
            "--constraint", "stdlib only",
        ]), 0)
        self.assertEqual(cb_main(["load", "--format", "short"]), 0)
        self.assertEqual(cb_main(["list"]), 0)
        self.assertEqual(cb_main(["status"]), 0)
        self.assertEqual(cb_main(["decide", "another call"]), 0)
        self.assertEqual(cb_main(["done", "ship"]), 0)
        h = core.get_handoff(self.tmp)
        self.assertEqual(h.decisions, ["keep it simple", "another call"])
        self.assertEqual(h.open_tasks[0].status, "done")


if __name__ == "__main__":
    unittest.main()
