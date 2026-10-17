#!/usr/bin/env bash
# Paste this entire file into a Bash session in Tera Term, or run with bash.
# Both range endpoints are JST; START is inclusive and END is exclusive.
# LOG_TZ is the timezone used INSIDE the log, not the host's timezone.
LOG_ROOT="${LOG_ROOT:-/mnt/logs}" \
START_JST="${START_JST:-2026-08-26 00:00:00}" \
END_JST="${END_JST:-2026-08-27 00:00:00}" \
LOG_TZ="${LOG_TZ:-JST}" \
LOG_COLOR="${LOG_COLOR:-auto}" \
python3 - <<'PY'
import os
import re
import stat
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9), "JST")
CONTAINERS = ("front", "back")
SERVICES = ("intra-web", "intra-api", "inter-api", "sf-api")
TASK = re.compile(r"[A-Za-z0-9]{32}\Z")
FILENAME = re.compile(r"server\.log(?:\.([0-9]{4}-[0-9]{2}-[0-9]{2}))?\Z")
HEADER = re.compile(
    r"^(?P<stamp>[0-9]{4}-[0-9]{2}-[0-9]{2}[ T]"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[,.][0-9]{3})?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)"
    r"[ \t]+(?P<level>[A-Za-z]+)\b[ \t]*(?P<body>.*)$"
)
DATE_PREFIX = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
ERROR_LEVELS = {"ERROR", "FATAL", "SEVERE"}
WARN_LEVELS = {"WARN", "WARNING"}


def safe(text):
    # Keep log content from injecting terminal escape/control sequences.
    return CONTROL.sub(lambda m: "\\x%02x" % ord(m[0]), str(text)).expandtabs(4)


def parse_time(value, default_tz):
    value = value.replace(",", ".").replace("Z", "+00:00")
    # Normalize compact offsets for Python 3.9 as well as newer versions.
    value = re.sub(r"([+-][0-9]{2})([0-9]{2})$", r"\1:\2", value)
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=default_tz) if parsed.tzinfo is None else parsed


def range_time(name):
    value = os.environ[name]
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:[,.][0-9]{3})?", value
    ):
        raise ValueError(name + " must be YYYY-MM-DD HH:MM:SS[.mmm] (JST)")
    return parse_time(value, JST)


def candidate_files(root):
    for container in CONTAINERS:
        for service in SERVICES:
            mid = root / container / "logs" / service / "mid"
            try:
                directories = list(mid.iterdir())
            except FileNotFoundError:
                # Not every service/container combination needs to exist.
                continue
            # Prefer the task-ID path when current is an alias of that task.
            for directory in sorted(
                directories, key=lambda p: (p.name == "current", p.name)
            ):
                if directory.name != "current" and not TASK.fullmatch(directory.name):
                    continue
                if not stat.S_ISDIR(directory.stat().st_mode):
                    continue
                for path in sorted(directory.iterdir()):
                    match = FILENAME.fullmatch(path.name)
                    if not match:
                        continue
                    if match[1]:
                        try:
                            datetime.strptime(match[1], "%Y-%m-%d")
                        except ValueError:
                            continue
                    if stat.S_ISREG(path.stat().st_mode):
                        yield path, container, service, directory.name


