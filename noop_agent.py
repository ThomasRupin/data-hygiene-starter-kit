"""The do-nothing agent: every row is promoted as delivered. Run it once to see your baseline:

  python simulator.py run --agent noop_agent:run_day
  python simulator.py report
"""


async def run_day(day):
    return None
