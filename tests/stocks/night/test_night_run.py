"""The night's orchestration (app/stocks/night.py) with the data steps and the AI package faked:
steps in order and recorded, partial and failed runs, the 02:00 wait, the 06:00 deadline, `stopped`,
reruns, the AI package missing, and the October DST night."""

import json
from datetime import date, timedelta

import pytest
from night_helpers import FakeAI, FakeClock, local

from app.stocks import config, night, sources

DAY = date(2026, 9, 30)       # a Wednesday: no deep research by default
DEEP_DAY = date(2026, 10, 1)  # a Thursday


def steps_of(conn, run_id):
    return json.loads(conn.execute("SELECT steps_json FROM stock_runs WHERE id = ?", (run_id,)).fetchone()["steps_json"])


def run_row(conn, run_id):
    return conn.execute("SELECT * FROM stock_runs WHERE id = ?", (run_id,)).fetchone()


def five(follow):
    return [follow(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]


def test_a_full_night_records_every_step_in_order(conn, follow, clock, quiet_data, ai, sent):
    five(follow)
    fake = ai(FakeAI(["AAA", "BBB", "CCC", "DDD", "EEE"]))
    config.save_settings(conn, {"deep_days": ["wed"]})
    run_id = night.run(conn, DAY, now=clock)

    steps = steps_of(conn, run_id)
    assert list(steps) == list(night.STEPS)
    for name, step in steps.items():
        assert step["status"] == "succeeded", (name, step)
        assert step["started_at"] <= step["finished_at"]
    assert run_row(conn, run_id)["status"] == "succeeded"
    assert run_row(conn, run_id)["finished_at"] is not None
    assert [c["step"] for c in fake.calls] == ["extract", "research", "verdict"]
    assert steps["extract"]["detail"]["done"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert steps["notes"]["detail"]["written"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert conn.execute("SELECT COUNT(*) FROM notes WHERE folder = 'aktsiad'").fetchone()[0] == 5
    digest = conn.execute("SELECT * FROM stock_notices WHERE dedupe_key = 'digest:2026-09-30'").fetchone()
    assert digest["sent_at"] is None and digest["body"].startswith("Öö\n• Õnnestus (süvauuringu öö)")
    assert not [s for s in sent if s[1].startswith("Aktsiate öö")]  # a good night sends no night alert
    assert [c[0] for c in quiet_data] == ["prices", "ingest"]


def test_ai_steps_wait_for_two_oclock_and_stop_at_six(conn, follow, clock, quiet_data, ai):
    five(follow)
    fake = ai(FakeAI(["AAA"]))
    night.run(conn, DAY, now=clock)
    assert clock.slept == [1800]
    assert fake.calls[0]["at"] == local(DAY, 2, 0)
    assert fake.calls[0]["deadline"] == local(DAY, 6, 0)


def test_a_failed_source_makes_the_night_partial(conn, follow, clock, quiet_data, ai, monkeypatch, sent):
    follow("AAA")
    ai(FakeAI(["AAA"]))
    monkeypatch.setattr(sources, "ingest", lambda conn, now, run_id=None: {
        "edgar": {"status": "succeeded", "fetched": 3, "new": 1, "error": None},
        "finnhub": {"status": "failed", "fetched": 0, "new": 0, "error": "HTTP 503"}})
    run_id = night.run(conn, DAY, now=clock)
    steps = steps_of(conn, run_id)
    assert steps["ingest"]["status"] == "partial"
    assert steps["prices"]["status"] == "succeeded"
    assert run_row(conn, run_id)["status"] == "partial"
    alert = [s for s in sent if s[1].startswith("Aktsiate öö")]
    assert alert == [(alert[0][0], "Aktsiate öö 30.09: osaliselt", "Osaliselt: uudised.")]


def test_a_step_that_raises_is_failed_and_the_night_goes_on(conn, follow, clock, quiet_data, ai, monkeypatch):
    follow("AAA")
    ai(FakeAI(["AAA"]))

    def broken(conn, today):
        raise RuntimeError("Yahoo kadus")
    monkeypatch.setattr(night.market, "refresh_prices", broken)
    run_id = night.run(conn, DAY, now=clock)
    steps = steps_of(conn, run_id)
    assert steps["prices"] == {**steps["prices"], "status": "failed", "detail": {"error": "RuntimeError: Yahoo kadus"}}
    assert steps["ingest"]["status"] == "succeeded" and steps["verdict"]["status"] == "succeeded"
    assert run_row(conn, run_id)["status"] == "partial"


def test_every_step_failing_is_a_failed_night(conn, clock, monkeypatch):
    def broken(n):
        raise RuntimeError("kõik on katki")
    monkeypatch.setattr(night, "STEP_FUNCS", {name: broken for name in night.STEPS})
    run_id = night.run(conn, DAY, now=clock)
    assert run_row(conn, run_id)["status"] == "failed"


def test_stopped_on_stock_three_of_five_skips_the_later_ai_steps(conn, follow, clock, quiet_data, ai, sent):
    five(follow)
    fake = ai(FakeAI(["AAA", "BBB", "CCC", "DDD", "EEE"], stop_at={"CCC": "Kasutuspiir on täis"}))
    run_id = night.run(conn, DEEP_DAY, now=clock)
    steps = steps_of(conn, run_id)
    extract = steps["extract"]
    assert extract["status"] == "partial"
    assert extract["detail"]["done"] == ["AAA", "BBB"]
    assert [s["symbol"] for s in extract["detail"]["skipped"]] == ["CCC", "DDD", "EEE"]
    assert extract["detail"]["stopped"] == "Kasutuspiir on täis"
    for name in ("research", "verdict"):
        assert steps[name]["status"] == "skipped"
        assert steps[name]["detail"] == {"reason": "Kasutuspiir on täis", "stopped": "Kasutuspiir on täis"}
    assert [c["step"] for c in fake.calls] == ["extract"]
    # The steps after the AI ones still run.
    assert steps["notes"]["status"] == "succeeded" and steps["digest"]["status"] == "succeeded"
    assert run_row(conn, run_id)["status"] == "partial"
    alert = [s for s in sent if s[1].startswith("Aktsiate öö")][0]
    assert "Peatus: Kasutuspiir on täis." in alert[2]


def test_the_six_oclock_deadline_stops_the_ai_steps(conn, follow, clock, quiet_data, ai):
    five(follow)
    # 02:00 + 5 stocks x 50 min passes 06:00 during the fifth stock's turn.
    fake = ai(FakeAI(["AAA", "BBB", "CCC", "DDD", "EEE"], per_stock=timedelta(minutes=50)))
    run_id = night.run(conn, DAY, now=clock)
    steps = steps_of(conn, run_id)
    assert steps["extract"]["detail"]["done"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    # Extraction ended at 06:10; the verdict step was never started.
    assert steps["verdict"]["status"] == "skipped"
    assert steps["verdict"]["detail"]["stopped"] == night.PAST_DEADLINE
    assert [c["step"] for c in fake.calls] == ["extract"]
    assert run_row(conn, run_id)["status"] == "partial"


def test_the_deadline_passed_inside_a_step_is_its_stop(conn, follow, clock, quiet_data, ai):
    five(follow)
    fake = ai(FakeAI(["AAA", "BBB", "CCC", "DDD", "EEE"], per_stock=timedelta(minutes=90)))
    run_id = night.run(conn, DAY, now=clock)
    steps = steps_of(conn, run_id)
    assert steps["extract"]["detail"]["done"] == ["AAA", "BBB", "CCC"]
    assert steps["extract"]["detail"]["stopped"] == "Öö aeg sai läbi"
    assert steps["verdict"]["status"] == "skipped"
    assert len(fake.calls) == 1


def test_no_research_on_other_days(conn, follow, clock, quiet_data, ai):
    follow("AAA")
    fake = ai(FakeAI(["AAA"]))
    run_id = night.run(conn, DAY, now=clock)
    steps = steps_of(conn, run_id)
    assert steps["research"] == {**steps["research"], "status": "skipped", "detail": {"reason": night.NOT_DEEP}}
    assert [c["step"] for c in fake.calls] == ["extract", "verdict"]
    assert run_row(conn, run_id)["status"] == "succeeded"


def test_without_the_ai_package_its_steps_are_skipped_not_failed(conn, follow, clock, quiet_data, no_ai):
    follow("AAA")
    run_id = night.run(conn, DEEP_DAY, now=clock)
    steps = steps_of(conn, run_id)
    for name in ("extract", "research", "verdict"):
        assert steps[name]["status"] == "skipped"
        assert steps[name]["detail"] == {"reason": "AI pakett puudub"}
    assert run_row(conn, run_id)["status"] == "succeeded"
    assert not hasattr(clock, "slept")  # nothing to wait for


def test_a_rerun_of_the_date_reuses_its_row_and_skips_what_is_done(conn, follow, clock, quiet_data, ai, monkeypatch):
    follow("AAA")
    fake = ai(FakeAI(["AAA"]))
    calls = {"n": 0}

    def flaky(conn, now, run_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("võrk kadus")
        return {"edgar": {"status": "succeeded", "fetched": 1, "new": 1, "error": None}}
    monkeypatch.setattr(sources, "ingest", flaky)
    first = night.run(conn, DAY, now=clock)
    assert steps_of(conn, first)["ingest"]["status"] == "failed"
    clock.advance(hours=1)
    again = night.run(conn, DAY, now=clock)
    assert again == first
    assert conn.execute("SELECT COUNT(*) FROM stock_runs").fetchone()[0] == 1
    steps = steps_of(conn, again)
    assert steps["ingest"]["status"] == "succeeded"
    assert [c[0] for c in quiet_data] == ["prices"]  # prices had succeeded: not again
    assert [c["step"] for c in fake.calls] == ["extract", "verdict"]  # nor the AI steps
    assert run_row(conn, again)["status"] == "succeeded"
    # No duplicates of what the night writes itself.
    assert conn.execute("SELECT COUNT(*) FROM notes WHERE folder = 'aktsiad'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM stock_notices WHERE kind = 'digest'").fetchone()[0] == 1


def test_only_runs_one_step(conn, follow, clock, quiet_data, ai):
    follow("AAA")
    ai(FakeAI(["AAA"]))
    run_id = night.run(conn, DAY, now=clock, only="prices")
    assert list(steps_of(conn, run_id)) == ["prices"]
    with pytest.raises(ValueError):
        night.run(conn, DAY, now=clock, only="predict")


def test_a_run_by_hand_after_six_gets_its_own_ai_time(conn, follow, quiet_data, ai, monkeypatch):
    follow("AAA")
    fake = ai(FakeAI(["AAA"]))
    c = FakeClock(local(DAY, 14, 0))
    monkeypatch.setattr(night, "_sleep", c.sleep)
    night.run(conn, DAY, now=c)
    assert not hasattr(c, "slept")
    assert fake.calls[0]["deadline"] == local(DAY, 14, 0) + night.MANUAL_BUDGET


def test_the_october_dst_night(conn, follow, quiet_data, ai, monkeypatch):
    """2026-10-25: at 04:00 summer time the clocks go back to 03:00. 01:30 is 22:30Z, 02:00 is
    23:00Z and 06:00 (winter time) is 04:00Z: the AI steps get five real hours, through the
    repeated hour, and stop at the right 06:00."""
    day = date(2026, 10, 25)
    follow("AAA")
    fake = ai(FakeAI([f"S{i}" for i in range(8)], per_stock=timedelta(minutes=40)))
    c = FakeClock(local(day, 1, 30))
    monkeypatch.setattr(night, "_sleep", c.sleep)
    run_id = night.run(conn, day, now=c)
    call = fake.calls[0]
    assert call["at"].isoformat() == "2026-10-24T23:00:00+00:00"
    assert call["deadline"].isoformat() == "2026-10-25T04:00:00+00:00"
    assert c.slept == [1800]
    extract = steps_of(conn, run_id)["extract"]["detail"]
    # 23:00Z + 7 x 40 min = 03:40Z < 04:00Z; the eighth starts at 03:40Z and ends past it.
    assert extract["done"] == [f"S{i}" for i in range(8)]
    assert steps_of(conn, run_id)["verdict"]["detail"]["stopped"] == night.PAST_DEADLINE


def test_record_skipped_once_per_date(conn, sent, follow):
    follow("AAA")
    now = local(DAY, 9, 0)
    assert night.record_skipped(conn, DAY, now) is True
    assert night.record_skipped(conn, DAY, now) is False
    row = conn.execute("SELECT * FROM stock_runs").fetchone()
    assert row["status"] == "skipped"
    assert json.loads(row["steps_json"])["night"]["detail"]["reason"] == night.MISSED
    assert [s[1] for s in sent] == ["Aktsiate öö 30.09: jäi vahele"]


def test_a_skipped_night_run_later_by_hand_drops_its_marker(conn, follow, quiet_data, no_ai, sent):
    follow("AAA")
    night.record_skipped(conn, DAY, local(DAY, 9, 0))
    run_id = night.run(conn, DAY, now=local(DAY, 10, 0))
    assert "night" not in steps_of(conn, run_id)
    assert run_row(conn, run_id)["status"] == "succeeded"


def test_has_problems():
    assert not night.has_problems({"done": ["A"], "skipped": [{"symbol": "B", "reason": "juba tehtud"}], "failed": [], "stopped": None})
    assert night.has_problems({"done": [], "failed": [{"symbol": "B", "error": "x"}]})
    assert night.has_problems({"refreshed": {}, "failed": {}, "fx": {"rates": {}, "failed": {"USD": "503"}}})
    assert night.has_problems({"edgar": {"status": "partial"}})
    assert not night.has_problems({"edgar": {"status": "skipped", "error": "võti puudub"}})
