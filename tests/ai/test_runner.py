"""app/ai/runner.py: the options every real call gets, error mapping, the async bridge, and the
fake mode other packages rely on."""

import os
import re
import threading

import pytest
from mcp import Client
from claude_agent_sdk import (AssistantMessage, CLINotFoundError, ProcessError, RateLimitEvent, RateLimitInfo,
                              ResultError, ResultMessage, SdkMcpTool, StreamEvent, ToolResultBlock, ToolUseBlock,
                              UserMessage, tool)

from app.ai import runner

SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}, "room": {"type": "string"}},
          "required": ["subject", "room"]}


def result(structured=None, *, is_error=False, subtype="success", text="valmis", status=None):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error, num_turns=1,
                         session_id="s-1", result=text, structured_output=structured, api_error_status=status)


def delta(text):
    return StreamEvent(uuid="u", session_id="s-1",
                       event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})


def tool_use(name, args, id_="t1"):
    return AssistantMessage(content=[ToolUseBlock(id=id_, name=name, input=args)], model="sonnet")


def tool_result(text, id_="t1"):
    return UserMessage(content=[ToolResultBlock(tool_use_id=id_, content=[{"type": "text", "text": text}])])


@tool("otsi", "test tool", {"query": str})
async def otsi(args):
    return {"content": [{"type": "text", "text": "leitud"}]}


def calls_today(conn):
    row = conn.execute("SELECT calls FROM ai_usage").fetchone()
    return row["calls"] if row else 0


# --- Options ---------------------------------------------------------------------------------------

def test_structured_options(conn, real_mode, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "app-secret")
    monkeypatch.setenv("OWNER_PASSWORD", "owner-pw")
    monkeypatch.setenv("OWNER_PASSWORD_HASH", "scrypt:32768:8:1$sool$00")
    real_mode.reply(result({"subject": "matemaatika", "room": "204"}))
    out = runner.run_structured(conn, "Kontrolltöö ruumis 204.", SCHEMA, system="Eralda.")
    assert out == {"subject": "matemaatika", "room": "204"}
    opts = real_mode.options
    assert opts.tools == []  # no built-in tool at all
    assert not opts.allowed_tools and not opts.mcp_servers
    assert opts.thinking == {"type": "disabled"}
    assert opts.output_format == {"type": "json_schema", "schema": SCHEMA}
    assert opts.setting_sources == [] and opts.permission_mode == "dontAsk"
    assert opts.model == "haiku" and opts.max_turns == runner.STRUCTURED_MAX_TURNS
    assert opts.system_prompt == "Eralda."
    assert real_mode.calls[-1]["cwd_files"] == []  # an empty working directory
    env = opts.env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-test-token"
    for name in ("SECRET_KEY", "MARKMIK_KEY", "OWNER_PASSWORD", "OWNER_PASSWORD_HASH", "ANTHROPIC_API_KEY"):
        assert env[name] == ""
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert opts.strict_mcp_config is True
    assert calls_today(conn) == 1


def test_config_dir_is_private_and_transcripts_go(conn, real_mode, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "developer-claude"))
    written = []

    def cli_writes_transcript(options):
        # What the CLI does on every call: projects/<working directory, sanitised>/<session>.jsonl.
        folder = os.path.join(options.env["CLAUDE_CONFIG_DIR"], "projects", re.sub(r"[^A-Za-z0-9]", "-", options.cwd))
        os.makedirs(folder)
        open(os.path.join(folder, "s-1.jsonl"), "w").close()
        written.append(folder)
    real_mode.before = cli_writes_transcript
    for _ in range(2):
        real_mode.reply(result({"subject": "a", "room": "b"}))
        runner.run_structured(conn, "x", SCHEMA)
    config = {c["options"].env["CLAUDE_CONFIG_DIR"] for c in real_mode.calls}
    assert len(config) == 1  # one per process, never the inherited one
    config = config.pop()
    assert config != str(tmp_path / "developer-claude") and os.path.basename(config).startswith("markmik-claude-")
    assert len(written) == 2 and not any(os.path.exists(f) for f in written)
    assert os.listdir(os.path.join(config, "projects")) == []


def test_tools_run_through_the_mcp_server_on_the_bridge_thread(app, conn, real_mode, monkeypatch):
    """The SDK serves our tools from options.mcp_servers inside its own event loop and thread; the
    handlers open their own connections there while the request thread waits."""
    from app import db, notes
    from app.ai import chat
    note = notes.create(conn, "Kool")
    threads = []
    real_connect = db.connect
    monkeypatch.setattr(db, "connect", lambda path: threads.append(threading.current_thread().name) or real_connect(path))
    args = {"title": "Kontrolltöö", "date": "2026-10-02", "time": "14:15"}

    async def use_tool(options):
        async with Client(options.mcp_servers["markmik"]["instance"]) as client:
            out = await client.call_tool("loo_sundmus", args)
        real_mode.tool_text = out.content[0].text
        return tool_use("mcp__markmik__loo_sundmus", args)

    async def tool_done(options):
        return tool_result(real_mode.tool_text)
    real_mode.reply(use_tool, tool_done, delta("Lisasin."), result())
    events = list(chat.turn(conn, note, "Lisa kontrolltöö"))
    assert [e["type"] for e in events] == ["tool", "event_created", "delta", "done"]
    row = conn.execute("SELECT * FROM events").fetchone()
    assert (row["title"], row["source"], row["note_id"]) == ("Kontrolltöö", "ai", note["id"])
    assert events[1]["id"] == row["id"]
    assert threads == ["markmik-ai"]  # opened on the runner's thread, not the request's


