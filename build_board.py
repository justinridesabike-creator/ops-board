#!/usr/bin/env python3
"""Ops Board: one page showing whether every scheduled job is healthy.

Runs in GitHub Actions so it works with the Mac off. Reads GitHub run history
for the cloud jobs and local/status.json (pushed by local_report.py) for the
Mac jobs, then writes site/index.html + site/status.json.

Exit code stays 0. If a job newly went bad since the last published status,
site/new_problems.txt is written; the workflow's last step fails on that file
so GitHub's failed-run email is the alert, and only on the transition.

Standard library only. The page is public: never add personal data to it.
"""
import argparse
import html
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

PERSONAL = "justinridesabike-creator"
# Kept in a repo secret: this code and page are public, and the repo's name
# would reveal who is job hunting. Those jobs also get no links on the page.
JOBS_REPO = os.environ.get("OPS_JOBS_REPO", "")
CRUISE = "checkcruiseprices/check-cruise-prices"
HOUR = 3600
PAGES_URL = f"https://{PERSONAL}.github.io/ops-board/status.json"
OK, WARN, BAD, UNKNOWN = "ok", "warn", "bad", "unknown"
ORDER = {BAD: 0, WARN: 1, UNKNOWN: 2, OK: 3}
LABEL = {OK: "OK", WARN: "Check", BAD: "Problem", UNKNOWN: "No data"}
ICON = {OK: "✓", WARN: "!", BAD: "✕", UNKNOWN: "?"}

CLOUD_JOBS = [
    dict(id="rankings", label="USAC + UCI ranking downloads", group="Cycling", cadence="Tuesdays at noon",
         repo=f"{PERSONAL}/ranking-downloads", workflow="downloads.yml", stale_h=8 * 24),
    dict(id="gmail", label="Gmail Sorter", group="Personal", cadence="Hourly",
         repo=f"{PERSONAL}/gmail-sorter-cloud", workflow="sorter.yml", stale_h=3, fail_tolerance=2),
    dict(id="jobs-daily", label="Job search", group="Personal", cadence="Daily",
         repo=JOBS_REPO, workflow="daily-search.yml", stale_h=30, hide_link=True),
    dict(id="cruise-prices", label="Cruise price check", group="Check Cruise Prices", cadence="Daily, 6:30 AM",
         repo=CRUISE, workflow="check-prices.yml", stale_h=30, cruise="stage"),
    dict(id="cruise-send", label="Cruise emails sent", group="Check Cruise Prices", cadence="Daily, after review",
         repo=CRUISE, workflow="check-prices.yml", stale_h=30, cruise="send"),
]
COMMIT_JOBS = [
    # A Claude scheduled task: needs the Mac on, so going quiet is a warning.
    dict(id="jobs-hospital", label="Hospital job check", group="Personal", cadence="Daily, 8:30 AM",
         repo=JOBS_REPO, message="Update hospital seen jobs", stale_h=50),
]


def parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def human_age(seconds):
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 36 * HOUR:
        return f"{round(seconds / HOUR)} hr ago"
    return f"{round(seconds / 86400)} days ago"


def row(job, state, detail, last_success=None, url=None):
    return dict(id=job["id"], label=job["label"], group=job["group"], cadence=job["cadence"],
                state=state, detail=detail, last_success=iso(last_success), url=url)


def evaluate_runs(job, runs, now):
    """runs: completed runs newest first, each {conclusion, created_at, url}.
    Cancelled/skipped runs say nothing about health and are ignored."""
    runs = [r for r in runs if r.get("conclusion") in ("success", "failure", "timed_out", "startup_failure")]
    stale = timedelta(hours=job["stale_h"])
    if not runs:
        return row(job, UNKNOWN, "No runs found")
    latest = runs[0]
    success = next((r for r in runs if r["conclusion"] == "success"), None)
    last_ok = parse_ts(success["created_at"]) if success else None
    streak = 0
    for r in runs:
        if r["conclusion"] == "success":
            break
        streak += 1
    tolerance = job.get("fail_tolerance", 1)
    if streak >= tolerance:
        plural = "s" if streak > 1 else ""
        return row(job, BAD, f"Last {streak} run{plural} failed" if streak > 1 else "Last run failed",
                   last_ok, latest.get("url"))
    if not last_ok or now - last_ok > stale:
        return row(job, BAD, "Hasn't succeeded on schedule", last_ok, latest.get("url"))
    if streak:
        return row(job, WARN, "Latest run failed; watching the next one", last_ok, latest.get("url"))
    day_ago = now - timedelta(hours=24)
    failed = sum(1 for r in runs if r["conclusion"] != "success" and parse_ts(r["created_at"]) >= day_ago)
    note = f"Recovered from {failed} failed run{'s' if failed > 1 else ''} in the last 24 hr" if failed else "Running normally"
    return row(job, OK, note, last_ok, latest.get("url"))


