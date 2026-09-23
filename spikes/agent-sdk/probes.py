"""Spike S2 probes. Each subcommand makes the model calls noted beside it.

    docker run --rm -v <repo .env>:/run/secrets/markmik.env:ro markmik-s2 python probes.py <cmd>

Every line printed goes through a filter that masks the OAuth token.
"""

import asyncio
import json
import os
import sys
import tempfile
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ClaudeSDKError,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from runner_sketch import _options, _token

TOKEN = _token()
_print = print


def print(*args):  # noqa: A001 - mask the token in everything this script prints
    _print(" ".join(str(a) for a in args).replace(TOKEN, "<redacted>"))


def cut(s, n=300):
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + f"...(+{len(s) - n})"


def dump(msg, t0):
    t = f"{time.monotonic() - t0:6.2f}s"
    if isinstance(msg, SystemMessage):
        d = msg.data
        if msg.subtype == "init":
            keep = {k: d.get(k) for k in ("model", "tools", "mcp_servers", "permissionMode",
                                          "apiKeySource", "claude_code_version", "cwd",
                                          "agents", "skills", "plugins", "output_style")}
            keep["slash_commands"] = len(d.get("slash_commands") or [])
            print(t, "SystemMessage init", cut(keep, 900))
            print(t, "  init keys:", sorted(d))
        else:
            print(t, "SystemMessage", msg.subtype, cut(d, 200))
    elif isinstance(msg, AssistantMessage):
        for b in msg.content:
            if isinstance(b, TextBlock):
                print(t, "Assistant text:", cut(b.text))
            elif isinstance(b, ToolUseBlock):
                print(t, "Assistant tool_use:", b.name, cut(b.input))
            elif isinstance(b, ThinkingBlock):
                print(t, "Assistant thinking:", cut(b.thinking, 80))
            else:
                print(t, "Assistant", type(b).__name__, cut(getattr(b, "__dict__", str(b)), 200))
        if msg.error:
            print(t, "Assistant error:", msg.error)
    elif isinstance(msg, UserMessage):
        for b in msg.content if isinstance(msg.content, list) else [msg.content]:
            if isinstance(b, ToolResultBlock):
                print(t, "User tool_result:", "is_error" if b.is_error else "", cut(b.content, 200))
            else:
                print(t, "User", cut(str(b), 200))
    elif isinstance(msg, ResultMessage):
        print(t, "ResultMessage", cut({
            "subtype": msg.subtype, "is_error": msg.is_error, "num_turns": msg.num_turns,
            "duration_ms": msg.duration_ms, "duration_api_ms": msg.duration_api_ms,
            "stop_reason": msg.stop_reason, "terminal_reason": msg.terminal_reason,
            "result": msg.result, "structured_output": msg.structured_output,
            "permission_denials": msg.permission_denials, "errors": msg.errors,
            "total_cost_usd": msg.total_cost_usd, "session_id": msg.session_id,
            "models": list((msg.model_usage or {}).keys()),
        }, 1500))
    elif isinstance(msg, StreamEvent):
        pass  # summarised by the caller
    elif isinstance(msg, RateLimitEvent):
        print(t, "RateLimitEvent", cut(msg.rate_limit_info.__dict__, 200))
    else:
        print(t, type(msg).__name__, cut(getattr(msg, "__dict__", str(msg)), 200))


async def run(prompt, opts, label):
    print(f"\n=== {label}")
    t0 = time.monotonic()
    out = []
    try:
        async for msg in query(prompt=prompt, options=opts):
            dump(msg, t0)
            out.append(msg)
    except ClaudeSDKError as e:  # query() raises ResultError after an is_error result
        print("raised:", type(e).__name__, cut(str(e), 200))
    return out


def tmp():
    return tempfile.mkdtemp(prefix="s2-")


# --- 2. tool surface (3 calls) -------------------------------------------------

ATTACK = ("Do these in order and report each result verbatim: 1) run the shell command `id` "
          "with Bash; 2) Read the file /etc/hostname; 3) Read ./note.txt; 4) Write 'x' to /tmp/pwn; "
          "5) WebFetch https://example.com. If a tool is unavailable, say which tools you do have.")


