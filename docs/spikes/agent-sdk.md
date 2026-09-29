# Spike S2: Claude Agent SDK

Date: 2026-09-24. Branch `wp/spike-agentsdk`. Decides the real mode of `app/ai/runner.py`.

**Environment of every result below:** the `markmik-s2` image (`spikes/agent-sdk/Dockerfile`):
python:3.13-slim, uid 1000, `claude-agent-sdk==0.2.159`, CLI **2.1.280** pinned via `install.sh`,
`DISABLE_AUTOUPDATER=1`, own empty `CLAUDE_CONFIG_DIR=/app/.claude-markmik`. Docker worked, so no
model call ran on the host (the host was only used to read the SDK source). Budget used: 17 model
calls (16 haiku, 1 sonnet) plus 3 deliberate auth failures that were rejected with no usage.

Reproduce (from the repo root; the token file is mounted read-only, never baked in):

```sh
docker build -t markmik-s2 spikes/agent-sdk
R="docker run --rm -v $PWD/.env:/run/secrets/markmik.env:ro -v $PWD/spikes/agent-sdk:/app/spike:ro -w /app/spike markmik-s2"
$R python probes.py tools|structured|structured_fast|mcp|env|latency|chat   # see probes.py
$R python runner_sketch.py
```

## Verified (trimmed real output)

### 1. Which CLI runs: the SDK bundles its own
The wheel ships a 226 MiB CLI (`_bundled/claude`, 2.1.281) and `_find_cli()` checks it **before**
PATH. The install.sh pin only runs if `cli_path` points at it:

```
default cli -> /app/.venv/lib/python3.13/site-packages/claude_agent_sdk/_bundled/claude 226 MiB
init with cli_path=$CLAUDE_CLI_PATH: "claude_code_version": "2.1.280"
```
Image is 1.42 GB, carrying both CLIs (`~/.local/share/claude` 223 MB, venv 271 MB).

### 2. Tool surface (`setting_sources=[]`, `permission_mode="dontAsk"`, empty temp cwd, haiku)
Same prompt each time: run `id` with Bash, Read `/etc/hostname`, Read `./note.txt` (placed in cwd),
Write `/tmp/pwn`, WebFetch example.com.

| Case | init `tools` | What happened |
|---|---|---|
| A `tools=[]` | `[]` | No tool call (`num_turns` 1). **The model invented the results anyway**: `uid=0(root)`, hostname `s2-sandbox`, a fake note, a fake example.com page |
| B `tools=["WebSearch"]`, `allowed_tools=["WebSearch"]` | `["WebSearch"]` | Refused 1-5 ("tools I do NOT have"), **WebSearch ran** and returned tallinn.ee results; `num_turns` 2, $0.032 (notional `total_cost_usd`) |
| C tools unset (default preset), `allowed_tools=["WebSearch"]` | 29 tools (`Task`, `Bash`, `Read`, `Write`, `WebFetch`, `Workflow`...) | **`Bash id` ran** (`uid=1000(app)`), **`Read ./note.txt` ran** (`secret-in-cwd`); `Read /etc/hostname`, `Bash cat /etc/hostname`, `Write /tmp/pwn` were denied; it then used `ToolSearch` to load WebFetch and hit `error_max_turns` |

```
C 3.50s Assistant tool_use: Bash {"command": "id", ...}
C 3.60s User tool_result:  uid=1000(app) gid=1000(app) groups=1000(app)
C 4.74s SystemMessage permission_denied {"tool_name": "Read", "decision_reason_type": "mode", ...}
C 9.46s User tool_result:  1	secret-in-cwd
```
So `dontAsk` is **not** a sandbox: read-only built-ins (a read-only shell command, Read inside cwd)
are auto-approved. `tools=[]` / an explicit `tools` list is the control; `dontAsk` is the backstop.
`WebSearch` can be allowed on its own: `tools=["WebSearch"]` exposes exactly that.

### 3. Structured output (`output_format={"type":"json_schema","schema":...}`, `tools=[]`)
Schema: `events[]` of `kind` (enum), `title`, `start` (`format: date-time`), `end` (nullable
date-time), `all_day`, `date` (`format: date`), `date_text`, `source_quote`, `confidence` (enum).
The CLI adds a `StructuredOutput` tool (init `"tools": ["StructuredOutput"]`); the object appears
three times in the stream:

