"""Sketch of the real mode of app/ai/runner.py (spike S2).

Verified inside the markmik-s2 image (spikes/agent-sdk/Dockerfile) with
claude-agent-sdk 0.2.159 and the pinned CLI 2.1.280. See docs/spikes/agent-sdk.md.

    python runner_sketch.py      # one run_structured + one stream_chat call (haiku)
"""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
)

MCP_SERVER = "markmik"  # in-process tools are exposed as mcp__markmik__<name>
MCP_PREFIX = f"mcp__{MCP_SERVER}__"


def _token() -> str:
    # Stand-in for app.secrets.get("CLAUDE_CODE_OAUTH_TOKEN"). The token never
    # enters os.environ; it reaches the CLI only through ClaudeAgentOptions.env.
    path = os.environ.get("MARKMIK_ENV_FILE", "/run/secrets/markmik.env")
    with open(path) as f:
        for line in f:
            key, _, value = line.strip().partition("=")
            if key == "CLAUDE_CODE_OAUTH_TOKEN":
                return value.strip().strip("'\"")
    raise RuntimeError("CLAUDE_CODE_OAUTH_TOKEN missing")


def _options(model: str, cwd: str, max_turns: int, **kw: Any) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=model,
        cwd=cwd,  # empty temp dir: nothing to find even if a file tool slipped in
        max_turns=max_turns,
        setting_sources=[],  # no ~/.claude, no project settings, no CLAUDE.md
        permission_mode="dontAsk",  # anything not allow-listed is denied, never prompted
        # The SDK prefers the CLI bundled in its wheel over PATH; point it at the pinned one.
        cli_path=os.environ.get("CLAUDE_CLI_PATH") or None,
        env={
            "CLAUDE_CODE_OAUTH_TOKEN": _token(),
            # An inherited API key would win over the OAuth token and bill the API instead.
            "ANTHROPIC_API_KEY": "",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",  # drops the telemetry plugin
        },
        # The CLI thinks by default: haiku extraction took 35 s with it, 4 s without.
        thinking={"type": "disabled"},
        **kw,
    )


async def run_structured(prompt: str, schema: dict, model: str = "haiku") -> dict:
    """One extraction call with no tools. Returns the object validated against schema."""
    with tempfile.TemporaryDirectory(prefix="markmik-ai-") as cwd:
        opts = _options(
            model,
            cwd,
            max_turns=3,  # StructuredOutput is a tool call; leave room for one retry
            tools=[],
            output_format={"type": "json_schema", "schema": schema},
        )
        result = None
        try:
            async for msg in query(prompt=prompt, options=opts):
                if isinstance(msg, ResultMessage):
                    result = msg
        except ClaudeSDKError:
            if result is None:  # otherwise it is the ResultError raised after an error result
                raise
    if result is None or result.is_error or result.structured_output is None:
        # subtype stays "success" on API errors (401, 429); the text is in result.result
        detail = result and (result.result or result.errors or result.subtype)
        raise RuntimeError(f"structured call failed: {detail}")
    return result.structured_output


def _render(messages: list[dict]) -> str:
    # The chat thread lives in our DB; every turn replays it. No CLI session state.
    *history, last = messages
    if not history:
        return last["content"]
    lines = [f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in history]
    return "<history>\n" + "\n".join(lines) + "\n</history>\n\n" + last["content"]


async def stream_chat(
    messages: list[dict],
    tools: list[SdkMcpTool | str],
    model: str = "haiku",
    system: str | None = None,
    max_turns: int = 6,
) -> AsyncIterator[dict]:
    """Yield {"type": "delta"|"tool"|"done"|"error", ...} for one assistant turn.

    messages: [{"role": "user"|"assistant", "content": str}, ...], last one from the user.
    tools: in-process @tool objects, plus built-in names such as "WebSearch".
    """
    sdk_tools = [t for t in tools if isinstance(t, SdkMcpTool)]
    builtins = [t for t in tools if isinstance(t, str)]
    allowed = builtins + [MCP_PREFIX + t.name for t in sdk_tools]
    mcp = {MCP_SERVER: create_sdk_mcp_server(MCP_SERVER, tools=sdk_tools)} if sdk_tools else {}

    with tempfile.TemporaryDirectory(prefix="markmik-ai-") as cwd:
        opts = _options(
            model,
            cwd,
            max_turns=max_turns,
            tools=builtins,  # [] = no built-ins at all
            allowed_tools=allowed,
            mcp_servers=mcp,
            system_prompt=system,
            include_partial_messages=True,
        )
        ended = False
        try:
            async for msg in query(prompt=_render(messages), options=opts):
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev["type"] == "content_block_delta" and ev["delta"]["type"] == "text_delta":
                        yield {"type": "delta", "text": ev["delta"]["text"]}
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            name = block.name.removeprefix(MCP_PREFIX)
                            yield {"type": "tool", "name": name, "input": block.input}
                elif isinstance(msg, ResultMessage):
                    ended = True
                    if msg.is_error:  # auth, rate limit, max_turns...
                        yield {"type": "error", "message": msg.result or "; ".join(msg.errors or [msg.subtype])}
                    else:
                        yield {
                            "type": "done",
                            "text": msg.result,
                            "denied": [d["tool_name"] for d in msg.permission_denials or []],
                            "usage": msg.usage,
                        }
        except ClaudeSDKError as e:
            if not ended:  # query() also raises ResultError right after an error result
                yield {"type": "error", "message": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    from claude_agent_sdk import tool

    @tool("search_notes", "Search the user's notes. Returns matching note titles.", {"query": str})
    async def search_notes(args):
        return {"content": [{"type": "text", "text": "- Matemaatika: kontrolltöö 2.10 (ruum 204)"}]}

    schema = {
        "type": "object",
        "properties": {"subject": {"type": "string"}, "room": {"type": "string"}},
        "required": ["subject", "room"],
        "additionalProperties": False,
    }

    async def main():
        print("run_structured ->", await run_structured(
            "Kontrolltöö matemaatikas toimub ruumis 204.", schema))
        msgs = [
            {"role": "user", "content": "Mu tütre nimi on Liis."},
            {"role": "assistant", "content": "Selge, jätan meelde."},
            {"role": "user", "content": "Mis on mu tütre nimi? Otsi ka märkmetest "
             "'kontrolltöö' ja ütle ruum. Vasta ühe lausega."},
        ]
        n = 0
        async for ev in stream_chat(msgs, [search_notes]):
            if ev["type"] == "delta":
                n += 1
            else:
                print(ev)
        print("deltas:", n)

    asyncio.run(main())
