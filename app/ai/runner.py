"""The one door to Claude. Everything that asks the model goes through run_structured() or
stream_chat(); security options live in one place, _options() (see docs/CONTRACTS.md, "AI runner",
and docs/spikes/agent-sdk.md).

AI_FAKE=1 (tests, local UI work): no model is called. run_structured() returns what
`fake_structured` returns; stream_chat() streams a canned answer, or what `fake_chat` yields.

Real mode runs the Claude Agent SDK, which is async and spawns one CLI process per call, on a
thread of its own; _bridge() hands its messages to the caller's (sync) iterator.
"""

import asyncio
import atexit
import html
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    RateLimitEvent,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
)

from .. import vault
from ..util import local_today

log = logging.getLogger("markmik.ai")


class AIUnavailable(RuntimeError):
    """No token, over the daily cap, or the model could not be reached."""


DAILY_CAP = int(os.environ.get("AI_DAILY_CAP", "300"))

MCP_SERVER = "markmik"  # app tools reach the model as mcp__markmik__<name>
MCP_PREFIX = f"mcp__{MCP_SERVER}__"
# The only built-in the model may have: WebFetch would be an exfiltration route, and file or shell
# tools would reach the server (`dontAsk` still auto-approves read-only ones).
BUILTINS = ("WebSearch",)
# The CLI inherits the app's whole environment; options.env only overlays it.
BLANKED_ENV = ("SECRET_KEY", "MARKMIK_KEY", "OWNER_PASSWORD", "OWNER_PASSWORD_HASH", "ANTHROPIC_API_KEY")
STRUCTURED_TIMEOUT = 120  # seconds for the whole call
CHAT_TIMEOUT = 240
STRUCTURED_MAX_TURNS = 3  # StructuredOutput is a tool call; room for one retry
SEARCH_MAX_TURNS = 6  # with web_search: a few searches, then StructuredOutput and its retry
CHAT_MAX_TURNS = 8


def is_fake():
    return os.environ.get("AI_FAKE") == "1"


def _default_fake_structured(prompt, schema, model):
    """An empty but schema-shaped answer: objects get their required keys as None/[]."""
    def empty(s):
        t = s.get("type")
        if t == "object":
            return {k: empty(s["properties"][k]) for k in s.get("required", [])}
        if t == "array":
            return []
        return None
    return empty(schema)


# Tests replace this: fake_structured = lambda prompt, schema, model: {...}
fake_structured = _default_fake_structured
FAKE_REPLY = "Proovivastus (AI_FAKE): "
# Tests (and the browser tests' /_test/ai/fake) may set fake_chat = lambda prompt, tools: events.
fake_chat = None


def fake_edit(reply, *, replace=(), append=""):
    """A fake_chat that changes the open note the way the model should (muuda_markmet): the note
    from the prompt's <note> block, its title kept, with `replace` ([old, new] pairs) and `append`
    (a paragraph at the end) applied; then `reply`."""
    def chat(prompt, tools):
        m = re.search(r'<note slug="([^"]*)"[^>]*>\n(.*)\n</note>', prompt, re.S)
        body = m.group(2)
        for old, new in replace:
            body = body.replace(old, new)
        if append:
            body = f"{body.rstrip()}\n\n{append}\n"
        args = {"slug": html.unescape(m.group(1)), "markdown": body}
        found = next(t for t in tools if getattr(t, "name", None) == "muuda_markmet")
        yield {"type": "tool", "name": "muuda_markmet", "input": args, "result": call_tool(found, args)}
        for i in range(0, len(reply), 8):
            yield {"type": "delta", "text": reply[i:i + 8]}
        yield {"type": "done", "session_id": "fake-session"}
    return chat


def count_call(conn):
    """Count one model call against today's cap; raise AIUnavailable when over it."""
    day = local_today().isoformat()
    conn.execute(
        "INSERT INTO ai_usage (day, calls) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET calls = calls + 1", (day,)
    )
    calls = conn.execute("SELECT calls FROM ai_usage WHERE day = ?", (day,)).fetchone()["calls"]
    if calls > DAILY_CAP:
        raise AIUnavailable(f"päevane AI-päringute piir ({DAILY_CAP}) on täis")