def test_token_from_vault_wins(conn, real_mode):
    from app import vault
    vault.set(conn, "claude_token", "sk-vault-token")
    real_mode.reply(result({"subject": "a", "room": "b"}))
    runner.run_structured(conn, "x", SCHEMA)
    assert real_mode.options.env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-vault-token"


def test_chat_options_only_app_tools_and_websearch(conn, real_mode):
    real_mode.reply(delta("Tere"), result())
    list(runner.stream_chat(conn, "Tere", system="Süsteem", tools=[otsi, "WebSearch"]))
    opts = real_mode.options
    assert opts.tools == ["WebSearch"]
    assert opts.allowed_tools == ["WebSearch", "mcp__markmik__otsi"]
    assert list(opts.mcp_servers) == ["markmik"]
    assert opts.permission_mode == "dontAsk" and opts.setting_sources == []
    assert opts.include_partial_messages is True
    assert opts.model == "sonnet" and opts.system_prompt == "Süsteem"
    assert opts.env["SECRET_KEY"] == "" and opts.env["MARKMIK_KEY"] == ""


def test_chat_without_tools_has_none(conn, real_mode):
    real_mode.reply(result())
    list(runner.stream_chat(conn, "Tere"))
    assert real_mode.options.tools == [] and real_mode.options.allowed_tools == []
    assert real_mode.options.mcp_servers == {}


@pytest.mark.parametrize("bad", ["Bash", "WebFetch", "Read", object()])
def test_chat_refuses_other_builtins(conn, real_mode, bad):
    with pytest.raises(ValueError):
        list(runner.stream_chat(conn, "x", tools=[otsi, bad]))
    assert real_mode.calls == []


# --- Streaming -------------------------------------------------------------------------------------

def test_stream_maps_sdk_messages(conn, real_mode):
    real_mode.reply(
        delta("Otsin. "),
        tool_use("mcp__markmik__otsi", {"query": "töö"}),
        tool_result("leitud"),
        tool_use("WebSearch", {"query": "ilm"}, id_="t2"),
        tool_result("päike", id_="t2"),
        delta("Leidsin."),
        result(text="Otsin. Leidsin."),
        error=ResultError("after result", {"subtype": "success"}),  # never seen: the result came first
    )
    events = list(runner.stream_chat(conn, "Otsi", tools=[otsi, "WebSearch"]))
    assert events == [
        {"type": "delta", "text": "Otsin. "},
        {"type": "tool", "name": "otsi", "input": {"query": "töö"}, "result": "leitud"},
        {"type": "tool", "name": "WebSearch", "input": {"query": "ilm"}, "result": "päike"},
        {"type": "delta", "text": "Leidsin."},
        {"type": "done", "session_id": "s-1"},
    ]


def test_stream_error_result_is_an_error_event(conn, real_mode):
    real_mode.reply(result(is_error=True, subtype="error_max_turns", text=""),
                    error=ResultError("x", {"subtype": "error_max_turns"}))
    events = list(runner.stream_chat(conn, "x"))
    assert events[-1]["type"] == "error" and "error_max_turns" in events[-1]["message"]
    assert len(events) == 1


def test_stream_process_crash_is_an_error_event(conn, real_mode):
    real_mode.reply(delta("pool"), error=ProcessError("boom", exit_code=1))
    events = list(runner.stream_chat(conn, "x"))
    assert events[0] == {"type": "delta", "text": "pool"}
    assert events[1]["type"] == "error"


# --- Errors that mean "AI unavailable" ---------------------------------------------------------------

