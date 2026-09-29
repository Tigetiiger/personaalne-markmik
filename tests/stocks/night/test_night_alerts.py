"""Alerts and the digest (app/stocks/alerts.py): each rule, one send per dedupe key, ntfy not set
up, a failed send recorded and retried, and what the morning digest says."""

import json
from datetime import date, timedelta

import pytest
from night_helpers import local

from app import reminders
from app.stocks import alerts, config

NOW = local(date(2026, 9, 30), 16, 0)
DAY = date(2026, 9, 30)


def notice(conn, key):
    return conn.execute("SELECT * FROM stock_notices WHERE dedupe_key = ?", (key,)).fetchone()


def eight_k(conn, iid, accession="0001-26-000001", fetched=NOW - timedelta(hours=2), form="8-K"):
    doc = conn.execute(
        "INSERT INTO market_docs (source, external_id, url, content_hash, title, fetched_at, meta_json) "
        "VALUES ('edgar', ?, ?, ?, 'Ettevõte teatas juhi vahetusest', ?, ?)",
        (accession, f"https://www.sec.gov/Archives/{accession}", accession,
         fetched.strftime("%Y-%m-%dT%H:%M:%SZ"), json.dumps({"form": form, "accession": accession, "items": ["5.02"]}))).lastrowid
    conn.execute("INSERT INTO market_doc_instruments VALUES (?, ?)", (doc, iid))
    return doc


def test_a_new_8k_is_sent_once(conn, follow, sent):
    iid = follow("NVDA", "NVIDIA")
    eight_k(conn, iid)
    assert alerts.check(conn, NOW) == 1
    assert alerts.check(conn, NOW + timedelta(minutes=5)) == 0
    assert len(sent) == 1
    cfg, title, message = sent[0]
    assert title == "NVDA: uus 8-K"
    assert message == "Ettevõte teatas juhi vahetusest (punktid 5.02)\nhttps://www.sec.gov/Archives/0001-26-000001"
    # The vault's server and token, the stocks topic.
    assert cfg == {"url": "https://ntfy.example.ee", "topic": "stock-oracle", "token": "tk"}
    row = notice(conn, "8k:0001-26-000001")
    assert row["sent_at"] is not None and row["error"] is None and row["kind"] == "8k"


def test_old_filings_and_10qs_do_not_alert(conn, follow, sent):
    iid = follow("NVDA")
    eight_k(conn, iid, "old", fetched=NOW - timedelta(hours=40))
    eight_k(conn, iid, "tenq", form="10-Q")
    assert alerts.check(conn, NOW) == 0


def test_the_topic_comes_from_seaded(conn, follow, sent):
    config.save_settings(conn, {"ntfy_topic": "aktsiad-test"})
    eight_k(conn, follow("NVDA"))
    alerts.check(conn, NOW)
    assert sent[0][0]["topic"] == "aktsiad-test"


def test_without_ntfy_alerts_are_recorded_not_sent(conn, follow, monkeypatch):
    monkeypatch.setattr(reminders, "publish", lambda *a: pytest.fail("sent without ntfy"))
    eight_k(conn, follow("NVDA"))
    assert alerts.check(conn, NOW) == 0
    row = notice(conn, "8k:0001-26-000001")
    assert row["sent_at"] is None and row["error"] == alerts.NOT_SET


def test_a_failed_send_is_recorded_and_retried(conn, follow, sent, monkeypatch):
    iid = follow("NVDA")
    eight_k(conn, iid, "a")
    eight_k(conn, iid, "b")

    def down(cfg, title, message):
        raise RuntimeError("ntfy vastas 502")
    monkeypatch.setattr(reminders, "publish", down)
    assert alerts.check(conn, NOW) == 0
    assert notice(conn, "8k:a")["error"] == "ntfy vastas 502" and notice(conn, "8k:a")["sent_at"] is None
    # The pass stopped at the first failure: the second was not tried (nor recorded).
    assert notice(conn, "8k:b") is None
    monkeypatch.setattr(reminders, "publish", lambda cfg, title, message: sent.append((cfg, title, message)))
    assert alerts.check(conn, NOW + timedelta(minutes=5)) == 2
    assert notice(conn, "8k:a")["error"] is None and notice(conn, "8k:a")["sent_at"] is not None
    # Bounded: past the look-back the rule no longer fires, so an unsent alert is let go.
    assert alerts.check(conn, NOW + timedelta(hours=40)) == 0


def test_big_moves_on_holdings_only(conn, follow, hold, bars, sent):
    held, watched, calm = follow("AAA", "Aaa AS"), follow("BBB"), follow("CCC")
    hold(held)
    hold(calm)
    bars(held, [("2026-09-28", "100"), ("2026-09-29", "106.5")])
    bars(watched, [("2026-09-28", "100"), ("2026-09-29", "120")])
    bars(calm, [("2026-09-28", "100"), ("2026-09-29", "104")])
    assert alerts.check(conn, NOW) == 1
    assert sent[0][1:] == ("AAA +6,5 %", "Aaa AS liikus 29.09 +6,5 %, hind 106,50 USD.")
    assert notice(conn, "move:AAA:2026-09-29") is not None


