#!/usr/bin/env python3
"""Mac side of the Ops Board. Reads local status, pushes local/status.json to
the board repo, and speaks up if the board itself has stopped updating.

Run hourly by launchd from ~/Library/Application Support/OpsBoard, outside
CloudStorage, so it needs no Dropbox or Full Disk Access (the same independence
rule as the other heartbeats). Skips the push when nothing changed, except for
a keep-alive every 6 hours.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
REPO = "justinridesabike-creator/ops-board"
PAGES_URL = "https://justinridesabike-creator.github.io/ops-board/status.json"
GH = "/opt/homebrew/bin/gh"
SUPPORT = os.path.join(HOME, "Library/Application Support")
CACHE = os.path.join(SUPPORT, "OpsBoard/last_push.json")
STAMP = os.path.join(SUPPORT, "DatarideSync/last_success")
KEEPALIVE_S = 6 * 3600
BOARD_STALE_S = 6 * 3600


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_exit(label):
    """launchd's last exit code for an agent, or None if it isn't readable."""
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"last exit code = (\d+)", out)
    return int(m.group(1)) if m else None


def gather():
    try:
        with open(STAMP) as f:
            last_success = iso(int(f.readline().strip()))
    except (OSError, ValueError):
        last_success = None
    return dict(
        dataride=dict(last_success=last_success, last_exit=last_exit("com.justinevans.dataride-sync")),
        disk_free_gb=int(shutil.disk_usage(HOME).free / 1e9),
    )


def alert(text):
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "⚠️ Ops Board" sound name "Basso"'], check=False)
    subprocess.run(["/usr/bin/say", text], check=False)


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def push(payload):
    path = f"repos/{REPO}/contents/local/status.json"
    current = subprocess.run([GH, "api", path, "--jq", ".sha"], capture_output=True, text=True, timeout=60)
    bot = dict(name="ops-board-bot", email="ops-board-bot@users.noreply.github.com")  # public repo: no real email
    body = dict(message="Mac status", committer=bot, author=bot, content=base64.b64encode(json.dumps(payload, indent=1).encode()).decode())
    if current.returncode == 0 and current.stdout.strip():
        body["sha"] = current.stdout.strip()
    done = subprocess.run([GH, "api", "-X", "PUT", path, "--input", "-"], input=json.dumps(body),
                          capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "push failed")


def check_board(cache, now):
    """Dead-man's switch for the board itself. At most one alert per 6 hours."""
    # curl, not urllib: python.org Python has no SSL certificates on this Mac,
    # and a failed fetch here would look just like "offline" and never alert.
    got = subprocess.run(["/usr/bin/curl", "-sf", "--max-time", "20", PAGES_URL], capture_output=True, text=True)
    if got.returncode != 0:
        return  # offline, or not published yet: never a false alarm
    try:
        generated = datetime.fromisoformat(json.loads(got.stdout)["generated_at"].replace("Z", "+00:00")).timestamp()
    except (ValueError, KeyError):
        return
    if now - generated > BOARD_STALE_S and now - cache.get("board_alert", 0) > BOARD_STALE_S:
        alert("The Ops Board has stopped updating.")
        cache["board_alert"] = now


def main():
    status = gather()
    if "--dry-run" in sys.argv:
        print(json.dumps(dict(reported_at=iso(datetime.now(timezone.utc).timestamp()), **status), indent=1))
        return
    now = datetime.now(timezone.utc).timestamp()
    cache = load_cache()
    if status != cache.get("status") or now - cache.get("pushed", 0) > KEEPALIVE_S:
        push(dict(reported_at=iso(now), **status))
        cache.update(status=status, pushed=now)
    check_board(cache, now)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(cache, f)


if __name__ == "__main__":
    main()