def test_no_token(conn, real_mode, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    with pytest.raises(runner.AIUnavailable, match="võti puudub"):
        runner.run_structured(conn, "x", SCHEMA)
    with pytest.raises(runner.AIUnavailable):
        list(runner.stream_chat(conn, "x"))
    assert real_mode.calls == []
    assert calls_today(conn) == 0  # nothing was called, nothing counted


def test_cli_missing(conn, real_mode):
    real_mode.reply(error=CLINotFoundError("Claude Code not found"))
    with pytest.raises(runner.AIUnavailable, match="Claude Code"):
        runner.run_structured(conn, "x", SCHEMA)
    with pytest.raises(runner.AIUnavailable):
        list(runner.stream_chat(conn, "x"))


@pytest.mark.parametrize("status, words", [(401, "võti"), (429, "kasutuspiir"), (529, "kättesaadav")])
def test_api_errors(conn, real_mode, status, words):
    real_mode.reply(result(is_error=True, text=f"API Error: {status}", status=status),
                    error=ResultError("x", {"subtype": "success"}))
    with pytest.raises(runner.AIUnavailable, match=words):
        runner.run_structured(conn, "x", SCHEMA)
    with pytest.raises(runner.AIUnavailable, match=words):
        list(runner.stream_chat(conn, "x"))


def test_auth_failure_seen_on_the_assistant_message(conn, real_mode):
    real_mode.reply(AssistantMessage(content=[], model="haiku", error="authentication_failed"),
                    result(is_error=True, text="Not logged in"), error=ResultError("x", {}))
    with pytest.raises(runner.AIUnavailable, match="võti"):
        runner.run_structured(conn, "x", SCHEMA)


def test_rate_limit_rejected_event(conn, real_mode):
    real_mode.reply(RateLimitEvent(rate_limit_info=RateLimitInfo(status="rejected"), uuid="u", session_id="s"),
                    result(is_error=True, text="limit"))
    with pytest.raises(runner.AIUnavailable, match="kasutuspiir"):
        list(runner.stream_chat(conn, "x"))


def test_structured_failures_are_runtime_errors(conn, real_mode):
    real_mode.reply(result(is_error=True, subtype="error_max_turns"))
    with pytest.raises(RuntimeError) as err:
        runner.run_structured(conn, "x", SCHEMA)
    assert not isinstance(err.value, runner.AIUnavailable)
    real_mode.reply(result(None))
    with pytest.raises(RuntimeError, match="skeemile"):
        runner.run_structured(conn, "x", SCHEMA)
    real_mode.reply(result({"subject": "matemaatika"}))  # "room" is required
    with pytest.raises(RuntimeError, match="room"):
        runner.run_structured(conn, "x", SCHEMA)


def test_structured_validates_nested_required_keys():
    schema = {"type": "object", "required": ["events"], "properties": {
        "events": {"type": "array", "items": {"type": "object", "required": ["title", "start"],
                                              "properties": {"title": {"type": "string"}}}}}}
    assert runner._missing({"events": [{"title": "a", "start": "b"}]}, schema) is None
    assert runner._missing({"events": [{"title": "a"}]}, schema) == "/events/0/start"
    assert runner._missing({"events": "x"}, schema) == "/events"
    assert runner._missing([], schema) == "/"


def test_bridge_timeout_cancels_the_call(conn, real_mode, monkeypatch):
    monkeypatch.setattr(runner, "STRUCTURED_TIMEOUT", 0.3)
    real_mode.reply(result({"subject": "a", "room": "b"}), delay=30)
    with pytest.raises(runner.AIUnavailable, match="ei vastanud"):
        runner.run_structured(conn, "x", SCHEMA)
    import time
    for _ in range(50):
        if real_mode.closed:
            break
        time.sleep(0.05)
    assert real_mode.closed  # the SDK generator (and with it the CLI process) was stopped


def test_bridge_stops_the_call_when_the_reader_leaves(conn, real_mode):
    real_mode.reply(delta("a"), delta("b"), delta("c"), result(), delay=0.2)
    stream = runner.stream_chat(conn, "x")
    assert next(stream) == {"type": "delta", "text": "a"}
    stream.close()
    import time
    for _ in range(50):
        if real_mode.closed:
            break
        time.sleep(0.05)
    assert real_mode.closed


def test_call_tool_runs_a_handler():
    assert isinstance(otsi, SdkMcpTool)
    assert runner.call_tool(otsi, {"query": "x"}) == "leitud"


# --- Fake mode (AI_FAKE=1, as every other test runs) --------------------------------------------------

def test_fake_mode_unchanged(conn, monkeypatch):
    def boom(**_):
        raise AssertionError("the SDK must not be called in fake mode")
    monkeypatch.setattr(runner, "query", boom)
    events = list(runner.stream_chat(conn, "Tere, kes sa oled?"))
    assert "".join(e["text"] for e in events if e["type"] == "delta") == runner.FAKE_REPLY + "Tere, kes sa oled?"
    assert events[-1] == {"type": "done", "session_id": "fake-session"}
    assert runner.run_structured(conn, "x", SCHEMA) == {"subject": None, "room": None}
    monkeypatch.setattr(runner, "fake_structured", lambda p, s, m: {"subject": m, "room": p})
    assert runner.run_structured(conn, "x", SCHEMA) == {"subject": "haiku", "room": "x"}
    assert calls_today(conn) == 3
    assert runner.test_connection(conn)["ok"] is True


def test_daily_cap(conn, monkeypatch):
    monkeypatch.setattr(runner, "DAILY_CAP", 1)
    runner.run_structured(conn, "x", SCHEMA)
    with pytest.raises(runner.AIUnavailable, match="piir"):
        runner.run_structured(conn, "x", SCHEMA)