def evaluate_commit(job, last_commit, now):
    if not last_commit:
        return row(job, UNKNOWN, "No runs found")
    if now - last_commit > timedelta(hours=job["stale_h"]):
        return row(job, WARN, "Quiet; it needs the Mac and Claude app on", last_commit)
    return row(job, OK, "Running normally", last_commit)


def evaluate_local(local, now):
    """The Mac's rows, from local/status.json. A sleeping Mac is not a failure,
    but a weekly job past its limit is bad however quiet the Mac has been."""
    base = dict(group="This Mac")
    dr = dict(id="dataride", label="Dataride results sync", cadence="Mondays, 9 AM", **base)
    disk = dict(id="disk", label="Mac free disk space", cadence="Hourly report", **base)
    tidy = dict(id="disk_tidy", label="Disk Tidy cache cleanup", cadence="Weekly (checks daily, 10:30 AM)", **base)
    if not local or not local.get("reported_at"):
        return [row(j, UNKNOWN, "The Mac hasn't reported yet") for j in (dr, disk, tidy)]
    reported = parse_ts(local["reported_at"])
    quiet = (now - reported).total_seconds()
    offline = quiet > 26 * HOUR
    offline_note = f"Mac last reported {human_age(quiet)}"

    d = local.get("dataride") or {}
    last_ok = parse_ts(d.get("last_success"))
    if not last_ok or now - last_ok > timedelta(days=8):
        dr_row = row(dr, BAD, "No successful sync in over 8 days", last_ok)
    elif d.get("last_exit") not in (0, None):
        dr_row = row(dr, BAD, f"Last run failed (exit {d['last_exit']}); rerun it", last_ok)
    else:
        dr_row = row(dr, OK, "Running normally", last_ok)

    free = local.get("disk_free_gb")
    if offline:
        disk_row = row(disk, UNKNOWN, offline_note, reported)
    elif free is None:
        disk_row = row(disk, UNKNOWN, "No reading", reported)
    else:
        state = BAD if free < 2 else WARN if free < 5 else OK
        disk_row = row(disk, state, f"{free} GB free", reported)

    t = local.get("disk_tidy")
    if t is None:
        tidy_row = row(tidy, UNKNOWN, "The Mac hasn't reported this job yet")
    else:
        t_ok = parse_ts(t.get("last_success"))
        if not t_ok or now - t_ok > timedelta(days=9):
            tidy_row = row(tidy, BAD, "No cleanup in over 9 days", t_ok)
        elif t.get("last_exit") not in (0, None):
            tidy_row = row(tidy, BAD, f"Last run failed (exit {t['last_exit']}); check its log", t_ok)
        else:
            tidy_row = row(tidy, OK, "Running normally", t_ok)
    return [dr_row, disk_row, tidy_row]


def new_problems(rows, previous):
    """Jobs that are bad now and weren't in the last published status. With no
    previous status (first run, page unreachable) stay quiet."""
    if not previous:
        return []
    was = {r["id"]: r["state"] for r in previous.get("rows", [])}
    return [r for r in rows if r["state"] == BAD and was.get(r["id"], OK) != BAD]


def headline(rows):
    bad = sum(r["state"] == BAD for r in rows)
    warn = sum(r["state"] == WARN for r in rows)
    if bad:
        return BAD, f"{bad} problem{'s' if bad > 1 else ''}" + (f", {warn} to check" if warn else "")
    if warn:
        return WARN, f"{warn} to check"
    unknown = sum(r["state"] == UNKNOWN for r in rows)
    if unknown == len(rows):
        return UNKNOWN, "No data yet"
    if unknown:
        return UNKNOWN, f"{len(rows) - unknown} healthy, {unknown} with no data"
    return OK, "All jobs healthy"


CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#17202b;--sub:#5b6673;--line:#dfe3e8;
--ok:#1a7f43;--warn:#9a6300;--bad:#b3261e;--unknown:#5b6673;
--ok-bg:#e4f4ea;--warn-bg:#fbf0d9;--bad-bg:#fbe6e4;--unknown-bg:#eceef1}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1e2329;--ink:#eef1f4;--sub:#a3adb8;--line:#333a42;
--ok:#63d08e;--warn:#efb955;--bad:#ff8f86;--unknown:#a3adb8;
--ok-bg:#183326;--warn-bg:#3a2d12;--bad-bg:#42201d;--unknown-bg:#2a3037}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:680px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:15px;font-weight:600;color:var(--sub);margin:0 0 4px}
.head{font-size:28px;font-weight:700;margin:0 0 4px}
.head.ok{color:var(--ok)}.head.warn{color:var(--warn)}.head.bad{color:var(--bad)}.head.unknown{color:var(--unknown)}
.when{color:var(--sub);font-size:14px;margin:0 0 20px}
.stale{background:var(--bad-bg);color:var(--bad);padding:12px 14px;border-radius:10px;font-weight:600;margin:0 0 16px}
h2{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--sub);margin:22px 0 8px}
.job{display:flex;gap:12px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 8px;color:inherit;text-decoration:none}
.dot{flex:none;width:26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px}
.ok .dot{background:var(--ok-bg);color:var(--ok)}.warn .dot{background:var(--warn-bg);color:var(--warn)}
.bad .dot{background:var(--bad-bg);color:var(--bad)}.unknown .dot{background:var(--unknown-bg);color:var(--unknown)}
.body{min-width:0;flex:1}.name{font-weight:600}.detail{font-size:14px;color:var(--sub)}
.bad .detail{color:var(--bad)}.warn .detail{color:var(--warn)}
.meta{font-size:13px;color:var(--sub);margin-top:2px}
"""

SCRIPT = """
function ago(s){if(s<90)return'just now';if(s<5400)return Math.round(s/60)+' min ago';
if(s<129600)return Math.round(s/3600)+' hr ago';return Math.round(s/86400)+' days ago'}
function tick(){var now=Date.now()/1000;
document.querySelectorAll('[data-ts]').forEach(function(e){e.textContent=ago(now-Date.parse(e.dataset.ts)/1000)});
var g=Date.parse(document.body.dataset.generated)/1000;
document.getElementById('stale').hidden=(now-g)<3*3600}
tick();setInterval(tick,60000);
"""


def job_html(r):
    last = (f'<div class="meta">Last good run <span data-ts="{r["last_success"]}"></span> · {html.escape(r["cadence"])}</div>'
            if r["last_success"] else f'<div class="meta">{html.escape(r["cadence"])}</div>')
    inner = (f'<span class="dot" aria-hidden="true">{ICON[r["state"]]}</span><div class="body">'
             f'<div class="name">{html.escape(r["label"])}</div>'
             f'<div class="detail">{LABEL[r["state"]]}: {html.escape(r["detail"])}</div>{last}</div>')
    if r.get("url"):
        return f'<a class="job {r["state"]}" href="{html.escape(r["url"])}">{inner}</a>'
    return f'<div class="job {r["state"]}">{inner}</div>'


def render(rows, generated):
    state, text = headline(rows)
    attention = sorted((r for r in rows if r["state"] in (BAD, WARN)), key=lambda r: ORDER[r["state"]])
    sections = []
    if attention:
        sections.append("<h2>Needs attention</h2>" + "".join(job_html(r) for r in attention))
    groups = {}
    for r in rows:
        if r["state"] not in (BAD, WARN):
            groups.setdefault(r["group"], []).append(r)
    for group in sorted(groups):
        sections.append(f"<h2>{html.escape(group)}</h2>" + "".join(job_html(r) for r in groups[group]))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta http-equiv="refresh" content="300">
<title>Ops Board</title><style>{CSS}</style></head>
<body data-generated="{iso(generated)}"><main>
<h1>Ops Board</h1><p class="head {state}">{html.escape(text)}</p>
<p class="when">Updated <span data-ts="{iso(generated)}"></span></p>
<p class="stale" id="stale" hidden>This board hasn't updated in over 3 hours, so these results may be out of date.</p>
{''.join(sections)}
</main><script>{SCRIPT}</script></body></html>"""