def run_structured(conn, prompt, schema, *, model="haiku", system=None, web_search=False, timeout=None,
                   max_turns=None, thinking=False):
    """One model call whose answer must match `schema` (JSON Schema). Returns the parsed object.
    Raises AIUnavailable, or RuntimeError when the answer is unusable.

    No tools, unless `web_search`: then WebSearch and nothing else. Search results are untrusted
    and the model can put words into its queries, so such a prompt must hold nothing private.

    `timeout` (seconds) and `max_turns` override the defaults for long calls (stock research);
    `thinking` turns the model's thinking on (adaptive), which extraction leaves off for speed."""
    if is_fake():
        count_call(conn)
        return fake_structured(prompt, schema, model)
    token = _token(conn)
    count_call(conn)
    tools = ["WebSearch"] if web_search else []
    with _workdir() as cwd:
        opts = _options(
            token, model=model, cwd=cwd,
            max_turns=max_turns or (SEARCH_MAX_TURNS if web_search else STRUCTURED_MAX_TURNS),
            tools=tools, allowed_tools=tools, system_prompt=system,
            output_format={"type": "json_schema", "schema": schema},
            thinking={"type": "adaptive"} if thinking else {"type": "disabled"},
        )
        result, failure = None, None
        try:
            for msg in _bridge(prompt, opts, timeout or (CHAT_TIMEOUT if web_search else STRUCTURED_TIMEOUT)):
                failure = _failure_of(msg) or failure
                if isinstance(msg, ResultMessage):
                    result = msg
        except ClaudeSDKError as exc:
            if result is None:  # otherwise it is the ResultError raised after an error result
                raise _sdk_error(exc) from None
    if result is None or result.is_error:
        raise _result_error(result, failure)
    out = result.structured_output
    missing = _missing(out, schema)
    if missing is not None:
        raise RuntimeError(f"AI vastus ei vasta skeemile ({missing})")
    return out


def stream_chat(conn, prompt, *, system=None, tools=(), model="sonnet", session_id=None):
    """Chat turn as a sync iterator of events:
        {"type": "delta", "text": str}
        {"type": "tool", "name": str, "input": dict, "result": object}
        {"type": "done", "session_id": str | None}
        {"type": "error", "message": str}
    `tools` are app-defined tool objects (SdkMcpTool) plus, optionally, the name "WebSearch"; the
    model never gets any other built-in tool. Raises AIUnavailable (no token, cap, rate limit,
    Claude unreachable); other failures end the stream with an "error" event."""
    if is_fake():
        count_call(conn)
        if fake_chat is not None:
            yield from fake_chat(prompt, tools)
            return
        text = FAKE_REPLY + prompt[:60]
        for i in range(0, len(text), 8):
            yield {"type": "delta", "text": text[i:i + 8]}
        yield {"type": "done", "session_id": session_id or "fake-session"}
        return
    yield from _real_chat(conn, prompt, system, tools, model)


def call_tool(tool, args):
    """Run an app tool's handler here, outside the model (fake mode). Returns its result text."""
    out = asyncio.run(tool.handler(args))
    return _tool_text(out.get("content"))