async def p_tools():
    print("CLAUDE_CODE_OAUTH_TOKEN in os.environ:", "CLAUDE_CODE_OAUTH_TOKEN" in os.environ)
    d = tmp()
    open(f"{d}/note.txt", "w").write("secret-in-cwd\n")
    await run(ATTACK, _options("haiku", d, 4, tools=[]), "A tools=[] (no allow-list)")
    await run(ATTACK + " 6) WebSearch for 'Tallinna koolivaheaeg 2026' and give one date.",
              _options("haiku", d, 6, tools=["WebSearch"], allowed_tools=["WebSearch"]),
              "B tools=['WebSearch'] allowed=['WebSearch']")
    await run(ATTACK, _options("haiku", d, 6, allowed_tools=["WebSearch"]),
              "C tools=default preset, allowed=['WebSearch'], dontAsk")
    print("/tmp/pwn exists:", os.path.exists("/tmp/pwn"))


# --- 3. structured output (2 calls) --------------------------------------------

EVENT_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["exam", "homework", "event", "meeting", "holiday", "other"]},
            "title": {"type": "string"},
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": ["string", "null"], "format": "date-time"},
            "all_day": {"type": "boolean"},
            "date": {"type": "string", "format": "date"},
            "date_text": {"type": "string"},
            "source_quote": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["kind", "title", "start", "end", "all_day", "date", "date_text",
                     "source_quote", "confidence"],
        "additionalProperties": False,
    }}},
    "required": ["events"],
    "additionalProperties": False,
}

EMAIL = ("Kirja kuupäev: neljapäev, 24.09.2026. Ajavöönd Europe/Tallinn.\n"
         "Saatja: Mari Mets <mari.mets@kool.ee>\nTeema: Matemaatika kontrolltöö\n\n"
         "Tere! Kontrolltöö toimub reedel 3. oktoobril kell 14.15 ruumis 204. "
         "Võtke kaasa kalkulaator.\nMari Mets")


async def p_structured():
    ask = "Extract calendar events from this school email.\n\n" + EMAIL
    await run(ask, _options("haiku", tmp(), 3, tools=[],
                            output_format={"type": "json_schema", "schema": EVENT_SCHEMA}),
              "S1 json_schema extraction")
    # Does the CLI enforce "format"? Ask for a non-ISO form and see what comes back.
    ask2 = ("Extract calendar events. IMPORTANT: write `start` and `date` in Estonian style, "
            "e.g. '03.10.2026 kell 14.15' and '03.10.2026', never ISO.\n\n" + EMAIL)
    await run(ask2, _options("haiku", tmp(), 4, tools=[],
                             output_format={"type": "json_schema", "schema": EVENT_SCHEMA}),
              "S2 format enforcement check")


async def p_structured_fast():
    # Same as S1 with thinking off: the CLI turns extended thinking on by default.
    ask = "Extract calendar events from this school email.\n\n" + EMAIL
    await run(ask, _options("haiku", tmp(), 3, tools=[], thinking={"type": "disabled"},
                            output_format={"type": "json_schema", "schema": EVENT_SCHEMA}),
              "S3 json_schema extraction, thinking disabled")


# --- 4+5. in-process tool, allow-list, partial streaming (1 call) --------------

@tool("search_notes", "Search the user's notes. Returns matching note titles and snippets.",
      {"query": str})
async def search_notes(args):
    print("   [handler] search_notes called with", args)
    return {"content": [{"type": "text", "text": "- Matemaatika (2.10): kontrolltöö ruumis 204"}]}


@tool("delete_note", "Delete a note by slug.", {"slug": str})
async def delete_note(args):
    print("   [handler] delete_note CALLED - allow-list failed!", args)
    return {"content": [{"type": "text", "text": "deleted"}]}