def api(path, token):
    req = urllib.request.Request(f"https://api.github.com/{path}", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ops-board"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_runs(job, token, per_page=30):
    data = api(f"repos/{job['repo']}/actions/workflows/{job['workflow']}/runs?status=completed&per_page={per_page}", token)
    return [dict(id=r["id"], conclusion=r["conclusion"], created_at=r["created_at"], url=r["html_url"])
            for r in data["workflow_runs"]]


def cruise_kind(steps):
    """Which kind of cruise run this was, from the steps that actually ran."""
    ran = {s["name"].split(" (")[0]: s["conclusion"] for s in steps}
    if ran.get("Daily price catalog") in ("success", "failure"):
        return "stage"
    if ran.get("Check out the price catalog") == "skipped" and ran.get("Check out the outbox") == "success":
        return "send"
    return None


def cruise_rows(jobs, token, limit=15):
    runs = fetch_runs(jobs[0], token, per_page=limit)
    sorted_runs = {"stage": [], "send": []}
    for run in runs:
        detail = api(f"repos/{CRUISE}/actions/runs/{run['id']}/jobs", token)
        steps = [s for j in detail["jobs"] for s in j["steps"]]
        kind = cruise_kind(steps)
        if kind:
            sorted_runs[kind].append(run)
    return sorted_runs


def safe(job, fn):
    """One job's API trouble must not take the whole board down."""
    try:
        return fn()
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
        return row(job, UNKNOWN, f"Couldn't read status ({type(e).__name__})")


def collect(now, personal_token, cruise_token, local):
    rows = []
    cruise_jobs = [j for j in CLOUD_JOBS if j.get("cruise")]
    grouped = None
    if cruise_token:
        try:
            grouped = cruise_rows(cruise_jobs, cruise_token)
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
            grouped = None
    for job in CLOUD_JOBS:
        if job.get("cruise"):
            if grouped is None:
                rows.append(row(job, UNKNOWN, "Couldn't read status"))
            else:
                rows.append(evaluate_runs(job, grouped[job["cruise"]], now))
        else:
            result = safe(job, lambda j=job: evaluate_runs(j, fetch_runs(j, personal_token), now))
            if job.get("hide_link"):
                result["url"] = None
            rows.append(result)
    for job in COMMIT_JOBS:
        def latest(j=job):
            commits = api(f"repos/{j['repo']}/commits?per_page=30", personal_token)
            hit = next((c for c in commits if c["commit"]["message"].startswith(j["message"])), None)
            return evaluate_commit(j, parse_ts(hit["commit"]["author"]["date"]) if hit else None, now)
        rows.append(safe(job, latest))
    rows.extend(evaluate_local(local, now))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default="local/status.json")
    ap.add_argument("--out", default="site")
    ap.add_argument("--no-previous", action="store_true", help="skip the alert check (local previews)")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    try:
        with open(args.local) as f:
            local = json.load(f)
    except (OSError, ValueError):
        local = None
    personal = os.environ.get("OPS_READ_TOKEN", "")
    cruise = os.environ.get("OPS_CRUISE_TOKEN", "")
    rows = collect(now, personal, cruise, local)
    previous = None
    if not args.no_previous:
        try:
            with urllib.request.urlopen(PAGES_URL, timeout=20) as resp:
                previous = json.load(resp)
        except (urllib.error.URLError, ValueError, TimeoutError):
            previous = None
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w") as f:
        f.write(render(rows, now))
    with open(os.path.join(args.out, "status.json"), "w") as f:
        json.dump(dict(generated_at=iso(now), rows=rows), f, indent=1)
    fresh = new_problems(rows, previous)
    marker = os.path.join(args.out, "new_problems.txt")
    if fresh:
        with open(marker, "w") as f:
            f.write("\n".join(f"{r['label']}: {r['detail']}" for r in fresh) + "\n")
    elif os.path.exists(marker):
        os.remove(marker)
    print(headline(rows)[1])
    for r in rows:
        print(f"  {r['state']:8}{r['label']}: {r['detail']}")


if __name__ == "__main__":
    main()
