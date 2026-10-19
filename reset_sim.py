"""Start the 28 simulation days again, keeping the 90 days of history.

Empties the staging tables and everything your agent wrote (issues, fixes, quarantine, tickets,
hand-offs, agent log), removes what the simulation promoted into the core tables, and forgets
every delivered simulation batch. Use it after a test run, or after choosing another scenario.

  python reset_sim.py
"""
import kit

TABLES = ["fixes", "quarantine", "issues", "tickets", "hand_offs", "agent_log", "sim_results",
          "staging_orders", "staging_customers", "staging_products"]


def history_state(conn, feed, key):
    """The last version of each record in the history files, with the batch that wrote it."""
    batch = dict(conn.execute("SELECT batch_date, batch_id FROM batches WHERE feed_name = %s AND batch_date < %s",
                              (feed, kit.SIM_START)).fetchall())
    state = {}
    for day in kit.history_dates():
        for row in kit.read_csv(kit.feed_file(kit.HISTORY, feed, day))[1]:
            state[row[key]] = (row, batch[day])
    return state


with kit.connect_or_exit() as conn:
    kit.apply_schema(conn)
    conn.execute(f"TRUNCATE {', '.join(TABLES)}")
    sim = "(SELECT batch_id FROM batches WHERE batch_date >= %s)"
    conn.execute(f"DELETE FROM column_profiles WHERE batch_id IN {sim}", (kit.SIM_START,))
    conn.execute(f"DELETE FROM orders WHERE batch_id IN {sim}", (kit.SIM_START,))
    for table, feed, key in (("customers", "crm", "customer_id"), ("products", "pim", "sku_id")):
        touched = [r[0] for r in conn.execute(f"SELECT {key} FROM {table} WHERE batch_id IN {sim}", (kit.SIM_START,))]
        if not touched:
            continue
        state = history_state(conn, feed, key)
        cols = kit.FEEDS[feed][1][1:]
        for rid in touched:
            if rid in state:
                row, bid = state[rid]
                conn.execute(f"UPDATE {table} SET {', '.join(c + ' = %s' for c in cols)}, batch_id = %s WHERE {key} = %s",
                             [row[c] for c in cols] + [bid, rid])
            else:
                conn.execute(f"DELETE FROM {table} WHERE {key} = %s", (rid,))
    n = conn.execute("DELETE FROM batches WHERE batch_date >= %s", (kit.SIM_START,)).rowcount
print(f"Removed {n} simulation batches and everything written about them. History is untouched.")
print("Next: python simulator.py step   (one day with the do-nothing agent)")