def test_connection(conn):
    """Ühendused card: one tiny call."""
    if is_fake():
        return {"ok": True, "summary": "AI_FAKE: mudelit ei kutsuta", "error": None}
    try:
        run_structured(conn, "Vasta: ok", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
    except (AIUnavailable, RuntimeError) as exc:
        return {"ok": False, "summary": "", "error": str(exc)}
    return {"ok": True, "summary": "Claude vastas", "error": None}


# --- Real mode ------------------------------------------------------------------------------------

def _token(conn):
    try:
        token = vault.get(conn, "claude_token")
    except vault.SecretsError:
        token = None
    if not token:
        raise AIUnavailable("Claude'i võti puudub (Seaded → Ühendused)")
    return token


def _options(token, *, model, cwd, max_turns, tools, thinking=None, **kw):
    """Every model call's options (docs/spikes/agent-sdk.md). `tools` is the real control: the
    built-ins listed there are the only ones the model sees."""
    env = {name: "" for name in BLANKED_ENV}
    env.update({
        "CLAUDE_CODE_OAUTH_TOKEN": token,  # only here, never in os.environ
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CONFIG_DIR": _config_dir(),
    })
    return ClaudeAgentOptions(
        model=model,
        cwd=cwd,  # empty temp dir: nothing to find even if a file tool slipped in
        max_turns=max_turns,
        setting_sources=[],  # no ~/.claude, no project settings, no CLAUDE.md
        strict_mcp_config=True,  # only mcp_servers below, never an .mcp.json or user MCP config
        permission_mode="dontAsk",  # anything not allow-listed is denied, never prompted
        tools=list(tools),
        env=env,
        # The CLI thinks by default: haiku extraction took 35 s with it, 4 s without.
        thinking=thinking or {"type": "disabled"},
        # The SDK prefers the CLI in its wheel; CLAUDE_CLI_PATH points at another one.
        cli_path=os.environ.get("CLAUDE_CLI_PATH") or None,
        **kw,
    )


_config = {"dir": None}
_config_lock = threading.Lock()


def _config_dir():
    """The CLI's config directory: private to this process (never a developer's ~/.claude or an
    inherited CLAUDE_CONFIG_DIR, whose settings, plugins or MCP servers would reach our calls) and
    removed when the process ends."""
    with _config_lock:
        if _config["dir"] is None:
            _config["dir"] = tempfile.mkdtemp(prefix="markmik-claude-")
            atexit.register(shutil.rmtree, _config["dir"], True)
        return _config["dir"]


@contextmanager
def _workdir():
    """An empty working directory for one call. Afterwards the CLI's transcript of the call (the
    prompt, note text, tool results), kept per working directory, is deleted."""
    with tempfile.TemporaryDirectory(prefix="markmik-ai-") as cwd:
        try:
            yield cwd
        finally:
            for path in {cwd, os.path.realpath(cwd)}:
                shutil.rmtree(os.path.join(_config_dir(), "projects", re.sub(r"[^A-Za-z0-9]", "-", path)),
                              ignore_errors=True)


def _real_chat(conn, prompt, system, tools, model):
    sdk_tools = [t for t in tools if isinstance(t, SdkMcpTool)]
    builtins = [t for t in tools if isinstance(t, str)]
    if set(builtins) - set(BUILTINS) or len(sdk_tools) + len(builtins) != len(tools):
        raise ValueError(f"lubamatu tööriist: {tools!r}")
    token = _token(conn)
    count_call(conn)
    with _workdir() as cwd:
        opts = _options(
            token, model=model, cwd=cwd, max_turns=CHAT_MAX_TURNS, tools=builtins,
            allowed_tools=builtins + [MCP_PREFIX + t.name for t in sdk_tools],
            # Every registered tool is shown to the model: register only ones that may run.
            mcp_servers={MCP_SERVER: create_sdk_mcp_server(MCP_SERVER, tools=sdk_tools)} if sdk_tools else {},
            system_prompt=system,
            include_partial_messages=True,
        )
        calls = {}  # tool_use id -> (name, input), until its result arrives
        ended, failure = False, None
        try:
            for msg in _bridge(prompt, opts, CHAT_TIMEOUT):
                failure = _failure_of(msg) or failure
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        yield {"type": "delta", "text": ev["delta"]["text"]}
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            calls[block.id] = (block.name.removeprefix(MCP_PREFIX), block.input)
                elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, ToolResultBlock) and block.tool_use_id in calls:
                            name, args = calls.pop(block.tool_use_id)
                            yield {"type": "tool", "name": name, "input": args, "result": _tool_text(block.content)}
                elif isinstance(msg, ResultMessage):
                    ended = True
                    if msg.is_error:
                        exc = _result_error(msg, failure)
                        if isinstance(exc, AIUnavailable):
                            raise exc
                        yield {"type": "error", "message": str(exc)}
                    else:
                        yield {"type": "done", "session_id": msg.session_id}
        except ClaudeSDKError as exc:
            if not ended:  # query() also raises ResultError right after an error result
                exc = _sdk_error(exc)
                if isinstance(exc, AIUnavailable):
                    raise exc from None
                yield {"type": "error", "message": str(exc)}