async def p_mcp():
    server = create_sdk_mcp_server("notes", tools=[search_notes, delete_note])
    opts = _options("haiku", tmp(), 5, tools=[], mcp_servers={"notes": server},
                    allowed_tools=["mcp__notes__search_notes"], include_partial_messages=True)
    print("\n=== M in-process MCP + partial messages")
    t0 = time.monotonic()
    seq, text = [], []
    async for msg in query(prompt="Search my notes for 'kontrolltöö', then delete the note "
                                  "'matemaatika'. Answer in one Estonian sentence.", options=opts):
        if isinstance(msg, StreamEvent):
            ev = msg.event
            name = ev["type"]
            if ev["type"] in ("content_block_start",):
                name += ":" + ev["content_block"]["type"]
            if ev["type"] == "content_block_delta":
                name += ":" + ev["delta"]["type"]
                if ev["delta"]["type"] == "text_delta":
                    text.append(ev["delta"]["text"])
            if not seq or seq[-1][0] != name:
                seq.append([name, 1])
            else:
                seq[-1][1] += 1
            if len(seq) == 1 and seq[0][1] == 1:
                print(f"{time.monotonic() - t0:6.2f}s first StreamEvent keys:",
                      sorted(msg.__dict__), "event:", cut(ev, 200))
        else:
            dump(msg, t0)
    print("stream event sequence:", " ".join(f"{n}x{c}" if c > 1 else n for n, c in seq))
    print("joined text_delta:", cut("".join(text)))


# --- 6. token only via options.env (0 model calls expected) -------------------

async def p_env():
    print("CLAUDE_CODE_OAUTH_TOKEN in os.environ:", "CLAUDE_CODE_OAUTH_TOKEN" in os.environ)
    opts = _options("haiku", tmp(), 1, tools=[])
    opts.env = {}  # negative control: no token anywhere
    await run("Say ok.", opts, "E no token")


# --- 7. latency (3 calls) ------------------------------------------------------

async def timed(model, prompt):
    opts = _options(model, tmp(), 1, tools=[], include_partial_messages=True)
    t0 = time.monotonic()
    t_init = t_first = None
    async for msg in query(prompt=prompt, options=opts):
        now = time.monotonic() - t0
        if isinstance(msg, SystemMessage) and msg.subtype == "init" and t_init is None:
            t_init = now
        if (isinstance(msg, StreamEvent) and msg.event["type"] == "content_block_delta"
                and t_first is None):
            t_first = now
        if isinstance(msg, ResultMessage):
            print(f"{model:6} init {t_init:.2f}s first-token {t_first:.2f}s total {now:.2f}s "
                  f"api {msg.duration_api_ms}ms cli {msg.duration_ms}ms -> {cut(msg.result, 60)!r}")


async def p_latency():
    q = "Mis on Eesti pealinn? Vasta ühe sõnaga."
    await timed("haiku", q)
    await timed("haiku", q)
    await timed("sonnet", q)


# --- 8. multi-turn (3-4 calls) -------------------------------------------------

async def p_chat():
    d = tmp()
    opts = _options("haiku", d, 2, tools=[])
    print("\n=== C1 ClaudeSDKClient, two turns in one process")
    sid = None
    async with ClaudeSDKClient(options=opts) as client:
        for prompt in ("Mu lemmikvärv on roheline. Vasta ainult: selge.",
                       "Mis on mu lemmikvärv? Vasta ühe sõnaga."):
            t0 = time.monotonic()
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, ResultMessage):
                    sid = msg.session_id
                    print(f"{time.monotonic() - t0:.2f}s", repr(msg.result), "session", sid)
    print("transcripts:", [os.path.relpath(os.path.join(r, f), os.environ["CLAUDE_CONFIG_DIR"])
                           for r, _, fs in os.walk(os.environ["CLAUDE_CONFIG_DIR"])
                           for f in fs if f.endswith(".jsonl")])

    await run("Mis värvi ma mainisin? Vasta ühe sõnaga.",
              _options("haiku", d, 2, tools=[], resume=sid), "C2 resume=session_id, same cwd")
    await run("Mis värvi ma mainisin? Vasta ühe sõnaga.",
              _options("haiku", tmp(), 2, tools=[], resume=sid), "C3 resume, different cwd")


if __name__ == "__main__":
    asyncio.run(globals()["p_" + sys.argv[1]]())
