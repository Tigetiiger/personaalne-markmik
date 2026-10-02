"""Spike S5: a picture through the Agent SDK's streaming input, with chat's options and an SDK MCP
tool registered (docs/KODU-PLAN.md, step 0).

    uv run python spikes/agent-sdk/image_probe.py [n_images]

Reads CLAUDE_CODE_OAUTH_TOKEN from the repo's .env; never prints it.
"""

import asyncio
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from claude_agent_sdk import (AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock, UserMessage,
                              create_sdk_mcp_server, query, tool)

from app.ai import runner

ENV = Path(__file__).resolve().parents[2] / ".env"
TOKEN = next(line.split("=", 1)[1].strip().strip("'\"") for line in ENV.read_text().splitlines()
             if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="))


def picture(text, size=(3000, 2000)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=140)
    for i, line in enumerate(text.split("\n")):
        d.text((150, 200 + i * 220), line, fill="black", font=font)
    img.thumbnail((1568, 1568))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


made = []


@tool("loo_markme", "Loo uus märge.", {"type": "object", "properties": {"title": {"type": "string"},
      "body": {"type": "string"}}, "required": ["title", "body"]})
async def loo_markme(args):
    made.append(args)
    return {"content": [{"type": "text", "text": json.dumps({"slug": "x", "title": args["title"]})}]}


async def main(n):
    pics = [picture(f"Poe nimekiri {i + 1}:\n- piim\n- leib\n- {['õunad', 'juust', 'munad', 'kohv'][i]}")
            for i in range(n)]
    print("jpeg bytes:", [len(p) for p in pics])
    content = [{"type": "text", "text": "Tee igast pildist märge tööriistaga loo_markme (pealkiri = esimene rida). "
                                        "Seejärel ütle ühe lausega, mis pildil oli."}]
    content += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(p).decode()}} for p in pics]

    async def prompt():
        yield {"type": "user", "session_id": "", "parent_tool_use_id": None,
               "message": {"role": "user", "content": content}}

    with runner._workdir() as cwd:
        opts = runner._options(TOKEN, model="sonnet", cwd=cwd, max_turns=8, tools=[],
                               allowed_tools=["mcp__markmik__loo_markme"],
                               mcp_servers={"markmik": create_sdk_mcp_server("markmik", tools=[loo_markme])},
                               system_prompt="Vasta eesti keeles.", include_partial_messages=True)
        t0 = time.monotonic()
        text = []
        async for msg in query(prompt=prompt(), options=opts):
            t = f"{time.monotonic() - t0:6.2f}s"
            if isinstance(msg, StreamEvent):
                ev = msg.event
                if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                    text.append(ev["delta"]["text"])
            elif isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, ToolUseBlock):
                        print(t, "tool_use", b.name, json.dumps(b.input, ensure_ascii=False)[:200])
            elif isinstance(msg, UserMessage):
                kinds = [type(b).__name__ if not isinstance(b, dict) else b.get("type") for b in
                         (msg.content if isinstance(msg.content, list) else [msg.content])]
                print(t, "UserMessage echo, blocks:", kinds)
            elif isinstance(msg, ResultMessage):
                print(t, "result is_error", msg.is_error, "turns", msg.num_turns,
                      "usage", json.dumps(msg.usage)[:400])
        print("text:", "".join(text))
        print("made:", made)


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1))