def main():
    start, end = range_time("START_JST"), range_time("END_JST")
    if start >= end:
        raise ValueError("START_JST must be earlier than END_JST")
    source_zone = os.environ["LOG_TZ"]
    if source_zone not in ("JST", "UTC"):
        raise ValueError("LOG_TZ must be JST or UTC")
    source_tz = JST if source_zone == "JST" else timezone.utc
    color_mode = os.environ["LOG_COLOR"]
    if color_mode not in ("auto", "always", "never"):
        raise ValueError("LOG_COLOR must be auto, always or never")
    use_color = color_mode == "always" or (
        color_mode == "auto" and sys.stdout.isatty()
    )
    root = Path(os.environ["LOG_ROOT"])
    if not root.is_dir():
        raise ValueError("Log root is not a directory: " + str(root))

    events, warnings, seen = [], [], set()
    files = duplicates = 0
    for path, container, service, task in candidate_files(root):
        with path.open("r", encoding="utf-8", errors="backslashreplace") as stream:
            info = os.fstat(stream.fileno())
            identity = (container, service, info.st_dev, info.st_ino)
            if identity in seen:
                duplicates += 1
                continue
            seen.add(identity)
            files += 1
            event = None
            headers = orphans = 0
            for number, raw in enumerate(stream, 1):
                line = raw.rstrip("\r\n")
                match = HEADER.match(line)
                if match:
                    try:
                        stamp = parse_time(match["stamp"], source_tz).astimezone(JST)
                    except ValueError as exc:
                        raise ValueError(f"{path}:{number}: {exc}") from exc
                    headers += 1
                    event = None
                    if start <= stamp < end:
                        event = {
                            "time": stamp, "level": match["level"].upper(),
                            "service": service, "container": container,
                            "task": task, "path": path, "line": number,
                            "body": [match["body"]],
                        }
                        events.append(event)
                elif DATE_PREFIX.match(line):
                    raise ValueError(
                        f"{path}:{number}: unsupported timestamp/level format"
                    )
                elif headers == 0:
                    orphans += bool(line.strip())
                elif event is not None:
                    # Stack traces belong to the preceding timestamped event.
                    event["body"].append(line)
            if orphans:
                if headers == 0:
                    raise ValueError(f"{path}: no supported timestamped log entries")
                warnings.append(
                    f"{path}: skipped {orphans} leading nonempty line(s) without a timestamp"
                )

    if files == 0:
        raise ValueError("No matching log files under " + str(root))
    # Stable sorting preserves line order within a file for equal timestamps.
    events.sort(key=lambda item: item["time"])
    counts = Counter(event["level"] for event in events)
    error_count = sum(counts[level] for level in ERROR_LEVELS)
    warn_count = sum(counts[level] for level in WARN_LEVELS)

    def paint(text, code):
        text = safe(text)
        return f"\x1b[{code}m{text}\x1b[0m" if use_color and code else text

    print(paint("JBoss EAP | LOG VIEW | JST (UTC+09:00)", "1;36"))
    print(safe(f"Range: {start.isoformat(' ')} <= time < {end.isoformat(' ')}"))
    print(safe(f"Root: {root} | Log timezone when no offset is present: {source_zone}"))
    print(
        f"Files: {files} | Events: {len(events)} | "
        + paint(f"ERROR: {error_count}", "1;31") + " | "
        + paint(f"WARN: {warn_count}", "1;33")
        + f" | Duplicate paths skipped: {duplicates}"
    )
    for warning in warnings:
        print(paint("[SCAN WARNING] " + warning, "1;33"), file=sys.stderr)
    print(paint("=" * 96, "36"))
    print(paint(f"{'TIME (JST)':23}  {'LEVEL':7}  {'ECS SERVICE':11}  CONTAINER", "1;36"))
    for event in events:
        level = event["level"]
        code = "1;31" if level in ERROR_LEVELS else "1;33" if level in WARN_LEVELS else ""
        stamp = event["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(paint(
            f"{stamp}  {level:7}  {event['service']:11}  {event['container']}", code
        ))
        print(paint(
            f"  task={event['task']}  file={event['path'].name}:{event['line']}", "2"
        ))
        for line in event["body"]:
            print(paint("  " + line, code))
        print(paint("-" * 96, "2"))
    if not events:
        print("No matching log entries in the specified JST range.")
    return 0


try:
    status = main()
    sys.stdout.flush()
    sys.exit(status)
except BrokenPipeError:
    # A user closing less is a normal early exit, not a log-reading failure.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    sys.exit(0)
except (OSError, ValueError, KeyError) as exc:
    print("[SCAN ERROR] " + safe(exc), file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    sys.exit(130)
PY