def _bridge(prompt, options, timeout):
    """query()'s messages, one by one, for sync code. The SDK runs on its own thread and event
    loop; past `timeout` seconds in total (or when the caller stops iterating) that task is
    cancelled, which ends the CLI process. Raises AIUnavailable on timeout."""
    box = queue.Queue()
    started = threading.Event()
    run = {}

    async def pump():
        run["loop"], run["task"] = asyncio.get_running_loop(), asyncio.current_task()
        started.set()
        try:
            async for msg in query(prompt=prompt, options=options):
                box.put(("msg", msg))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller's thread
            box.put(("error", exc))
        else:
            box.put(("end", None))

    thread = threading.Thread(target=asyncio.run, args=(pump(),), name="markmik-ai", daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                kind, value = box.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise AIUnavailable(f"Claude ei vastanud {timeout} sekundiga") from None
            if kind == "msg":
                yield value
            elif kind == "error":
                raise value
            else:
                return
    finally:
        if thread.is_alive() and started.wait(5):
            try:
                run["loop"].call_soon_threadsafe(run["task"].cancel)
            except RuntimeError:  # the loop closed meanwhile: nothing left to stop
                pass
        # The CLI process and any tool handler end before the caller cleans up after the call.
        thread.join(10)


def _failure_of(msg):
    """Why a call is failing, from the messages before the result: 'auth', 'limit', 'down'."""
    if isinstance(msg, AssistantMessage) and msg.error:
        return {"authentication_failed": "auth", "billing_error": "auth", "rate_limit": "limit",
                "server_error": "down"}.get(msg.error)
    if isinstance(msg, RateLimitEvent) and msg.rate_limit_info.status == "rejected":
        return "limit"
    return None


UNAVAILABLE = {
    "auth": "Claude'i võti ei kehti või on aegunud (Seaded → Ühendused)",
    "limit": "Claude'i kasutuspiir on praegu täis, proovi hiljem uuesti",
    "down": "Claude ei ole praegu kättesaadav, proovi hiljem uuesti",
}


def _result_error(result, failure):
    """The exception for a failed call: AIUnavailable when Claude could not be used at all."""
    status = result.api_error_status if result is not None else None
    if status in (401, 403):
        failure = "auth"
    elif status == 429:
        failure = "limit"
    elif status is not None and status >= 500:
        failure = "down"
    if failure:
        return AIUnavailable(UNAVAILABLE[failure])
    if result is None:
        return RuntimeError("AI ei andnud vastust")
    # On API errors subtype stays "success"; the text is in result.result. Logged, not shown:
    # it may quote the prompt.
    log.warning("AI call failed: %s %s", result.subtype, (result.result or "; ".join(result.errors or []))[:300])
    return RuntimeError(f"AI päring ebaõnnestus ({result.subtype})")


def _sdk_error(exc):
    if isinstance(exc, CLIConnectionError):  # includes CLINotFoundError
        log.warning("Claude CLI unavailable: %s", exc)
        return AIUnavailable("Claude Code'i ei saanud käivitada")
    log.warning("AI call failed: %s", type(exc).__name__)
    return RuntimeError(f"AI päring ebaõnnestus ({type(exc).__name__})")


def _missing(value, schema, path=""):
    """First required key missing from `value` (or a wrong object/array), else None. Types, enums
    and formats beyond that are the caller's to check (dates are not enforced by the CLI)."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return path or "/"
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}/{key}"
        for key, sub in schema.get("properties", {}).items():
            if key in value and value[key] is not None and (m := _missing(value[key], sub, f"{path}/{key}")):
                return m
    elif t == "array":
        if not isinstance(value, list):
            return path or "/"
        for i, item in enumerate(value):
            if m := _missing(item, schema.get("items", {}), f"{path}/{i}"):
                return m
    return None


def _tool_text(content):
    """A tool result's content (str, or MCP content blocks) as text."""
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content or ""
