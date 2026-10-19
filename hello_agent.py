"""An ADK agent that calls the hello MCP server: the starting point for your own.

  python hello_agent.py 15                               ask about simulation day 15 (run that day first)
  python simulator.py step --agent hello_agent:run_day   let the simulator call it

The LLM comes from .env:
  GOOGLE_API_KEY=...                          Gemini, free key from aistudio.google.com (default model below)
  WATCHDOG_MODEL=ollama_chat/qwen3:4b         or a local model through Ollama (needs: ollama pull qwen3:4b)
"""
import kit  # first: it checks your Python version before anything else is imported

import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")  # ADK announces its experimental features

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters

DEFAULT_GEMINI = "gemini-flash-latest"


def model():
    name = kit.ENV.get("WATCHDOG_MODEL", DEFAULT_GEMINI)
    if name.startswith(("ollama", "openai/", "anthropic/")):  # anything LiteLLM serves
        from google.adk.models.lite_llm import LiteLlm
        return LiteLlm(model=name)
    if not kit.ENV.get("GOOGLE_API_KEY"):
        raise RuntimeError("No LLM configured. Put GOOGLE_API_KEY=... in .env (free key from aistudio.google.com),\n"
                           "or WATCHDOG_MODEL=ollama_chat/qwen3:4b to use a local model through Ollama.")
    os.environ.setdefault("GOOGLE_API_KEY", kit.ENV["GOOGLE_API_KEY"])
    return name


async def run_day(day):
    # MCP passes only a few environment variables to the server it starts; pass ours, so settings such as
    # PG_PORT made in the shell reach it too.
    tools = McpToolset(connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(command=sys.executable, args=[str(kit.ROOT / "hello_mcp.py")],
                                            env=dict(os.environ)),
        timeout=30))
    agent = LlmAgent(
        name="hello_watchdog",
        model=model(),
        instruction="You check a retailer's daily data feeds. Use the list_batches tool, then say in two or three "
                    "sentences whether any feed is missing or has far fewer or far more rows than usual.",
        tools=[tools])
    runner = InMemoryRunner(agent=agent, app_name="hello_watchdog")
    try:
        session = await runner.session_service.create_session(app_name="hello_watchdog", user_id="simulator")
        question = types.Content(role="user", parts=[types.Part(text=f"Check the batches delivered on {day.isoformat()}.")])
        async for event in runner.run_async(user_id="simulator", session_id=session.id, new_message=question):
            for part in (event.content.parts if event.content else []) or []:
                if part.function_call:
                    print(f"  [tool call] {part.function_call.name}({dict(part.function_call.args or {})})")
                elif part.text and not part.thought and event.is_final_response():  # skip the model's reasoning
                    print(f"  [agent] {part.text.strip()}")
    finally:
        await tools.close()
        await runner.close()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or not 1 <= int(sys.argv[1]) <= kit.SIM_DAYS:
        sys.exit(f"Usage: python hello_agent.py DAY   (1 to {kit.SIM_DAYS})")
    try:
        asyncio.run(run_day(kit.sim_dates()[int(sys.argv[1]) - 1]))
    except RuntimeError as e:
        sys.exit(str(e))
