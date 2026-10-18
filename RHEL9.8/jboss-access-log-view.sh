#!/usr/bin/env bash
# Paste this entire file into the remote Bash session, or run with bash.
# Edit the example range below. START is inclusive; END is exclusive, in JST.
LOG_ROOT="${LOG_ROOT:-/mnt/logs}" \
START_JST="${START_JST:-2026-08-20 00:00:00}" \
END_JST="${END_JST:-2026-08-21 00:00:00}" \
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
FILENAME = re.compile(r"access_log(?:\.([0-9]{4}-[0-9]{2}-[0-9]{2}))?\.log\Z")
HEADER = re.compile(
    r'(?P<client>\S+)[ \t]+(?P<ident>\S+)[ \t]+(?P<user>\S+)[ \t]+'
    r'\[(?P<stamp>[^\]]+)\][ \t]+"(?P<request>(?:[^"\\]|\\.)*)"[ \t]+'
    r'(?P<status>[1-5][0-9]{2})[ \t]+(?P<bytes>[0-9]+|-)'
    r'(?:[ \t]+(?P<extra>.*))?\Z'
)
ACCESS_TIME = re.compile(
    r"(?P<day>[0-9]{2})/(?P<month>[A-Za-z]{3})/(?P<year>[0-9]{4}):"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:[.,](?P<millis>[0-9]{3}))? "
    r"(?P<sign>[+-])(?P<zone_hour>[0-9]{2}):?(?P<zone_minute>[0-9]{2})\Z"
)
MONTHS = {
    month: number for number, month in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text):
    # Escape log-controlled terminal sequences before adding our own colors.
    return CONTROL.sub(lambda m: "\\x%02x" % ord(m[0]), str(text)).expandtabs(4)


def paint(text, code, stream=None):
    stream = sys.stdout if stream is None else stream
    mode = os.environ.get("LOG_COLOR", "auto")
    colored = mode == "always" or (mode == "auto" and stream.isatty())
    text = safe(text)
    return f"\x1b[{code}m{text}\x1b[0m" if colored and code else text


def range_time(name):
    value = os.environ[name]
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:[.,][0-9]{3})?", value
    ):
        raise ValueError(name + " must be YYYY-MM-DD HH:MM:SS[.mmm] (JST)")
    return datetime.fromisoformat(value.replace(",", ".")).replace(tzinfo=JST)


def access_time(value):
    match = ACCESS_TIME.fullmatch(value)
    if match is None or match["month"] not in MONTHS:
        raise ValueError("expected DD/Mon/YYYY:HH:MM:SS[.mmm] +HHMM timestamp")
    hours, minutes = int(match["zone_hour"]), int(match["zone_minute"])
    if hours > 23 or minutes > 59:
        raise ValueError("invalid timestamp UTC offset")
    offset = timedelta(hours=hours, minutes=minutes)
    if match["sign"] == "-":
        offset = -offset
    # Avoid %b parsing, whose result would depend on the host's locale.
    return datetime(
        int(match["year"]), MONTHS[match["month"]], int(match["day"]),
        int(match["hour"]), int(match["minute"]), int(match["second"]),
        int(match["millis"] or "0") * 1000, tzinfo=timezone(offset),
    ).astimezone(JST)


def candidate_files(root):
    for container in CONTAINERS:
        for service in SERVICES:
            mid = root / container / "logs" / service / "mid"
            try:
                directories = list(mid.iterdir())
            except FileNotFoundError:
                # Not every service/container combination needs to exist.
                continue
            # Prefer task IDs to current when they refer to the same files.
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
    if os.environ["LOG_COLOR"] not in ("auto", "always", "never"):
        raise ValueError("LOG_COLOR must be auto, always or never")
    root = Path(os.environ["LOG_ROOT"])
    if not root.is_dir():
        raise ValueError("Log root is not a directory: " + str(root))

    events, seen = [], set()
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
            for number, raw in enumerate(stream, 1):
                line = raw.rstrip("\r\n")
                if not line.strip():
                    continue
                match = HEADER.fullmatch(line)
                if match is None:
                    raise ValueError(
                        f"{path}:{number}: unsupported access log format; "
                        "expected common/combined with a quoted request and HTTP status"
                    )
                try:
                    stamp = access_time(match["stamp"])
                except (ValueError, OverflowError) as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc
                if start <= stamp < end:
                    events.append({
                        "time": stamp, "status": int(match["status"]),
                        "service": service, "container": container,
                        "task": task, "path": path, "line": number,
                        "client": match["client"], "ident": match["ident"],
                        "user": match["user"], "request": match["request"],
                        "bytes": match["bytes"], "extra": match["extra"],
                    })

    if files == 0:
        raise ValueError("No matching access log files under " + str(root))
    # Stable sorting preserves file/line order for equal instants.
    events.sort(key=lambda item: item["time"])
    counts = Counter(event["status"] // 100 for event in events)
    print(paint("JBoss EAP | ACCESS LOG VIEW | JST (UTC+09:00)", "1;36"))
    print(safe(f"Range: {start.isoformat(' ')} <= time < {end.isoformat(' ')}"))
    print(safe(f"Root: {root} | Log timestamp offsets converted to JST"))
    print(
        f"Files: {files} | Requests: {len(events)} | "
        + paint(f"ERROR (5xx): {counts[5]}", "1;31") + " | "
        + paint(f"WARN (4xx): {counts[4]}", "1;33")
        + f" | Duplicate paths skipped: {duplicates}"
    )
    print(paint("=" * 96, "36"))
    print(paint(
        f"{'TIME (JST)':23}  {'HTTP':4}  {'LEVEL':5}  "
        f"{'ECS SERVICE':11}  CONTAINER", "1;36"
    ))
    for event in events:
        status = event["status"]
        level, code = ("ERROR", "1;31") if status >= 500 else (
            ("WARN", "1;33") if status >= 400 else ("INFO", "")
        )
        stamp = event["time"].isoformat(" ", timespec="milliseconds")[:-6]
        print(paint(
            f"{stamp}  {status:<4}  {level:5}  "
            f"{event['service']:11}  {event['container']}", code
        ))
        print(paint("  " + event["request"], code))
        print(safe(
            f"  client={event['client']}  bytes={event['bytes']}  "
            f"user={event['user']}  ident={event['ident']}"
        ))
        print(paint(
            f"  task={event['task']}  file={event['path'].name}:{event['line']}", "2"
        ))
        if event["extra"]:
            print(paint("  extra=" + event["extra"], code))
        print(paint("-" * 96, "2"))
    if not events:
        print("No matching access log entries in the specified JST range.")
    return 0


try:
    status = main()
    sys.stdout.flush()
    sys.exit(status)
except BrokenPipeError:
    # Closing less is a normal early exit, not a failed scan.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    sys.exit(0)
except (OSError, ValueError, KeyError) as exc:
    print(paint("[SCAN ERROR] " + str(exc), "1;31", sys.stderr), file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    sys.exit(130)
PY
