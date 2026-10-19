"""A one-tool MCP server: the starting point for your own.

It speaks MCP over stdio, so you do not run it yourself: hello_agent.py starts it. It connects to
the database as the restricted user 'agent', like your real MCP server should.

To see the tool without an LLM:  python hello_mcp.py --test 1
"""
import kit  # first: it checks your Python version before anything else is imported

import sys
from datetime import date, timedelta

from mcp.server import MCPServer  # MCP Python SDK 2.x; in 1.x this class was called FastMCP

mcp = MCPServer("watchdog-hello")


@mcp.tool()
def list_batches(day: str) -> list[dict]:
    """List the batches delivered on a day (yyyy-mm-dd): the feed, its row count and status, and the
    average row count that feed sent on the same weekday over the previous 30 days."""
    d = date.fromisoformat(day)
    with kit.connect(user="agent", password="agent") as conn, conn.cursor() as cur:  # errors reach the agent
        cur.execute(
            "SELECT b.feed_name, b.row_count, b.status,"
            " (SELECT round(avg(h.row_count)) FROM batches h WHERE h.feed_name = b.feed_name AND h.status = 'promoted'"
            "  AND h.batch_date >= %s AND h.batch_date < %s AND extract(dow FROM h.batch_date) = extract(dow FROM b.batch_date))"
            " FROM batches b WHERE b.batch_date = %s ORDER BY b.batch_id",
            (d - timedelta(days=30), d, d))
        return [{"feed": f, "rows": n, "status": s, "usual_rows_on_this_weekday": None if u is None else int(u)}
                for f, n, s, u in cur.fetchall()]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        if not sys.argv[2].isdigit() or not 1 <= int(sys.argv[2]) <= kit.SIM_DAYS:
            sys.exit(f"Usage: python hello_mcp.py --test DAY   (1 to {kit.SIM_DAYS})")
        day = kit.sim_dates()[int(sys.argv[2]) - 1].isoformat()
        try:
            rows = list_batches(day)
        except kit.DatabaseUnavailable as e:
            sys.exit(str(e))
        if not rows:
            sys.exit(f"No batches for {day} yet: run the simulator up to that day first (python simulator.py step).")
        for row in rows:
            print(row)
    else:
        mcp.run()  # stdio
