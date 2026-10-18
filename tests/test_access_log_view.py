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
SCRIPT_PATH = ROOT / "RHEL9.8" / "jboss-access-log-view.sh"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
PROGRAM = SCRIPT.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
TASK_A = "a" * 32
TASK_B = "Z9" * 16


def entry(stamp="20/Aug/2026:09:00:00 +0900", status=200,
          request="GET /example HTTP/1.1", extra="", size="123"):
    suffix = " " + extra if extra else ""
    return f'192.0.2.10 - - [{stamp}] "{request}" {status} {size}{suffix}\n'


class AccessLogViewTests(unittest.TestCase):
    def setUp(self):
        # Inherit workspace ACLs, as in the existing server-log tests.
        self.root = ROOT / (".test-logs-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)

    def log(self, text, container="front", service="intra-web", task=TASK_A,
            filename="access_log.log"):
        path = self.root / container / "logs" / service / "mid" / task / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_view(self, program=PROGRAM, **overrides):
        env = dict(os.environ, LOG_ROOT=str(self.root),
                   START_JST="2026-08-20 00:00:00",
                   END_JST="2026-08-21 00:00:00", LOG_COLOR="never",
                   PYTHONIOENCODING="utf-8")
        env.update(overrides)
        return subprocess.run(
            [sys.executable, "-B", "-"], input=program, env=env,
            encoding="utf-8", capture_output=True, timeout=15,
        )

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_jst_sort_across_files_and_range_boundaries(self):
        self.log(
            entry("20/Aug/2026:10:00:01.900 +0900", 503, "POST /failed HTTP/1.1")
            + entry("20/Aug/2026:11:00:00 +0900", request="GET /exclusive-end HTTP/1.1"),
            container="back", service="intra-api", task=TASK_B,
        )
        self.log(
            entry("20/Aug/2026:09:59:59.999 +0900", request="GET /before-start HTTP/1.1")
            + entry("20/Aug/2026:10:00:00 +0900", request="GET /inclusive-start HTTP/1.1")
            + entry("20/Aug/2026:10:00:01.100 +0900", 404, "GET /missing HTTP/1.1")
            + entry("20/Aug/2026:10:59:59.999 +0900", request="GET /last HTTP/1.1"),
            task="current", filename="access_log.2026-08-20.log",
        )
        result = self.run_view(START_JST="2026-08-20 10:00:00",
                               END_JST="2026-08-20 11:00:00")
        self.assert_ok(result)
        for value in ("before-start", "exclusive-end"):
            self.assertNotIn(value, result.stdout)
        positions = [result.stdout.index(marker)
                     for marker in ("/inclusive-start", "/missing", "/failed", "/last")]
        self.assertEqual(positions, sorted(positions))
        for value in ("Requests: 4", "ERROR (5xx): 1", "WARN (4xx): 1", TASK_B,
                      "access_log.2026-08-20.log:2", "intra-api", "back", "front"):
            self.assertIn(value, result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_offsets_cross_dates_ignore_filename_date_and_host_timezone(self):
        self.log(
            entry("19/Aug/2026:14:59:59.999 +0000", request="GET /before HTTP/1.1")
            + entry("19/Aug/2026:15:00:00 +0000", request="GET /day-start HTTP/1.1")
            + entry("20/Aug/2026:09:59:59.999 -0500", request="GET /day-end HTTP/1.1")
            + entry("20/Aug/2026:15:00:00 +0000", request="GET /after HTTP/1.1"),
            filename="access_log.2026-08-19.log",
        )
        result = self.run_view(TZ="Pacific/Honolulu", LC_ALL="C")
        self.assert_ok(result)
        for value in ("2026-08-20 00:00:00.000", "2026-08-20 23:59:59.999"):
            self.assertIn(value, result.stdout)
        self.assertNotIn("/before", result.stdout)
        self.assertNotIn("/after", result.stdout)

    def test_equal_instants_keep_line_order_and_millisecond_range(self):
        self.log(
            entry("20/Aug/2026:00:00:00.100 +0000", request="GET /first HTTP/1.1")
            + entry("20/Aug/2026:09:00:00,100 +09:00", request="GET /second HTTP/1.1")
            + entry("19/Aug/2026:19:00:00.100 -0500", request="GET /third HTTP/1.1")
            + entry("20/Aug/2026:09:00:00.101 +0900", request="GET /excluded HTTP/1.1")
        )
        result = self.run_view(START_JST="2026-08-20 09:00:00.100",
                               END_JST="2026-08-20 09:00:00,101")
        self.assert_ok(result)
        positions = [result.stdout.index(label) for label in ("/first", "/second", "/third")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(result.stdout.count("2026-08-20 09:00:00.100  200"), 3)
        self.assertNotIn("/excluded", result.stdout)

    def test_exact_file_and_directory_selection(self):
        for container in ("front", "back"):
            for service in ("intra-web", "intra-api", "inter-api", "sf-api"):
                self.log(entry(), container=container, service=service)
        self.log(entry(), task="current", filename="access_log.2026-08-20.log")
        invalid_names = [
            "access_log.log.2026-08-20", "access_log.2026-08-20.log.gz",
            "access_log.2026-08-20.log.bak", "access_log.2026-08-20.1.log",
            "access_log.log.1", "access_log.2026-02-30.log",
            "access_log.2026-8-20.log", "access_log.2026-08=20.log", "server.log",
        ]
        for filename in invalid_names:
            self.log("excluded invalid content\n", filename=filename)
        for invalid in ({"container": "other"}, {"service": "other"},
                        {"task": "a" * 31}, {"task": "a" * 33},
                        {"task": "-" * 32}, {"task": "current-old"}):
            self.log("excluded invalid content\n", **invalid)
        # Matching names that are directories must not be opened as files.
        directory = self.root / "back/logs/sf-api/mid" / TASK_A / "access_log.2026-08-19.log"
        directory.mkdir()
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 9 | Requests: 9", result.stdout)
        self.assertIn("task=current", result.stdout)
        self.assertNotIn("excluded", result.stdout)

    def test_current_hardlink_deduplicated_independent_copy_retained(self):
        original = self.log(entry())
        current = original.parent.parent / "current"
        current.mkdir()
        os.link(original, current / original.name)
        self.log(entry(), task=TASK_B)
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 2 | Requests: 2", result.stdout)
        self.assertIn("Duplicate paths skipped: 1", result.stdout)
        self.assertNotIn("task=current", result.stdout)
        self.assertIn(TASK_B, result.stdout)

    def test_current_symlink_deduplicates_active_and_rotated_files(self):
        original = self.log(entry())
        self.log(entry(), filename="access_log.2026-08-20.log")
        link = original.parent.parent / "current"
        try:
            link.symlink_to(original.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest("Directory symlinks are unavailable here: " + str(exc))
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 2 | Requests: 2", result.stdout)
        self.assertIn("Duplicate paths skipped: 2", result.stdout)

    def test_same_inode_in_different_services_is_not_deduplicated(self):
        original = self.log(entry())
        other = self.log("", service="sf-api")
        other.unlink()
        os.link(original, other)
        result = self.run_view()
        self.assert_ok(result)
        self.assertIn("Files: 2 | Requests: 2", result.stdout)
        self.assertIn("sf-api", result.stdout)

    def test_status_colors_and_no_classification_by_request_or_extra(self):
        self.log(
            entry(status=500, request="POST /failed HTTP/1.1")
            + entry(status=499, request="GET /missing HTTP/1.1")
            + entry(status=302, request="GET /ERROR-WARN-500 HTTP/1.1", extra='"500 ERROR WARN"')
            + entry(status=100, request="GET /continue HTTP/1.1")
            + entry(status=599, request="GET /last-error HTTP/1.1")
        )
        result = self.run_view(LOG_COLOR="always")
        self.assert_ok(result)
        self.assertIn("\x1b[1;31m  POST /failed HTTP/1.1\x1b[0m", result.stdout)
        self.assertIn("\x1b[1;33m  GET /missing HTTP/1.1\x1b[0m", result.stdout)
        self.assertIn("\n  GET /ERROR-WARN-500 HTTP/1.1\n", result.stdout)
        self.assertIn("ERROR (5xx): 2", result.stdout)
        self.assertIn("WARN (4xx): 1", result.stdout)
        auto_result = self.run_view(LOG_COLOR="auto")
        self.assert_ok(auto_result)
        self.assertNotIn("\x1b", auto_result.stdout)

    def test_combined_extra_long_request_identity_and_escaped_quotes_preserved(self):
        request = 'GET /日本語/' + "a" * 300 + r'\"quoted HTTP/1.1'
        extra = '"https://example.invalid/" "ExampleAgent" duration=123'
        text = entry(request=request, extra=extra, size="-").replace(" - - [", " ident user [")
        self.log(text)
        result = self.run_view()
        self.assert_ok(result)
        for value in (request, extra, "client=192.0.2.10", "bytes=-", "user=user", "ident=ident"):
            self.assertIn(value, result.stdout)

    def test_invalid_configuration_fails_before_results(self):
        self.log(entry())
        for change in ({"END_JST": "2026-08-20 00:00:00"},
                       {"START_JST": "2026-08-22 00:00:00"},
                       {"START_JST": "2026-02-30 00:00:00"},
                       {"START_JST": "2026-08-20"},
                       {"END_JST": "2026-08-21 00:00:00+0900"},
                       {"LOG_COLOR": "unknown"},
                       {"LOG_ROOT": str(self.root / "missing")}):
            with self.subTest(change=change):
                result = self.run_view(**change)
                self.assertEqual(result.returncode, 1)
                self.assertIn("[SCAN ERROR]", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_no_files_is_error_but_empty_or_outside_range_is_success(self):
        result = self.run_view(LOG_COLOR="always")
        self.assertEqual(result.returncode, 1)
        self.assertIn("\x1b[1;31m[SCAN ERROR]", result.stderr)
        for text in ("\n\n", entry("19/Aug/2026:09:00:00 +0900")):
            self.log(text)
            result = self.run_view()
            self.assert_ok(result)
            self.assertIn("Requests: 0", result.stdout)
            self.assertIn("No matching access log entries", result.stdout)

    def test_malformed_rows_and_invalid_timestamps_report_file_and_line(self):
        for text in ("unsupported-format\n", entry("30/Feb/2026:09:00:00 +0900"),
                     entry("20/Foo/2026:09:00:00 +0900"),
                     entry("20/Aug/2026:09:00:00 +2460"),
                     entry("20/Aug/2026:09:00:00 +0960"),
                     entry("31/Dec/9999:23:59:59 +0000"),
                     entry("01/Jan/0001:00:00:00 +2359"),
                     entry("20/Aug/2026:09:00:00"), entry(status=999),
                     entry(status="-"), entry(size="invalid")):
            with self.subTest(text=text):
                self.log(entry() + text)
                result = self.run_view()
                self.assertEqual(result.returncode, 1)
                self.assertIn("access_log.log:2", result.stderr)
                self.assertIn("[SCAN ERROR]", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_read_error_is_reported_without_partial_output(self):
        self.log(entry())
        denied_open = (
            "from pathlib import Path\n"
            "def denied_open(path, *args, **kwargs):\n"
            "    raise PermissionError(13, 'Permission denied', str(path))\n"
            "Path.open = denied_open\n"
        )
        result = self.run_view(program=denied_open + PROGRAM)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[SCAN ERROR]", result.stderr)
        self.assertIn("access_log.log", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_control_characters_and_invalid_utf8_are_escaped(self):
        path = self.log(entry(request="GET /safe\x1b[2J HTTP/1.1", extra='"agent\x07"'))
        path.write_bytes(path.read_bytes().replace(b"agent", b"agent\xff"))
        result = self.run_view()
        self.assert_ok(result)
        for value in ("safe\\x1b[2J", "agent\\xff\\x07"):
            self.assertIn(value, result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)

    def test_all_english_months_are_parsed_without_locale(self):
        months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        self.log("".join(entry(f"01/{month}/2026:09:00:00 +0900") for month in months))
        result = self.run_view(START_JST="2026-01-01 00:00:00",
                               END_JST="2027-01-01 00:00:00", LC_ALL="C")
        self.assert_ok(result)
        self.assertIn("Requests: 12", result.stdout)
        for number in range(1, 13):
            self.assertIn(f"2026-{number:02}-01 09:00:00.000", result.stdout)

    def test_python39_syntax_and_lf_only_pasteable_shell(self):
        ast.parse(PROGRAM, feature_version=(3, 9))
        self.assertNotIn(b"\r", SCRIPT_PATH.read_bytes())
        self.assertFalse(SCRIPT_PATH.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertIsNone(re.search(r"\\[ \t]+$", SCRIPT, re.MULTILINE))
        wrapper = SCRIPT.split("python3 - <<'PY'\n", 1)[0]
        assignments = [line for line in wrapper.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(assignments), 4)
        for line in assignments:
            self.assertTrue(line.endswith(" \\"), line)
        self.assertTrue(SCRIPT.endswith("\nPY\n"))

    def test_closed_pager_exits_without_a_false_scan_error(self):
        self.log(entry())
        # Inject POSIX EPIPE: a closed pipe reports EINVAL in the Windows CRT.
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
