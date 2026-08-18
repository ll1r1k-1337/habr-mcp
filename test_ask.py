#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "httpx"]
# ///
"""Checks habr_ask: the client answers the elicitation and the answer reaches the tool."""
import asyncio

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import ElicitResult


async def main() -> None:
    async def answer(ctx, params):  # the client plays "the user"
        assert "hh.ru" in params.message, params.message
        return ElicitResult(action="accept", content={"answer": "https://hh.ru/resume/42"})

    params = StdioServerParameters(command="./server.py", args=[])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w, elicitation_callback=answer) as s:
            await s.initialize()
            assert "habr_ask" in {t.name for t in (await s.list_tools()).tools}
            out = await s.call_tool("habr_ask", {"question": "Ссылка на резюме hh.ru?"})
            text = out.content[0].text
            assert text == "https://hh.ru/resume/42", text
    print("ok: habr_ask asked and got the answer")


asyncio.run(main())