def test_a_quote_newer_than_the_last_bar_is_the_move(conn, follow, hold, bars, sent):
    iid = follow("AAA")
    hold(iid)
    bars(iid, [("2026-09-28", "100"), ("2026-09-29", "101")])
    conn.execute("INSERT INTO quotes (instrument_id, price, previous_close, as_of, source) VALUES (?, '95', '101', "
                 "'2026-09-30T15:00:00Z', 'finnhub')", (iid,))
    alerts.check(conn, NOW)
    assert sent[0][1] == "AAA -5,9 %"
    assert notice(conn, "move:AAA:2026-09-30") is not None


def test_insider_buys_code_p_only(conn, follow, sent):
    iid = follow("NVDA")
    doc = eight_k(conn, iid, "f4", form="4")
    conn.execute("INSERT INTO insider_trades (instrument_id, document_id, insider_name, insider_role, trade_date, kind, code, qty, price) "
                 "VALUES (?, ?, 'Jensen Huang', 'CEO', '2026-09-28', 'buy', 'P', '1000', '120.50')", (iid, doc))
    doc2 = eight_k(conn, iid, "f4b", form="4")
    conn.execute("INSERT INTO insider_trades (instrument_id, document_id, insider_name, trade_date, kind, code, qty) "
                 "VALUES (?, ?, 'Kingitus', '2026-09-28', 'other', 'G', '50')", (iid, doc2))
    assert alerts.check(conn, NOW) == 1
    assert sent[0][1:] == ("NVDA: siseringi ost", "Jensen Huang (CEO) ostis 1 000 aktsiat umbes 120 500 $ eest (28.09).\n"
                                                  "https://www.sec.gov/Archives/f4")


def test_earnings_tomorrow(conn, follow, sent):
    iid = follow("NVDA", "NVIDIA")
    conn.execute("INSERT INTO earnings (instrument_id, date, time_of_day, source) VALUES (?, '2026-10-01', 'amc', 'finnhub')", (iid,))
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, '2026-10-05', 'finnhub')", (iid,))
    assert alerts.check(conn, NOW) == 1
    assert sent[0][1:] == ("NVDA: tulemused homme", "NVIDIA avaldab tulemused 01.10, pärast turu sulgemist.")


def test_attention_spike(conn, follow, sent):
    iid = follow("NVDA")
    for n in range(1, 31):
        day = DAY - timedelta(days=n)
        conn.execute("INSERT INTO doc_counts VALUES (?, ?, 'yahoo_rss', ?)", (iid, day.isoformat(), 2 + n % 2))
    conn.execute("INSERT INTO doc_counts VALUES (?, ?, 'yahoo_rss', 25)", (iid, DAY.isoformat()))
    assert alerts.check(conn, NOW) == 1
    assert sent[0][1] == "NVDA: tähelepanu hüpe"
    assert notice(conn, "attention:NVDA:2026-09-30") is not None