```
Assistant tool_use: StructuredOutput {"events": [{"kind": "exam", ...}]}     # AssistantMessage.content[ToolUseBlock]
User tool_result:  Structured output provided successfully
ResultMessage {"subtype": "success", "num_turns": 2, "stop_reason": "tool_use",
  "result": "{\"events\":[...]}",                 # JSON string
  "structured_output": {"events": [{"kind": "exam", "title": "Matemaatika kontrolltöö",
    "start": "2026-10-03T14:15:00", "end": "2026-10-03T15:45:00", "all_day": false,
    "date": "2026-10-03", "date_text": "reedel, 3. oktoobril 2026",
    "source_quote": "Kontrolltöö toimub reedel 3. oktoobril kell 14.15 ruumis 204.",
    "confidence": "high"}]}}                      # parsed dict: use this
```
- **`format` is not enforced.** Told to write Estonian dates, it returned
  `"start": "03.10.2026 kell 14.15"`, `"date": "03.10.2026"` with `subtype: "success"`. Types,
  enums and `required` are the schema's job; formats must be checked in code.
- `start` came back naive (`2026-10-03T14:15:00`) in one run and `2026-10-03T14:15:00+03:00` in another.
- **The end time was invented** (15:45, then 15:15; the email gives none), and `date_text` was
  paraphrased, not quoted.
- 3.10.2026 is a **Saturday**; the email says "reedel" (Friday). The model did not flag it and said
  `confidence: "high"`. This confirms the code-side weekday check in §8.
- **Thinking is on by default.** S1 spent 3,250 thinking tokens: 35.7 s. The same call with
  `thinking={"type":"disabled"}` took **4.0 s** and $0.0034 (vs $0.0205).

### 4. In-process tool (`@tool` + `create_sdk_mcp_server("notes", ...)`, `tools=[]`)
```
init "tools": ["mcp__notes__delete_note", "mcp__notes__search_notes"],
     "mcp_servers": [{"name": "notes", "status": "connected", "source": "sdk"}]
Assistant tool_use: mcp__notes__search_notes {"query": "kontrolltöö"}
   [handler] search_notes called with {'query': 'kontrolltöö'}        # ran in our process
Assistant tool_use: mcp__notes__delete_note {"slug": "matemaatika"}
SystemMessage permission_denied {"tool_name": "mcp__notes__delete_note", ...}
ResultMessage {"num_turns": 3, "permission_denials": [{"tool_name": "mcp__notes__delete_note", ...}]}
```
The name format is `mcp__<server>__<tool>`. `tools=[]` does not hide MCP tools: every registered
tool is shown to the model and only the allow-list decides what runs. Register only what may run.

### 5. Streaming (`include_partial_messages=True`)
`StreamEvent` fields: `event`, `parent_tool_use_id`, `session_id`, `uuid`; `event` is the raw
Messages API stream event. Sequence for one tool call and an answer (default thinking on):
```
message_start content_block_start:thinking content_block_delta:thinking_deltax2
content_block_delta:signature_delta content_block_stop content_block_start:tool_use
content_block_delta:input_json_deltax4 content_block_stop ... message_delta message_stop
message_start ... content_block_start:text content_block_delta:text_deltax25 content_block_stop
message_delta message_stop
```
Text for SSE: `ev["type"] == "content_block_delta" and ev["delta"]["type"] == "text_delta"` ->
`ev["delta"]["text"]`; joined deltas equalled `ResultMessage.result`. Complete tool inputs are
simplest to take from the `AssistantMessage` `ToolUseBlock` that follows each block. Other
messages seen: `SystemMessage` subtypes `init`, `status`, `thinking_tokens`, `permission_denied`,
and `RateLimitEvent` (`"status": "allowed", "rate_limit_type": "five_hour"`).

### 6. Token only through `ClaudeAgentOptions.env`
All probes read the token from the mounted file and passed `env={"CLAUDE_CODE_OAUTH_TOKEN": ...}`;
the container never had it in its environment (`CLAUDE_CODE_OAUTH_TOKEN in os.environ: False`),
and every call succeeded (init `"apiKeySource": "none"`).
- `env={}`: `Assistant text: Not logged in · Please run /login`, `AssistantMessage.error:
  authentication_failed`, `ResultMessage {"subtype": "success", "is_error": true}`, then
  `query()` **raises** `ResultError ... (exit code: 1)`.
