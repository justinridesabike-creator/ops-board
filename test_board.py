import unittest
from datetime import datetime, timedelta, timezone

import build_board as b

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
JOB = dict(id="x", label="X", group="G", cadence="Daily", stale_h=30)


def run(conclusion, hours_ago):
    return dict(conclusion=conclusion, created_at=b.iso(NOW - timedelta(hours=hours_ago)), url="u")


class Runs(unittest.TestCase):
    def state(self, runs, **extra):
        return b.evaluate_runs({**JOB, **extra}, runs, NOW)

    def test_healthy(self):
        self.assertEqual(self.state([run("success", 2)])["state"], b.OK)

    def test_failed_latest_is_bad(self):
        self.assertEqual(self.state([run("failure", 1), run("success", 25)])["state"], b.BAD)

    def test_one_failure_tolerated_when_allowed(self):
        self.assertEqual(self.state([run("failure", 1), run("success", 2)], fail_tolerance=2)["state"], b.WARN)

    def test_two_failures_exceed_tolerance(self):
        r = self.state([run("failure", 1), run("failure", 2), run("success", 3)], fail_tolerance=2)
        self.assertEqual(r["state"], b.BAD)
        self.assertIn("2", r["detail"])

    def test_job_that_stopped_running_is_bad(self):
        self.assertEqual(self.state([run("success", 31)])["state"], b.BAD)

    def test_recovered_failure_stays_ok_with_note(self):
        r = self.state([run("success", 1), run("failure", 2)])
        self.assertEqual(r["state"], b.OK)
        self.assertIn("Recovered", r["detail"])

    def test_cancelled_runs_ignored(self):
        self.assertEqual(self.state([run("cancelled", 1), run("success", 2)])["state"], b.OK)

    def test_no_runs_is_unknown(self):
        self.assertEqual(self.state([])["state"], b.UNKNOWN)


class Local(unittest.TestCase):
    def rows(self, **over):
        base = dict(reported_at=b.iso(NOW - timedelta(hours=1)),
                    dataride=dict(last_success=b.iso(NOW - timedelta(days=2)), last_exit=0),
                    disk_free_gb=50)
        base.update(over)
        return {r["id"]: r for r in b.evaluate_local(base, NOW)}

    def test_all_good(self):
        self.assertTrue(all(r["state"] == b.OK for r in self.rows().values()))

    def test_dataride_failed_exit(self):
        r = self.rows(dataride=dict(last_success=b.iso(NOW - timedelta(days=2)), last_exit=1))
        self.assertEqual(r["dataride"]["state"], b.BAD)

    def test_dataride_too_old_even_if_mac_quiet(self):
        r = self.rows(reported_at=b.iso(NOW - timedelta(days=5)),
                      dataride=dict(last_success=b.iso(NOW - timedelta(days=9)), last_exit=0))
        self.assertEqual(r["dataride"]["state"], b.BAD)

    def test_sleeping_mac_is_not_a_disk_problem(self):
        r = self.rows(reported_at=b.iso(NOW - timedelta(days=2)), disk_free_gb=1)
        self.assertEqual(r["disk"]["state"], b.UNKNOWN)

    def test_low_disk(self):
        self.assertEqual(self.rows(disk_free_gb=4)["disk"]["state"], b.WARN)
        self.assertEqual(self.rows(disk_free_gb=1)["disk"]["state"], b.BAD)

    def test_no_report_yet(self):
        self.assertTrue(all(r["state"] == b.UNKNOWN for r in b.evaluate_local(None, NOW)))


class Alerts(unittest.TestCase):
    def test_only_on_transition(self):
        rows = [dict(id="a", state=b.BAD), dict(id="b", state=b.BAD), dict(id="c", state=b.OK)]
        prev = dict(rows=[dict(id="a", state=b.BAD), dict(id="b", state=b.OK), dict(id="c", state=b.BAD)])
        self.assertEqual([r["id"] for r in b.new_problems(rows, prev)], ["b"])

    def test_no_previous_is_quiet(self):
        self.assertEqual(b.new_problems([dict(id="a", state=b.BAD)], None), [])


class CruiseKind(unittest.TestCase):
    def step(self, name, conclusion):
        return dict(name=name, conclusion=conclusion)

    def test_stage(self):
        self.assertEqual(b.cruise_kind([self.step("Daily price catalog (every sailing)", "success"),
                                        self.step("Check out the outbox (x)", "success")]), "stage")

    def test_send(self):
        self.assertEqual(b.cruise_kind([self.step("Daily price catalog (every sailing)", "skipped"),
                                        self.step("Check out the price catalog (x)", "skipped"),
                                        self.step("Check out the outbox (x)", "success")]), "send")

    def test_new_signup_run_is_neither(self):
        self.assertIsNone(b.cruise_kind([self.step("Daily price catalog (every sailing)", "skipped"),
                                         self.step("Check out the price catalog (x)", "success"),
                                         self.step("Check out the outbox (x)", "skipped")]))


class Headline(unittest.TestCase):
    def test_unknown_never_reads_as_healthy(self):
        rows = [dict(state=b.OK), dict(state=b.UNKNOWN)]
        self.assertEqual(b.headline(rows), (b.UNKNOWN, "1 healthy, 1 with no data"))

    def test_all_ok(self):
        self.assertEqual(b.headline([dict(state=b.OK)]), (b.OK, "All jobs healthy"))

    def test_bad_outranks_warn(self):
        self.assertEqual(b.headline([dict(state=b.BAD), dict(state=b.WARN)])[1], "1 problem, 1 to check")


class Render(unittest.TestCase):
    def test_escapes_and_noindex(self):
        r = dict(id="a", label="A<b>", group="G", cadence="Daily", state=b.BAD, detail="x&y", last_success=None, url=None)
        page = b.render([r], NOW)
        self.assertIn("A&lt;b&gt;", page)
        self.assertIn("noindex", page)
        self.assertIn("1 problem", page)


if __name__ == "__main__":
    unittest.main()
