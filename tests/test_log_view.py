import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "jboss-log-view.sh").read_text(encoding="utf-8")
PROGRAM = SCRIPT.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
TASK_A = "a" * 32
TASK_B = "Z9" * 16


class LogViewTests(unittest.TestCase):
    def setUp(self):
        # Inherit workspace ACLs; Python's mode-0700 temporary directories can
        # exclude restricted Windows execution tokens from their own fixtures.
        self.root = ROOT / (".test-logs-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)

    def log(self, text, container="front", service="intra-web", task=TASK_A,
            filename="server.log"):
        path = self.root / container / "logs" / service / "mid" / task / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_view(self, program=PROGRAM, **overrides):
        env = dict(os.environ, LOG_ROOT=str(self.root), LOG_TZ="JST",
                   START_JST="2026-08-26 00:00:00",
                   END_JST="2026-08-27 00:00:00", LOG_COLOR="never",
                   PYTHONIOENCODING="utf-8")
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-B", "-"], input=program, env=env,
            encoding="utf-8", capture_output=True, timeout=15,
        )

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_jst_sort_across_files_keeps_traces_and_range_boundaries(self):
        self.log(
            "2026-08-26 10:00:01,900 ERROR [api] failed\n"
            "java.lang.IllegalStateException: example\n"
            "\tat example.Api.run(Api.java:1)\n"
            "2026-08-26 11:00:00,000 INFO exclusive-end\n"
            "not part of an included event\n",
            container="back", service="intra-api", task=TASK_B,
        )
        self.log(
            "2026-08-26 09:59:59,999 INFO before-start\n"
            "2026-08-26 10:00:00,000 INFO inclusive-start\n"
            "2026-08-26 10:00:01,100 WARN [web] warning 日本語\n"
            "2026-08-26 10:59:59,999 INFO last-millisecond\n",
            task="current", filename="server.log.2026-08-26",
        )
        result = self.run_view(START_JST="2026-08-26 10:00:00",
                               END_JST="2026-08-26 11:00:00")
        self.assert_ok(result)
        for value in ("before-start", "exclusive-end", "not part of"):
            self.assertNotIn(value, result.stdout)
        output = result.stdout
        markers = ["inclusive-start", "warning 日本語", "[api] failed",
                   "IllegalStateException", "at example.Api", "last-millisecond"]
        self.assertEqual(sorted(map(output.index, markers)), list(map(output.index, markers)))
        for value in ("Events: 4", "ERROR: 1", "WARN: 1", TASK_B,
                      "server.log.2026-08-26:2", "intra-api", "back", "front"):
            self.assertIn(value, output)
        self.assertNotIn("\x1b", output)

    def test_utc_to_jst_crosses_date_and_ignores_filename_date(self):
        self.log(
            "2026-08-25 14:59:59,999 INFO before-jst-day\n"
            "2026-08-25 15:00:00,000 INFO day-start\n"
            "2026-08-26 14:59:59,999 INFO day-end\n"
            "2026-08-26 15:00:00,000 INFO after-jst-day\n",
            filename="server.log.2026-08-25",
        )
        result = self.run_view(LOG_TZ="UTC")
        self.assert_ok(result)
        self.assertIn("2026-08-26 00:00:00.000", result.stdout)
        self.assertIn("2026-08-26 23:59:59.999", result.stdout)
        self.assertNotIn("before-jst-day", result.stdout)
        self.assertNotIn("after-jst-day", result.stdout)

    def test_exact_file_and_directory_selection_for_all_allowed_values(self):
        for container in ("front", "back"):
            for service in ("intra-web", "intra-api", "inter-api", "sf-api"):
                self.log("2026-08-26 00:00:00,000 INFO included\n",
                         container=container, service=service)
        invalid_names = ["server.log.1", "server.log.bak", "server.log.2026-08-26.gz",
                         "server.log.2026-08-26.1", "server.log.2026-08=26",
                         "server.log.2026-02-30", "server.log.2026-8-26"]
        for filename in invalid_names:
            self.log("2026-08-26 00:00:00,000 INFO excluded\n", filename=filename)
        for invalid in ({"container": "other"}, {"service": "other"},
                        {"task": "a" * 31}, {"task": "a" * 33},
                        {"task": "-" * 32}):
            self.log("2026-08-26 00:00:00,000 INFO excluded\n", **invalid)
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 8 | Events: 8", result.stdout)
        self.assertNotIn("excluded", result.stdout)

    def test_current_hardlink_is_read_once_but_independent_copy_is_retained(self):
        original = self.log("2026-08-26 00:00:00,000 INFO same-content\n")
        current = original.parent.parent / "current"
        current.mkdir()
        os.link(original, current / original.name)
        self.log(original.read_text(), task=TASK_B)
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 2 | Events: 2", result.stdout)
        self.assertIn("Duplicate paths skipped: 1", result.stdout)
        self.assertEqual(result.stdout.count("same-content"), 2)
        self.assertNotIn("task=current", result.stdout)

    def test_current_directory_symlink_is_read_once(self):
        original = self.log("2026-08-26 00:00:00,000 INFO linked\n")
        link = original.parent.parent / "current"
        try:
            link.symlink_to(original.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Directory symlinks are unavailable here: " + str(exc))
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 1 | Events: 1", result.stdout)
        self.assertIn("Duplicate paths skipped: 1", result.stdout)

    def test_offsets_override_source_timezone_and_equal_times_keep_line_order(self):
        self.log(
            "2026-08-26T00:00:00.100Z WARN first\n"
            "2026-08-26 09:00:00,100+0900 INFO second\n"
            "2026-08-26 09:00:00,100+09:00 INFO third\n"
        )
        result = self.run_view()
        self.assert_ok(result)
        self.assertEqual(result.stdout.count("2026-08-26 09:00:00.100"), 3)
        positions = [result.stdout.index(label) for label in ("first", "second", "third")]
        self.assertEqual(positions, sorted(positions))

    def test_level_coloring_is_not_based_on_words_in_the_message(self):
        self.log(
            "2026-08-26 00:00:00,000 ERROR failed\n"
            "stack continuation\n"
            "2026-08-26 00:00:01,000 WARNING slow\n"
            "2026-08-26 00:00:02,000 INFO contains ERROR WARN words\n"
        )
        result = self.run_view(LOG_COLOR="always")
        self.assert_ok(result)
        self.assertIn("\x1b[1;31m  failed\x1b[0m", result.stdout)
        self.assertIn("\x1b[1;31m  stack continuation\x1b[0m", result.stdout)
        self.assertIn("\x1b[1;33m  slow\x1b[0m", result.stdout)
        self.assertIn("\n  contains ERROR WARN words\n", result.stdout)

    def test_invalid_configuration_fails_before_results(self):
        for change in ({"END_JST": "2026-08-26 00:00:00"},
                       {"START_JST": "2026-02-30 00:00:00"},
                       {"START_JST": "2026-08-26"},
                       {"LOG_TZ": "unknown"}, {"LOG_COLOR": "unknown"},
                       {"LOG_ROOT": str(self.root / "missing")}):
            with self.subTest(change=change):
                result = self.run_view(**change)
                self.assertEqual(result.returncode, 1)
                self.assertIn("[SCAN ERROR]", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_no_files_is_error_but_no_matching_events_is_success(self):
        self.assertEqual(self.run_view().returncode, 1)
        self.log("2026-08-25 00:00:00,000 INFO outside\n")
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Events: 0", result.stdout)
        self.assertIn("No matching log entries", result.stdout)

    def test_unsupported_format_and_invalid_timestamp_are_reported(self):
        for text in ("00:00:00,000 INFO no-date\n",
                     "2026-08-26 00:00:00,000 INFO okay\n2026-08-26 00:00 bad\n",
                     "2026-02-30 00:00:00,000 INFO invalid-date\n"):
            with self.subTest(text=text):
                self.log(text)
                result = self.run_view()
                self.assertEqual(result.returncode, 1)
                self.assertIn("[SCAN ERROR]", result.stderr)
                self.assertIn("server.log", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_leading_orphan_is_warned_and_control_characters_are_escaped(self):
        self.log("orphan stack line\n2026-08-26 00:00:00,000 INFO safe\x1b[2J\n")
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("skipped 1 leading nonempty line", result.stderr)
        self.assertNotIn("orphan stack line", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("safe\\x1b[2J", result.stdout)

    def test_python39_syntax_and_lf_only_shell(self):
        ast.parse(PROGRAM, feature_version=(3, 9))
        self.assertNotIn(b"\r", (ROOT / "jboss-log-view.sh").read_bytes())
        self.assertIsNone(re.search(r"\\[ \t]+$", SCRIPT, re.MULTILINE))

    def test_closed_pager_exits_without_a_false_scan_error(self):
        self.log("2026-08-26 00:00:00,000 INFO message\n")
        # Windows CRT reports EINVAL for a closed pipe. Inject the POSIX EPIPE
        # contract to test the RHEL early-exit path without masking file errors.
        closed_pipe = (
            "import sys\n"
            "class ClosedPipe:\n"
            "    def isatty(self): return False\n"
            "    def fileno(self): return sys.__stdout__.fileno()\n"
            "    def flush(self): pass\n"
            "    def write(self, text): raise BrokenPipeError(32, 'Reader closed')\n"
            "sys.stdout = ClosedPipe()\n"
        )
        result = self.run_view(program=closed_pipe + PROGRAM)
        self.assert_ok(result)
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