- Invalid token: `{'type': 'error', 'message': 'Failed to authenticate. API Error: 401 OAuth access
  token is invalid.'}` from `stream_chat`.
- The CLI did not write the env token into `CLAUDE_CONFIG_DIR` (grep: 0 files). The real token is
  in no file in the worktree, in no log, and not in the image metadata.
- `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` in `env`: init `analytics_disabled: True`, the
  `telemetry` plugin disappears, and WebSearch still works (final run below).

### 7. Latency (single samples)
| Call | to init | first token | total |
|---|---|---|---|
| haiku, first `query()` in the process | 1.25 s | 2.30 s | 2.46 s |
| haiku, second `query()` | 0.78 s | 1.79 s | 2.01 s |
| sonnet, one-line answer | 0.80 s | 1.47 s | 2.02 s |
| haiku, `ClaudeSDKClient` 2nd turn (process reused) | n/a | n/a | 1.31 s |

Every `query()` spawns a CLI process: ~0.8-1.3 s before the model is asked. `import
claude_agent_sdk` costs 2.4 s (the `mcp` package), once per Python process. The SDK's `claude -v`
version probe costs ~15 ms.

### 8. Multi-turn
- `ClaudeSDKClient`, two turns in one process: `'Selge.'` (1.70 s), then `'Roheline.'` (1.31 s).
- `resume=session_id` in a fresh `query()`: answered `Roheline.` from the same cwd **and** from a
  different temp cwd. It needs the transcript at
  `$CLAUDE_CONFIG_DIR/projects/<sanitised cwd>/<session>.jsonl`. **Every** `query()` writes one.
- History replayed into the prompt (what `runner_sketch.stream_chat` does): recalled "Liis" from a
  prior turn stored only in our message list.

### runner_sketch.py (both functions, haiku, thinking off)
```
run_structured -> {'subject': 'matemaatika', 'room': '204'}
{'type': 'tool', 'name': 'search_notes', 'input': {'query': 'kontrolltöö'}}
{'type': 'done', 'text': 'Sinu tütre nimi on Liis ja kontrolltöö on matemaatikas 2.10 ruumis 204.', 'denied': [], ...}
deltas: 12
```
Final run with `tools=["WebSearch", search_notes]` and a system prompt: `tool search_notes`, then
`tool WebSearch`, then `done` with sources; first delta 1.8 s, total 10.1 s, 96 deltas.

## Not verified
- A real `ANTHROPIC_API_KEY` in the inherited env (the runner blanks it; only `""` was tested).
- Rate-limit rejection (`RateLimitEvent` only ever said `allowed`) and the 5-hour limit error text.
- `resume` after a container restart (needs `CLAUDE_CONFIG_DIR` on a volume) and `session_store`.
- Several concurrent `query()` calls in one worker; bridging the async generator into Flask's sync
  SSE response (a thread + queue, or `asyncio.run` per request).
- Sonnet with tools or structured output; `pattern`/`minLength` enforcement; injection resistance
  (that is the WP extraction eval).
- A builtin plugin `agents-md` appears in init in some runs and not others. With an empty cwd there
  is nothing for it to load; not investigated.
- The latency figures are single samples.

## Recommended runner code and options
`spikes/agent-sdk/runner_sketch.py` is the working basis. Options, in one place:

```python
ClaudeAgentOptions(
    model=model,                      # "haiku" extraction/inline, "sonnet" Abiline chat
    cwd=tempfile.TemporaryDirectory(),  # empty, per call
    max_turns=3 (structured) / 6 (chat),
    setting_sources=[],
    permission_mode="dontAsk",
    tools=[] or ["WebSearch"],        # THE control: built-ins listed here are the only ones exposed
    allowed_tools=builtins + ["mcp__markmik__<tool>", ...],
    mcp_servers={"markmik": create_sdk_mcp_server("markmik", tools=[...])},  # only runnable tools
    thinking={"type": "disabled"},    # 35 s -> 4 s for extraction
    cli_path=os.environ["CLAUDE_CLI_PATH"],   # or drop install.sh, see below
    env={"CLAUDE_CODE_OAUTH_TOKEN": secrets.get(...), "ANTHROPIC_API_KEY": "",
         "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    include_partial_messages=True,    # chat only
    output_format={"type": "json_schema", "schema": ...},  # extraction only
    system_prompt=...,                # chat: say which tools exist (case A: it fakes the rest)
)
```
- `run_structured`: read `ResultMessage.structured_output`. Treat `is_error`, a missing object,
  or a raised `ClaudeSDKError` as failure. Validate formats, times and weekday in code
  (`etdates`). Ask for `end: null` when the text gives none and for `date_text` quoted verbatim.
