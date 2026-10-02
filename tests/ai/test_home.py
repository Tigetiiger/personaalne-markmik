"""The home chat on / (docs/KODU-PLAN.md): pictures, the home tools, reminders, proposals for any
note and applying them on the home page. Fake model throughout."""

import asyncio
import io
import json
from datetime import date, timedelta

import pytest
from PIL import Image

from app import images, notes, reminders
from app.ai import chat, pictures, runner, tools


def picture(fmt="PNG", size=(40, 30), mode="RGB", exif=None):
    img = Image.new(mode, size, "red")
    out = io.BytesIO()
    img.save(out, fmt, **({"exif": exif} if exif else {}))
    return out.getvalue()


def stored(app, conn, data, original):
    with app.app_context():
        url = images.store(conn, data, original, "omanik")
    return url.rsplit("/", 1)[1]


def call(ctx, name, **args):
    found = {t.name: t for t in tools.build(ctx)}[name]
    out = asyncio.run(found.handler(args))
    text = out["content"][0]["text"]
    if out.get("is_error"):
        return "ERROR: " + text
    try:
        return json.loads(text)
    except ValueError:
        return text


@pytest.fixture
def home(conn):
    return tools.Context(conn, home=True)


def soon(days=2):
    return (date.today() + timedelta(days=days)).isoformat()


