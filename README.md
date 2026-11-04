# Ops Board

One phone-friendly page showing whether every scheduled job is healthy.

- `build_board.py` runs in GitHub Actions, so it works with the Mac off. It reads run history for the cloud jobs and `local/status.json` for the Mac jobs, then publishes to GitHub Pages.
- `local_report.py` runs hourly on the Mac (`install.sh` sets it up). It pushes Dataride, CX autopilot and disk status, and speaks up if the board itself stops updating.
- **Alerts:** when a job newly turns bad, the workflow's last step fails, so GitHub's failed-run email is the alert. It fires once per problem, not every hour.
- **The page is public.** It shows job names and timing only. Never add client, rider or personal data.

## Secrets (repo Settings > Secrets > Actions)
- `OPS_READ_TOKEN`: fine-grained token, owner justinridesabike-creator. Repos: ranking-downloads, gmail-sorter-cloud, and the job-search repo. Permissions: Actions read, Contents read.
- `OPS_JOBS_REPO`: the job-search repo as owner/name. It's a secret so the name stays out of this public code.
- `OPS_CRUISE_TOKEN`: fine-grained token, owner checkcruiseprices. Repo: check-cruise-prices. Permissions: Actions read.

## Trigger
cron-job.org, hourly: POST `https://api.github.com/repos/justinridesabike-creator/ops-board/dispatches` with body `{"event_type":"build-board"}` and the `cron-job-dispatch` token (add this repo to that token first). GitHub's own schedule is only a backup.

## Adding a job
Add it to `CLOUD_JOBS` in `build_board.py` with a `stale_h` limit. Mac-only jobs go in `local_report.py` and `evaluate_local`.

## Tests
`python3 -m unittest test_board`