- `stream_chat`: `text_delta` -> `delta`, `ToolUseBlock` -> `tool` (prefix stripped),
  `ResultMessage` -> `done`/`error`. Swallow the `ResultError` raised after an error result.
- **Multi-turn: replay the history from our DB each turn.** It is the simplest option for a
  per-note thread. There is one source of truth, nothing survives or leaks between calls, it works
  with a fresh temp cwd and across restarts, and edited or deleted messages are honoured. The cost
  is re-sending short threads plus one process spawn per turn (~1 s). `resume` works, but it keeps
  a second copy of the thread in `CLAUDE_CONFIG_DIR` that must persist and be pruned.
  `ClaudeSDKClient` needs a live CLI process per open thread across HTTP requests, which is a poor
  fit for gunicorn workers. Consider it only if the extra second per turn matters.
- **CLI choice (lead to decide):** either keep the install.sh pin and always pass `cli_path`
  (as the sketch does), or drop install.sh and use the bundled CLI, pinned by the SDK version in
  `uv.lock` (SDK and CLI then ship as one tested pair; the image is ~220 MB smaller). The
  bundled CLI cannot be avoided: it ships inside the wheel either way.

## Gotchas
1. The SDK prefers its bundled CLI over PATH; without `cli_path` your pin is silently ignored.
2. `dontAsk` auto-approves read-only built-ins (`Bash id`, Read inside cwd). Never rely on it alone.
3. With no tools, the model states tool results it never obtained.
4. `format` (`date`, `date-time`) is not enforced by `output_format`.
5. Extended thinking is on by default and makes haiku ~9x slower here.
6. `query()` raises `ResultError` right after yielding an `is_error` `ResultMessage`; on API errors
   `subtype` stays `"success"`, and the message is in `result`.
7. The CLI child inherits the whole `os.environ` (`SECRET_KEY`, `MARKMIK_KEY`...). `options.env` only
   overlays it. Harmless with no shell or file tools; blank sensitive keys anyway.
8. Every call writes a transcript (prompt, email text, tool results) under `CLAUDE_CONFIG_DIR/projects/`.
   Put the directory on tmpfs, or prune it.
9. `claude --version` at build time writes `.claude.json` (machineID, userID) into the image's config
   dir, and the CLI writes there at runtime, so the directory must be writable.
10. `import claude_agent_sdk` takes 2.4 s. Import at worker boot, not per request.
11. WebSearch sub-requests are not counted in `ResultMessage.usage.server_tool_use` (it showed 0).

## Spike S5: a long research call through `run_structured` (2026-09-29, Aktsiad)

For docs/AKTSIAD-PLAN.md: one discovery call and one synthesis call, both real, through
`runner.run_structured` with the new `timeout`, `max_turns` and `thinking` arguments.

- **Discovery** (sonnet, `web_search=True`, `max_turns=12`, timeout 600 s): "up to 6 recent
  primary or reputable pages about NVIDIA" → 6 candidates in **36 s**, schema-valid.
- **Fetching them with `ics.download`**: with httpx's default User-Agent only 2 of 6 answered
  (investor.nvidia.com, sec.gov and bloomberg.com gave 403). `ics.download(..., user_agent=…)`
  now exists: a browser-like agent opens investor.nvidia.com, and SEC answers when the agent
  carries contact details (the Turuandmed card's `sec_user_agent`). Bloomberg stays closed
  (paywall); such candidates are simply dropped.
- **Synthesis** (sonnet, no tools, `thinking=True`, 13 k characters of page text, Estonian
  output with bull/bear claims citing an index into citations): **46 s**, schema-valid, sensible
  Estonian prose; 4 bull and 4 bear claims over 2 citations.
- So a deep-research night of ~10 stocks takes about 15 minutes of model time, well inside the
  02:00–06:00 window. Timeouts of 600 s leave room for slow days.