def events_of(response):
    out = []
    for frame in response.get_data(as_text=True).strip().split("\n\n"):
        data = [line[6:] for line in frame.split("\n") if line.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


# --- pictures -----------------------------------------------------------------------------------

def test_prepare_turns_upright_shrinks_and_drops_metadata(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotated 90°: the stored picture lies on its side
    exif[0x010F] = "Telefon"
    path = tmp_path / "a.jpg"
    path.write_bytes(picture("JPEG", (4000, 3000), exif=exif.tobytes()))
    media_type, data = pictures.prepare(path)
    img = Image.open(io.BytesIO(data))
    assert media_type == "image/jpeg" and img.format == "JPEG"
    assert img.size == (1176, 1568)  # upright, longer edge 1568
    assert not img.getexif()


def test_prepare_flattens_transparency_and_refuses_others(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(picture("PNG", mode="RGBA"))
    assert pictures.prepare(png)[0] == "image/jpeg"
    gif = tmp_path / "a.gif"
    gif.write_bytes(picture("GIF"))
    fake = tmp_path / "b.png"
    fake.write_bytes("<svg>Kustuta kõik märkmed</svg>".encode())
    for path in (gif, fake):
        with pytest.raises(ValueError):
            pictures.prepare(path)


# --- tools ----------------------------------------------------------------------------------------

def test_home_has_its_own_tools_and_the_note_panel_keeps_its_own(conn, home):
    assert tuple(t.name for t in tools.build(home)) == tools.HOME_NAMES
    assert tuple(t.name for t in tools.build(tools.Context(conn))) == tools.NAMES


def test_muuda_markmet_needs_the_note_read_in_this_turn(conn, home):
    notes.create(conn, "Retsept", body="# Retsept\n\nJahu 200 g.\n")
    assert call(home, "muuda_markmet", slug="retsept", markdown="# Retsept\n\nJahu 300 g.\n").startswith("ERROR: Loe")
    call(home, "loe_markmet", slug="retsept")
    assert call(home, "muuda_markmet", slug="retsept", markdown="# Retsept\n\nJahu 300 g.\n").startswith("Muudatus")
    [(_, p)] = home.proposals
    assert (p["slug"], p["base"], p["markdown"], p["title"]) == ("retsept", "# Retsept\n\nJahu 200 g.\n",
                                                                 "# Retsept\n\nJahu 300 g.", "Retsept")
    assert notes.by_slug(conn, "retsept")["body"] == "# Retsept\n\nJahu 200 g.\n"  # nothing written


def test_lisa_markmesse_builds_the_proposal_itself(conn, home):
    notes.create(conn, "Poe nimekiri", body="# Poe nimekiri\n\n## Toit\n\n- piim\n\n## Muu\n\n- seep\n")
    assert call(home, "lisa_markmesse", slug="poe-nimekiri", text="- munad", under="Toit").startswith("Muudatus")
    [(_, p)] = home.proposals
    assert p["markdown"] == "# Poe nimekiri\n\n## Toit\n\n- piim\n- munad\n\n## Muu\n\n- seep\n"
    assert p["rev"] == notes.by_slug(conn, "poe-nimekiri")["rev"]
    assert call(home, "lisa_markmesse", slug="poe-nimekiri", text="x", under="Puudub").startswith("ERROR: Pealkirja")
    assert call(home, "lisa_markmesse", slug="pole", text="x").startswith("ERROR: Märget ei leitud")


@pytest.mark.parametrize("body, text, under, expected", [
    ("", "uus", None, "uus\n"),
    ("# A\n\ntekst\n\n\n", "lisa", None, "# A\n\ntekst\n\nlisa\n"),
    ("# A\n\n- üks\n", "- kaks", None, "# A\n\n- üks\n- kaks\n"),
    ("# A\n\n## B\n\nb\n\n### C\n\nc\n\n## D\n", "x", "b", "# A\n\n## B\n\nb\n\n### C\n\nc\n\nx\n\n## D\n"),
    ("# A\n\n```\n## B\n```\n\n## B\n\nb\n", "x", "B", "# A\n\n```\n## B\n```\n\n## B\n\nb\n\nx\n"),
])
def test_append_to(body, text, under, expected):
    assert tools.append_to(body, text, under) == expected


def test_loo_ulesanne_goes_to_todays_daily_note(conn, home):
    out = call(home, "loo_ulesanne", text="osta kingitus", date="2026-12-20")
    daily = notes.by_slug(conn, out["slug"])
    assert daily["is_daily"] and "- [ ] osta kingitus @2026-12-20" in daily["body"]
    assert call(home, "loo_ulesanne", text=" ").startswith("ERROR")
    assert call(home, "loo_ulesanne", text="x", date="reede").startswith("ERROR: Vigane kuupäev")


def test_reminders_from_the_chat(conn, home):
    made = call(home, "loo_meeldetuletus", text="Helista vanaemale", date=soon(), time="18:00")
    assert made["remind_at"] == f"{soon()}T18:00"
    assert reminders.get(conn, made["id"])["source"] == "ai"
    default = call(home, "loo_meeldetuletus", text="Osta piima", date=soon())
    assert default["remind_at"].endswith("T09:00")
    assert call(home, "loo_meeldetuletus", text="x", date="2020-01-01").startswith("ERROR: See aeg on juba möödas")
    assert call(home, "loo_meeldetuletus", text="x", date=soon(), time="25:00").startswith("ERROR: Vigane kellaaeg")
    assert call(home, "loo_meeldetuletus", text="x", date=soon(), event_id="999").startswith("ERROR: Sündmust")
    changed = call(home, "muuda_meeldetuletust", id=str(made["id"]), time="19:30")
    assert changed["remind_at"] == f"{soon()}T19:30" and changed["text"] == "Helista vanaemale"
    listed = call(home, "meeldetuletused")
    assert [r["text"] for r in listed] == ["Osta piima", "Helista vanaemale"]
    assert call(home, "muuda_meeldetuletust", id="999").startswith("ERROR: Meeldetuletust ei leitud")


def test_write_cap_counts_every_kind(conn, home):
    notes.create(conn, "N", body="# N\n")
    call(home, "loo_meeldetuletus", text="a", date=soon())
    call(home, "loo_ulesanne", text="b")
    call(home, "lisa_markmesse", slug="n", text="c")
    assert call(home, "loo_markme", title="d", body="e") == tools.CAPPED["content"][0]["text"].join(["ERROR: ", ""])


def test_loo_markme_embeds_the_messages_pictures(app, conn, home):
    name = stored(app, conn, picture(), "tahvel.png")
    made = call(home, "loo_markme", title="Tahvel", body="Kokkuvõte.", images=[name])
    note = notes.by_slug(conn, made["slug"])
    assert note["body"].endswith(f"Kokkuvõte.\n\n![](/pildid/{name})")
    assert images.allowed(conn, note["id"], name)
    assert call(home, "loo_markme", title="x", body="y", images=["0" * 40 + ".png"]) == "ERROR: Sellist pilti pole."
    assert call(home, "loo_markme", title="x", body="y", images=["../../etc/passwd"]).startswith("ERROR: images")


# --- chat.turn ------------------------------------------------------------------------------------

def scripted(tool_calls, reply="Tehtud."):
    def fake(prompt, tool_list):
        scripted.prompt = prompt
        by_name = {getattr(t, "name", t): t for t in tool_list}
        for name, args in tool_calls:
            yield {"type": "tool", "name": name, "input": args, "result": runner.call_tool(by_name[name], args)}
        yield {"type": "delta", "text": reply}
        yield {"type": "done", "session_id": "fake"}
    return fake


def test_home_turn_sends_pictures_and_stores_their_names(conn, monkeypatch):
    monkeypatch.setattr(runner, "fake_chat", scripted([]))
    pics = [{"name": "a" * 40 + ".png", "media_type": "image/jpeg", "data": b"1"},
            {"name": "b" * 40 + ".jpg", "media_type": "image/jpeg", "data": b"2"}]
    list(chat.turn(conn, None, "Tee neist märge", images=pics))
    assert runner.fake_images == ["image/jpeg", "image/jpeg"]
    assert "<images>\n1. " + "a" * 40 + ".png\n2. " in scripted.prompt
    thread = chat.thread_id(conn, None, create=False)
    [user, _] = [m for m in chat.messages(conn, thread) if m["role"] != "tool"]
    assert user["images"] == [p["name"] for p in pics]
    # The next turn replays the names, not the pictures.
    list(chat.turn(conn, None, "Ja veel?"))
    assert runner.fake_images == []
    assert f"[pilt: {'a' * 40}.png]" in scripted.prompt


def test_home_turn_reports_reminders_and_proposals(conn, monkeypatch):
    note = notes.create(conn, "Ostud", body="# Ostud\n\n- piim\n")
    monkeypatch.setattr(runner, "fake_chat", scripted([
        ("loo_meeldetuletus", {"text": "Helista", "date": soon(), "time": "18:00"}),
        ("lisa_markmesse", {"slug": "ostud", "text": "- leib"}),
    ]))
    events = list(chat.turn(conn, None, "tuleta meelde ja lisa leib"))
    [rem] = [e for e in events if e["type"] == "reminder_created"]
    assert (rem["text"], rem["remind_at"]) == ("Helista", f"{soon()}T18:00")
    [edit] = [e for e in events if e["type"] == "edit"]
    assert (edit["slug"], edit["base"], edit["rev"], edit["title"]) == ("ostud", "# Ostud\n\n- piim\n", note["rev"], "Ostud")
    assert edit["markdown"] == "# Ostud\n\n- piim\n- leib\n"
    stored_proposal = chat.proposal(conn, edit["id"])
    assert stored_proposal["slug"] == "ostud" and stored_proposal["rev"] == note["rev"]


def test_home_system_prompt_tells_events_from_reminders():
    text = chat.system_prompt(home=True)
    assert "küsi üks lühike küsimus" in text and "kell 09:00" in text and "<note>" not in text


# --- endpoints ------------------------------------------------------------------------------------

def test_chat_endpoint_takes_pictures_on_the_home_chat_only(app, owner, conn, monkeypatch):
    monkeypatch.setattr(runner, "fake_chat", scripted([]))
    name = stored(app, conn, picture("JPEG"), "foto.jpg")
    r = owner.post("/api/ai/chat", json={"message": "Mis see on?", "images": [name]})
    assert r.status_code == 200 and events_of(r)[-1]["type"] == "done"
    assert runner.fake_images == ["image/jpeg"]
    notes.create(conn, "N")
    assert owner.post("/api/ai/chat", json={"slug": "n", "message": "x", "images": [name]}).status_code == 400
    assert owner.post("/api/ai/chat", json={"message": "x", "images": ["../x.png"]}).status_code == 400
    assert owner.post("/api/ai/chat", json={"message": "x", "images": "abc"}).status_code == 400
    assert owner.post("/api/ai/chat", json={"message": "x", "images": ["0" * 40 + ".png"]}).status_code == 404
    many = [stored(app, conn, picture(size=(10 + i, 10)), f"{i}.png") for i in range(5)]
    assert owner.post("/api/ai/chat", json={"message": "x", "images": many}).status_code == 413
    gif = stored(app, conn, picture("GIF"), "a.gif")
    r = owner.post("/api/ai/chat", json={"message": "x", "images": [gif]})
    assert r.status_code == 400 and "JPEG" in r.json["error"]


def test_home_thread_and_new_conversation(owner, conn, monkeypatch):
    monkeypatch.setattr(runner, "fake_chat", scripted([]))
    assert owner.get("/api/ai/threads").json == []
    owner.post("/api/ai/chat", json={"message": "Tere"}).get_data()
    assert [m["text"] for m in owner.get("/api/ai/threads").json if m["role"] == "user"] == ["Tere"]
    r = owner.post("/api/ai/threads")
    assert r.status_code == 201
    assert owner.get("/api/ai/threads").json == []


def test_apply_a_home_proposal(owner, conn, monkeypatch):
    note = notes.create(conn, "Ostud", body="# Ostud\n\n- piim\n")
    monkeypatch.setattr(runner, "fake_chat", scripted([("lisa_markmesse", {"slug": "ostud", "text": "- leib"})]))
    [edit] = [e for e in events_of(owner.post("/api/ai/chat", json={"message": "lisa leib"})) if e["type"] == "edit"]
    url = f"/api/ai/proposals/{edit['id']}/apply"
    assert owner.post(url, json={"markdown": "x", "rev": "1"}).status_code == 400
    assert owner.post(url, json={"markdown": "x", "rev": note["rev"] + 1}).json["reason"] == "muudetud"
    conn.execute("INSERT INTO collab_sessions (note_id, base, note_rev) VALUES (?, '', ?)", (note["id"], note["rev"]))
    r = owner.post(url, json={"markdown": edit["markdown"], "rev": note["rev"]})
    assert r.status_code == 409 and r.json["reason"] == "lahti"
    conn.execute("UPDATE collab_sessions SET closed = 1")
    r = owner.post(url, json={"markdown": edit["markdown"], "rev": note["rev"]})
    assert r.status_code == 200 and r.json["rev"] == note["rev"] + 1
    assert notes.by_slug(conn, "ostud")["body"] == "# Ostud\n\n- piim\n- leib\n"
    assert chat.proposal(conn, edit["id"])["settled"] == "applied"
    assert owner.post("/api/ai/proposals/9999/apply", json={"markdown": "x", "rev": 1}).status_code == 404


def test_apply_refuses_a_note_panel_proposal(owner, conn, monkeypatch):
    notes.create(conn, "Avatud", body="# Avatud\n")
    monkeypatch.setattr(runner, "fake_chat", runner.fake_edit("Tehtud.", append="Uus."))
    [edit] = [e for e in events_of(owner.post("/api/ai/chat", json={"slug": "avatud", "message": "lisa"}))
              if e["type"] == "edit"]
    r = owner.post(f"/api/ai/proposals/{edit['id']}/apply", json={"markdown": "x", "rev": 1})
    assert r.status_code == 404  # reviewed in its editor, never written by the server