def test_new_deep_analysis(conn, follow, sent):
    iid = follow("NVDA")
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model, created_at) VALUES "
                 "(?, 'deep', '2026-09-30', 'Nõudlus kasvab edasi. Teine lause.', 'r@1', 'sonnet', ?)",
                 (iid, (NOW - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model, created_at) VALUES "
                 "(?, 'on_demand', '2026-09-30', 'Uuri kohe', 'r@1', 'sonnet', ?)",
                 (iid, (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    assert alerts.check(conn, NOW) == 1
    assert sent[0][1:] == ("NVDA: uus süvaanalüüs", "Nõudlus kasvab edasi.")


def test_the_app_link_needs_public_url(conn, follow, sent, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://markmik.example.ee/")
    iid = follow("BRK.B")
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, '2026-10-01', 'finnhub')", (iid,))
    alerts.check(conn, NOW)
    assert sent[0][2].endswith("\nhttps://markmik.example.ee/aktsiad/BRK.B")


def test_night_alert_once_per_night(conn, sent):
    steps = {"prices": {"status": "failed", "detail": {"error": "x"}},
             "extract": {"status": "partial", "detail": {"stopped": "Kasutuspiir on täis"}},
             "verdict": {"status": "skipped", "detail": {"reason": "Kasutuspiir on täis", "stopped": "Kasutuspiir on täis"}}}
    assert alerts.night_alert(conn, DAY, "partial", steps, NOW) is True
    assert alerts.night_alert(conn, DAY, "failed", steps, NOW) is False
    assert sent[0][1:] == ("Aktsiate öö 30.09: osaliselt", "Ebaõnnestus: hinnad.\nPeatus: Kasutuspiir on täis.")


# --- The digest -----------------------------------------------------------------------------------

@pytest.fixture
def night_data(conn, follow, hold, bars):
    a, b = follow("AAA", "Aaa AS"), follow("BBB", "Bbb Corp")
    hold(a)
    bars(a, [("2026-09-28", "100"), ("2026-09-29", "102")])
    steps = {"prices": {"status": "succeeded", "detail": {}},
             "research": {"status": "succeeded", "detail": {"done": ["AAA"], "skipped": [], "failed": [], "stopped": None}},
             "verdict": {"status": "partial", "detail": {"done": ["AAA"], "skipped": [],
                                                         "failed": [{"symbol": "BBB", "error": "vastus polnud JSON"}], "stopped": None}}}
    conn.execute("INSERT INTO stock_runs (run_date, started_at, finished_at, status, steps_json) VALUES "
                 "('2026-09-30', '2026-09-29T22:30:00Z', '2026-09-30T02:00:00Z', 'partial', ?)", (json.dumps(steps),))
    conn.execute("INSERT INTO analyses (instrument_id, kind, as_of, summary, prompt_version, model) VALUES "
                 "(?, 'deep', '2026-09-30', 'Kasv jätkub.', 'r@1', 'sonnet')", (a,))
    for iid, as_of, v in ((a, "2026-09-29", "hold"), (a, "2026-09-30", "buy"), (b, "2026-09-30", "sell")):
        conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                     "VALUES (?, ?, ?, 'medium', 'x', 'v@1', 'sonnet')", (iid, as_of, v))
    conn.execute("INSERT INTO earnings (instrument_id, date, time_of_day, source) VALUES (?, '2026-10-02', 'bmo', 'finnhub')", (b,))
    conn.execute("INSERT INTO earnings (instrument_id, date, source) VALUES (?, '2026-10-20', 'finnhub')", (b,))
    conn.execute("INSERT INTO stock_source_runs (source, started_at, status, error) VALUES "
                 "('finnhub', '2026-09-29T23:00:00Z', 'failed', 'HTTP 503')")
    conn.execute("INSERT INTO stock_source_runs (source, started_at, status, error) VALUES "
                 "('finnhub', '2026-09-28T23:00:00Z', 'failed', 'eilne')")  # the night before: not this digest's
    for status, ms in (("success", 30000), ("success", 60000), ("failed", 30000)):
        conn.execute("INSERT INTO stock_ai_runs (purpose, model, prompt_version, started_at, duration_ms, status) VALUES "
                     "('extract', 'haiku', 'e@1', '2026-09-30T00:10:00Z', ?, ?)", (ms, status))


def test_digest_content(conn, night_data):
    title, body = alerts.build_digest(conn, DAY)
    assert title == "Aktsiad 30.09"
    assert body == (
        "Öö\n• Osaliselt (süvauuringu öö)\n\n"
        "Portfell\n• AAA +2,0 %, 102,00 USD (29.09)\n\n"
        "Uued analüüsid\n• AAA: Kasv jätkub.\n\n"
        "Hinnang muutus\n• AAA: Hoiaks ◆ → Ostaks ▲\n\n"
        "Tulemused\n• 02.10 BBB (enne turu avanemist)\n\n"
        "Vahele jäi või ebaõnnestus\n• hinnangud, BBB: vastus polnud JSON\n• allikas finnhub ebaõnnestus: HTTP 503\n\n"
        "AI kasutus\n• extract: 3 päringut, 1 ebaõnnestus, 2,0 min")


def test_the_digest_is_stored_by_the_night_and_sent_once_at_seven(conn, night_data, sent):
    alerts.store_digest(conn, DAY)
    assert notice(conn, "digest:2026-09-30")["sent_at"] is None
    assert alerts.send_digest(conn, DAY) is True
    assert alerts.send_digest(conn, DAY) is False
    titles = [s[1] for s in sent]
    assert titles == ["Aktsiate öö 30.09: osaliselt", "Aktsiad 30.09"]
    # A sent digest is not rebuilt by a later rerun of the night.
    before = notice(conn, "digest:2026-09-30")["body"]
    conn.execute("DELETE FROM analyses")
    alerts.store_digest(conn, DAY)
    assert notice(conn, "digest:2026-09-30")["body"] == before


def test_a_night_that_never_ran_is_told_at_seven(conn, follow, sent):
    follow("AAA")
    assert alerts.send_digest(conn, DAY) is True
    assert [s[1:] for s in sent][0] == ("Aktsiate öö 30.09: puudub", "Ööd pole kirjas: töötaja ei käinud.")
    assert sent[1][2] == "Öö\n• Ööd pole kirjas."


def test_no_digest_without_stocks(conn, sent):
    assert alerts.send_digest(conn, DAY) is False
    assert sent == []


def test_a_digest_that_failed_at_seven_goes_with_the_next_check(conn, follow, sent, monkeypatch):
    follow("AAA")

    def down(cfg, title, message):
        raise RuntimeError("ntfy vastas 502")
    monkeypatch.setattr(reminders, "publish", down)
    assert alerts.send_digest(conn, DAY) is False
    assert notice(conn, "digest:2026-09-30")["error"] == "ntfy vastas 502"
    monkeypatch.setattr(reminders, "publish", lambda cfg, title, message: sent.append((cfg, title, message)))
    alerts.check(conn, local(DAY, 16, 0))
    assert [s[1] for s in sent] == ["Aktsiad 30.09"]
    alerts.check(conn, local(DAY, 16, 5))
    assert len(sent) == 1
