"""Consume an existing MCP search server with a scripted agent.

Run: uv run --extra mcp python -m examples.31_mcp --url http://localhost:8000/mcp
The server must provide a read-only search(query: str) tool.
"""

import asyncio
from argparse import ArgumentParser

from harnessed import Agent, ToolCall
from harnessed.models import ModelChunk
from harnessed.plugins.mcp import MCP
from harnessed.testing import ScriptedModel


async def main(url: str) -> None:
    docs = MCP.http(
        "docs",
        url=url,
        include_tools=("search",),
        readonly_tools=("search",),
    )
    model = ScriptedModel(
        [
            [ModelChunk(tool_call=ToolCall("docs__search", {"query": "MCP"}))],
            "Each session owns its MCP connection.",
        ]
    )
    agent = Agent(model=model, plugins=[docs])
    result = await agent.run("How are MCP connections owned?")
    print(result.tool_calls[0][1].text)
    print(result.text)


if __name__ == "__main__":
    parser = ArgumentParser(description="Consume an existing read-only MCP search tool.")
    parser.add_argument("--url", required=True, help="Streamable HTTP endpoint of the MCP server")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.url))
