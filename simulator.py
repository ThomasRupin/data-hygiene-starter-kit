"""Run the 28 simulation days, calling your agent once a day.

  python simulator.py step                          run the next day with the do-nothing agent
  python simulator.py step --agent my_agent:run_day
  python simulator.py run --agent my_agent:run_day  run every remaining day (or --days N)
  python simulator.py resume --agent my_agent:run_day   finish a day that was interrupted
  python simulator.py report                        the results report so far

Each day, in this order:
  1. Morning: the data steward decides on the fixes awaiting approval from earlier days, and applies
     the ones a person approved in your UI. Approved fixes are applied to the staging row; rejected ones
     get a rejection_reason, which your agent can read the same day.
  2. The day's batches are delivered into staging (raw JSON); a feed with no file is 'missing'.
  3. Every new batch is profiled into column_profiles, next to its 30-day baseline.
  4. Your agent's  async def run_day(day)  runs once, in its own process, logged in as 'agent'.
  5. Every staging row still 'pending' or 'clean' is converted to the core types and promoted.
     Rows that fail the conversion or point to something unknown become 'lost'.
  6. A line is added to the results report (sim_results).
After day 28 one last morning runs, so fixes proposed on the last day are decided too.

What your agent writes, and what the simulator does with it: see README.md, "Contract with the simulator".
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal

import kit
import profiler

KEY = {"orders": "order_id", "customers": "customer_id", "products": "sku_id"}
COMPARE = {  # core columns compared with the true (clean) values for data health
    "orders": ["store_id", "customer_id", "sku_id", "quantity", "unit_price", "order_ts"],
    "customers": ["name", "email", "phone", "postcode"],
    "products": ["name", "category", "unit_price", "barcode"],
}
FEED_OF = {"customers": ["crm"], "products": ["pim"], "orders": ["pos", "web"]}


# ---------------------------------------------------------------- truth
class Truth:
    """What the simulator scores with: the true rows and the planted problems, from the active scenario."""

    def __init__(self):
        name, bundle = kit.active_scenario()
        self.ok = bool(bundle and bundle.exists())
        t = {}
        if self.ok:
            with zipfile.ZipFile(bundle) as z:
                t = kit.unpack_truth(z.read("truth.bin"))
        self._clean = t.get("clean", {})
        self.problems = t.get("problems", [])
        self.expected_promote = {tuple(x) for x in t.get("expected_promote", [])}
        self.unrecoverable = {tuple(x) for x in t.get("unrecoverable", [])}
        self.may_quarantine = {tuple(x) for x in t.get("may_quarantine", [])}
        self.excused = {tuple(x) for x in t.get("excused", [])}
        self.duplicates = t.get("duplicates", {})
        self._index = {}

    def clean(self, feed, day):
        """{business id: row} and the column names of the true file for one feed and day."""
        if (feed, day) not in self._index:
            c = self._clean.get(feed, {}).get(day.isoformat(), {"cols": [], "rows": []})
            self._index[(feed, day)] = ({r[KEY[kit.FEEDS[feed][0]]]: r for r in c["rows"]}, c["cols"])
        return self._index[(feed, day)]

    def clean_row(self, feed, day, rid):
        """The true row and the day it belongs to (late lines belong to the day before)."""
        for d in (day, day - timedelta(days=1)):
            row = self.clean(feed, d)[0].get(rid)
            if row:
                return row, d
        return None, None

    def problems_on(self, day):
        return [p for p in self.problems if day.isoformat() in p["dates"]]


def same(a, b):
    a = None if a in (None, "") else str(a).strip()
    b = None if b in (None, "") else str(b).strip()
    if a is None or b is None:
        return a == b
    try:
        return abs(float(a) - float(b)) < 0.005
    except ValueError:
        return a == b


# ---------------------------------------------------------------- judging fixes
def judge(cur, truth, fix):
    """Is this fix right? Returns (verdict, reason): True, False, or None when there is nothing to judge
    (the fix changes nothing, or the true value cannot be known from the data)."""
    fix_id, fix_type, batch_id, table, column, record_id, before, after = fix
    if table not in KEY:
        return False, "table_name must be orders, customers or products."
    cur.execute("SELECT feed_name, batch_date FROM batches WHERE batch_id = %s", (batch_id,))
    got = cur.fetchone()
    if not got:
        return False, "the fix has no valid batch_id."
    feed, day = got
    if feed not in FEED_OF[table]:
        return False, f"batch {batch_id} does not hold {table}."
    if fix_type == "map_column":
        if not before or not after:
            return False, "a column mapping needs before_value (delivered name) and after_value (usual name)."
        cur.execute(f"SELECT original FROM staging_{table} WHERE batch_id = %s LIMIT 1", (batch_id,))
        delivered = list((cur.fetchone() or [{}])[0] or {})
        clean_cols = truth.clean(feed, day)[1] if truth.ok else [after]
        right = before in delivered and after not in delivered and after in clean_cols and before not in clean_cols
        return right, None if right else f"'{before}' is not the column '{after}' in this batch."
    if not re.fullmatch(r"[0-9]+", str(record_id or "")):
        return False, "record_id must be the row_num of the staging row."
    if not column or after is None:
        return False, "a fix needs column_name and after_value."
    cur.execute(f"SELECT original FROM staging_{table} WHERE batch_id = %s AND row_num = %s", (batch_id, int(record_id)))
    got = cur.fetchone()
    if not got:
        return False, f"there is no row {record_id} in batch {batch_id}."
    original = got[0]
    rid = original.get(KEY[table])
    if fix_type == "merge":
        right = truth.duplicates.get(rid) == after if truth.ok else True
        return right, None if right else f"{rid} and {after} are different customers."
    if same(original.get(column), after):
        return None, None  # changes nothing
    if (day.isoformat(), rid, column) in truth.unrecoverable:
        return None, None  # the true value is not in the data: not judged
    if not truth.ok:
        return True, None
    clean, _ = truth.clean_row(feed, day, rid)
    right = clean is not None and same(clean.get(column), after)
    return right, None if right else f"'{after}' is not the right {column} for {rid}."


def apply_fix(cur, fix):
    fix_id, fix_type, batch_id, table, column, record_id, before, after = fix
    if fix_type == "map_column":
        cur.execute(f"UPDATE staging_{table} SET payload = (payload - %s::text)"
                    " || jsonb_build_object(%s::text, payload -> %s::text)"
                    " WHERE batch_id = %s AND payload ? %s::text", (before, after, before, batch_id, before))
    elif fix_type != "merge":  # a merge is applied at promotion: the duplicate is not promoted
        cur.execute(f"UPDATE staging_{table} SET payload = jsonb_set(payload, %s::text[], to_jsonb(%s::text), true)"
                    " WHERE batch_id = %s AND row_num = %s", ([column], after, batch_id, int(record_id)))


FIX_COLS = "f.fix_id, f.fix_type, f.batch_id, f.table_name, f.column_name, f.record_id, f.before_value, f.after_value"


# ---------------------------------------------------------------- 1. morning: the steward
def steward(conn, truth, day):
    """Decide on the fixes awaiting approval from earlier days; apply the ones a person approved."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {FIX_COLS}, f.status FROM fixes f LEFT JOIN batches b USING (batch_id)"
                    " WHERE (f.status = 'awaiting_approval' AND (b.batch_date IS NULL OR b.batch_date < %s))"
                    " OR (f.status IN ('approved', 'rejected') AND f.decided_on IS NULL)"
                    " OR (f.status = 'approved' AND f.applied_on IS NULL)"
                    " ORDER BY f.fix_id", (day,))
        todo = cur.fetchall()
    for *fix, status in todo:
        fix = tuple(fix)
        try:
            with conn.transaction(), conn.cursor() as cur:
                if status == "awaiting_approval":
                    row_status = None
                    if fix[3] in KEY and re.fullmatch(r"[0-9]+", str(fix[5] or "")):
                        cur.execute(f"SELECT status FROM staging_{fix[3]} WHERE batch_id = %s AND row_num = %s",
                                    (fix[2], int(fix[5])))
                        row_status = (cur.fetchone() or [None])[0]
                    if row_status in ("promoted", "merged", "lost"):
                        right, reason = False, "the row was already processed: propose fixes before the day ends."
                    else:
                        right, reason = judge(cur, truth, fix)
                        right = right is not False  # a fix that changes nothing is harmless: approve it
                    if right:
                        apply_fix(cur, fix)
                        cur.execute("UPDATE fixes SET status = 'approved', decided_by = 'steward', decided_on = %s,"
                                    " applied_on = %s WHERE fix_id = %s", (day, day, fix[0]))
                    else:
                        cur.execute("UPDATE fixes SET status = 'rejected', decided_by = 'steward', decided_on = %s,"
                                    " rejection_reason = %s WHERE fix_id = %s", (day, f"Rejected: {reason}", fix[0]))
                elif status == "approved":  # a person approved it in the UI: apply it as it is
                    apply_fix(cur, fix)
                    cur.execute("UPDATE fixes SET decided_on = coalesce(decided_on, %s), applied_on = %s"
                                " WHERE fix_id = %s", (day, day, fix[0]))
                else:  # a person rejected it in the UI
                    cur.execute("UPDATE fixes SET decided_on = %s WHERE fix_id = %s", (day, fix[0]))
        except Exception as e:  # a malformed fix must never stop the simulation
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("UPDATE fixes SET status = 'rejected', decided_by = 'steward', decided_on = %s,"
                            " applied_on = NULL, rejection_reason = %s WHERE fix_id = %s",
                            (day, f"Rejected: the fix could not be applied ({str(e).splitlines()[0][:200]}).", fix[0]))
    # Rows no longer waiting for any fix go back to 'pending', and are promoted tonight.
    with conn.transaction(), conn.cursor() as cur:
        for table in KEY:
            cur.execute(f"UPDATE staging_{table} s SET status = 'pending' FROM batches b"
                        " WHERE b.batch_id = s.batch_id AND b.batch_date < %s AND s.status = 'awaiting_approval'"
                        " AND NOT EXISTS (SELECT 1 FROM fixes f WHERE f.batch_id = s.batch_id"
                        " AND f.status = 'awaiting_approval' AND (f.record_id = s.row_num::text OR f.record_id IS NULL))",
                        (day,))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE status = 'approved'), count(*) FILTER (WHERE status = 'rejected')"
                    " FROM fixes WHERE decided_on = %s", (day,))
        return cur.fetchone()


# ---------------------------------------------------------------- 2. delivery
def deliver(cur, day):
    """Load the day's feed files into staging, one batch per feed."""
    for feed in kit.FEED_ORDER:
        table = kit.FEEDS[feed][0]
        path = kit.feed_file(kit.LANDING, feed, day)
        if not path.exists():
            cur.execute("INSERT INTO batches (feed_name, target_table, batch_date, row_count, status)"
                        " VALUES (%s, %s, %s, 0, 'missing') RETURNING batch_id", (feed, table, day))
            print(f"  {feed}: no file arrived (batch {cur.fetchone()[0]}, missing)")
            continue
        cols, rows = kit.read_csv(path)
        cur.execute("INSERT INTO batches (feed_name, target_table, batch_date, row_count, schema_hash)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING batch_id",
                    (feed, table, day, len(rows), kit.schema_hash(cols)))
        bid = cur.fetchone()[0]
        with cur.copy(f"COPY staging_{table} (batch_id, row_num, payload, original) FROM STDIN") as cp:
            for i, row in enumerate(rows, 1):
                payload = json.dumps({k: (v if v != "" else None) for k, v in row.items()})
                cp.write_row([bid, i, payload, payload])
        print(f"  {feed}: {len(rows)} rows (batch {bid})")


# ---------------------------------------------------------------- 4. the agent
RUNNER = """
import asyncio, importlib, sys
from datetime import date
sys.path[:0] = [sys.argv[3], sys.argv[4]]
module, _, name = sys.argv[1].partition(":")
run_day = getattr(importlib.import_module(module), name or "run_day")
asyncio.run(run_day(date.fromisoformat(sys.argv[2])))
"""


def run_agent(agent, day, timeout):
    """Run the agent in its own process: logged in as 'agent', without the instructor folder, with a hard
    time limit. Returns an error message, or None."""
    if callable(agent):  # called from your own code with a function: runs in this process, not isolated
        try:
            asyncio.run(asyncio.wait_for(agent(day), timeout))
            return None
        except (Exception, SystemExit) as e:
            return f"{type(e).__name__}: {e}"
    env = {k: v for k, v in os.environ.items() if k not in ("PGUSER", "PGPASSWORD")}
    env.update({"WATCHDOG_AGENT_MODE": "1", "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "-c", RUNNER, agent, day.isoformat(), os.getcwd(), str(kit.ROOT)],
                            env=env, cwd=os.getcwd())
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return f"timed out after {timeout}s"
    except KeyboardInterrupt:
        proc.kill()
        raise
    return None if code == 0 else f"the agent stopped with exit code {code} (see its error above)"


# ---------------------------------------------------------------- 5. promotion
def _price(v):
    v = None if v is None else str(v).strip()
    if v is None or not re.fullmatch(r"[0-9]+(\.[0-9]+)?", v):
        raise ValueError(f"unit_price {v!r} is not a price in pounds")
    return Decimal(v)


def _row_values(table, feed, p):
    g = lambda c: None if p.get(c) in (None, "") else str(p.get(c)).strip()
    if table == "orders":
        if not g("order_id") or not g("sku_id"):
            raise ValueError("order_id or sku_id missing")
        q = g("quantity")
        if q is None or not re.fullmatch(r"[0-9]+", q) or int(q) < 1:
            raise ValueError(f"quantity {q!r} is not a whole number of items")
        try:
            ts = datetime.strptime(g("order_ts") or "", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError(f"order_ts {g('order_ts')!r} is not yyyy-mm-dd HH:MM:SS")
        return [g("order_id"), feed, g("store_id"), g("customer_id"), g("customer_email"), g("sku_id"),
                int(q), _price(g("unit_price")), ts]
    if table == "customers":
        if not g("customer_id"):
            raise ValueError("customer_id missing")
        try:
            created = datetime.strptime(g("created_at"), "%Y-%m-%d").date() if g("created_at") else None
        except ValueError:
            raise ValueError(f"created_at {g('created_at')!r} is not yyyy-mm-dd")
        return [g("customer_id"), g("name"), g("email"), g("phone"), g("postcode"), created]
    if not g("sku_id"):
        raise ValueError("sku_id missing")
    return [g("sku_id"), g("name"), g("category"), _price(g("unit_price")), g("barcode")]


SQL = {
    "orders": "INSERT INTO orders (order_id, channel, store_id, customer_id, customer_email, sku_id, quantity,"
              " unit_price, order_ts, batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "customers": "INSERT INTO customers (customer_id, name, email, phone, postcode, created_at, batch_id)"
                 " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (customer_id) DO UPDATE SET name = EXCLUDED.name,"
                 " email = EXCLUDED.email, phone = EXCLUDED.phone, postcode = EXCLUDED.postcode,"
                 " created_at = coalesce(customers.created_at, EXCLUDED.created_at), batch_id = EXCLUDED.batch_id",
    "products": "INSERT INTO products (sku_id, name, category, unit_price, barcode, batch_id)"
                " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (sku_id) DO UPDATE SET name = EXCLUDED.name,"
                " category = EXCLUDED.category, unit_price = EXCLUDED.unit_price, barcode = EXCLUDED.barcode,"
                " batch_id = EXCLUDED.batch_id",
}


def promote(conn):
    """Promote every pending or clean staging row of batches that were not handed off or missing."""
    counts = {"promoted": 0, "lost": 0, "merged": 0}
    promoted, touched = [], set()  # promoted: (table, feed, batch date, business id)
    with conn.cursor() as cur:
        # An agent that left a transaction open must not block promotion.
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'agent'"
                    " AND pid <> pg_backend_pid()")
        cur.execute("SET lock_timeout = '10s'")
        cur.execute("UPDATE batches SET status = 'handed_off' WHERE status IN ('landed', 'missing')"
                    " AND batch_id IN (SELECT batch_id FROM hand_offs)")
        for table in ("products", "customers", "orders"):
            cur.execute(f"SELECT s.batch_id, s.row_num, s.payload, b.feed_name, b.batch_date FROM staging_{table} s"
                        " JOIN batches b USING (batch_id) WHERE b.status IN ('landed', 'promoted')"
                        " AND s.status IN ('pending', 'clean') ORDER BY b.batch_date, s.batch_id, s.row_num")
            for bid, row_num, payload, feed, bdate in cur.fetchall():
                touched.add((bid, table))
                cur.execute("SELECT 1 FROM fixes WHERE batch_id = %s AND record_id = %s AND fix_type = 'merge'"
                            " AND (status = 'applied' OR (status = 'approved' AND applied_on IS NOT NULL))",
                            (bid, str(row_num)))
                if table == "customers" and cur.fetchone():
                    status, reason = "merged", "merged into an existing customer"
                else:
                    try:
                        with conn.transaction():
                            cur.execute(SQL[table], _row_values(table, feed, payload) + [bid])
                        status, reason = "promoted", None
                        promoted.append((table, feed, bdate, payload.get(KEY[table])))
                    except Exception as e:  # conversion or constraint failure: the row is lost
                        status, reason = "lost", str(e).splitlines()[0][:300]
                cur.execute(f"UPDATE staging_{table} SET status = %s, reason = coalesce(%s, reason)"
                            " WHERE batch_id = %s AND row_num = %s", (status, reason, bid, row_num))
                counts[status] += 1
        for table in KEY:
            cur.execute(f"UPDATE batches b SET status = 'promoted' WHERE b.status = 'landed' AND b.target_table = %s"
                        f" AND NOT EXISTS (SELECT 1 FROM staging_{table} s WHERE s.batch_id = b.batch_id"
                        " AND s.status IN ('pending', 'clean', 'awaiting_approval'))", (table,))
        for bid, table in touched:  # baselines only ever learn from rows that reached the core tables
            profiler.profile_promoted(cur, bid, table)
        cur.execute("RESET lock_timeout")
    conn.commit()
    return counts, promoted


# ---------------------------------------------------------------- 6. results
def results(conn, truth, day, promoted, counts, agent_error, last_morning=None):
    days = [day] + ([last_morning] if last_morning else [])
    with conn.cursor() as cur:
        problems = truth.problems_on(day)
        cur.execute("SELECT b.feed_name FROM issues i JOIN batches b USING (batch_id) WHERE b.batch_date = %s"
                    " UNION ALL SELECT b.feed_name FROM hand_offs h JOIN batches b USING (batch_id)"
                    " WHERE b.batch_date = %s", (day, day))
        alarms = [r[0] for r in cur.fetchall()]
        caught = [p["feed"] for p in problems if p["feed"] in alarms]
        missed = [p["feed"] for p in problems if p["feed"] not in alarms]
        expected = {feed for d, feed in truth.expected_promote if d == day.isoformat()}
        false_alarms = sum(1 for feed in alarms if feed not in {p["feed"] for p in problems} | expected)

        # Fixes the agent applied itself today (judged tonight) and the steward's decisions this morning.
        cur.execute(f"SELECT {FIX_COLS} FROM fixes f JOIN batches b USING (batch_id)"
                    " WHERE f.status = 'applied' AND b.batch_date = %s", (day,))
        verdicts = [judge(cur, truth, fx)[0] for fx in cur.fetchall()]
        cur.execute(f"SELECT {FIX_COLS} FROM fixes f WHERE f.decided_on = ANY(%s)", (days,))
        verdicts += [judge(cur, truth, fx)[0] for fx in cur.fetchall()]  # a wrong proposal counts as wrong
        verdicts = [v for v in verdicts if v is not None]

        # Data health: of the rows that should have reached the core tables today, the share that did,
        # with their true values. Rows that should not exist (e.g. bot orders) count against it. Rows held
        # for approval, rows of a missing or handed-off batch, rightly quarantined rows and values the data
        # cannot tell (unrecoverable) are left out until they land.
        cur.execute("SELECT feed_name FROM batches WHERE batch_date = %s AND status IN ('missing', 'handed_off')", (day,))
        absent = {r[0] for r in cur.fetchall()}
        held = set()
        for table in KEY:
            cur.execute(f"SELECT b.feed_name, s.original ->> %s, s.status FROM staging_{table} s JOIN batches b"
                        " USING (batch_id) WHERE b.batch_date = %s AND s.status IN ('awaiting_approval', 'quarantined')",
                        (KEY[table], day))
            for feed, rid, status in cur.fetchall():
                if status == "awaiting_approval" or (day.isoformat(), rid) in truth.may_quarantine:
                    held.add((feed, rid))
        landed = {(feed, rid) for table, feed, bdate, rid in promoted}
        should = sum(1 for feed in kit.FEEDS if feed not in absent for rid in truth.clean(feed, day)[0]
                     if (feed, rid) not in held
                     and ((day.isoformat(), rid) not in truth.excused or (feed, rid) in landed))
        good = junk = wrong = 0
        seen = set()
        for table, feed, bdate, rid in promoted:
            if (table, rid) in seen:
                continue
            seen.add((table, rid))
            clean, origin = truth.clean_row(feed, bdate, rid)
            if clean is None:
                junk += 1
                continue
            if origin != day:
                should += 1  # it arrived late or waited for approval: it counts on the day it lands
            cur.execute(f"SELECT {', '.join(COMPARE[table])} FROM {table} WHERE {KEY[table]} = %s", (rid,))
            core = cur.fetchone()
            ok = all(same(c.strftime('%Y-%m-%d %H:%M:%S') if isinstance(c, datetime) else c, clean.get(col))
                     for col, c in zip(COMPARE[table], core)
                     if col in clean and (origin.isoformat(), rid, col) not in truth.unrecoverable)
            good, wrong = (good + 1, wrong) if ok else (good, wrong + 1)
        health = round(min(1, max(0, good - junk) / should), 4) if should else None
        details = {"problems_today": len(problems), "caught": caught, "missed": missed,
                   "rows_promoted": counts["promoted"], "rows_lost": counts["lost"], "rows_merged": counts["merged"],
                   "rows_expected": should, "rows_correct": good, "rows_wrong": wrong,
                   "rows_that_should_not_exist": junk, "agent_error": agent_error}
        cur.execute("INSERT INTO sim_results (sim_day, caught, fixed_correctly, missed, wrongly_changed, false_alarms,"
                    " data_health, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (sim_day) DO UPDATE SET"
                    " caught = EXCLUDED.caught, fixed_correctly = EXCLUDED.fixed_correctly, missed = EXCLUDED.missed,"
                    " wrongly_changed = EXCLUDED.wrongly_changed, false_alarms = EXCLUDED.false_alarms,"
                    " data_health = EXCLUDED.data_health, details = EXCLUDED.details",
                    (day, len(caught), sum(verdicts), len(missed), len(verdicts) - sum(verdicts), false_alarms,
                     health, json.dumps(details)))
    conn.commit()
    return details, health, sum(verdicts), len(verdicts) - sum(verdicts), false_alarms


# ---------------------------------------------------------------- the day loop
def state(cur):
    """(next day number, True if that day was delivered but not finished)."""
    cur.execute("SELECT max(batch_date) FROM batches WHERE batch_date >= %s", (kit.SIM_START,))
    last = cur.fetchone()[0]
    if last is None:
        return 1, False
    cur.execute("SELECT 1 FROM sim_results WHERE sim_day = %s", (last,))
    n = (last - kit.SIM_START).days + 1
    return (n + 1, False) if cur.fetchone() else (n, True)


def step(agent="noop_agent:run_day", timeout=900, resume=False):
    """Run the next simulation day (or finish an interrupted one with resume=True).
    Returns the day number run, or None when all days are done."""
    if not kit.LANDING.exists() or not kit.active_scenario()[0]:
        raise SystemExit("No scenario chosen yet: run python scenario.py dev first.")
    truth = Truth()
    with kit.connect() as conn:
        kit.apply_schema(conn)
        conn.commit()
        with conn.cursor() as cur:
            n, unfinished = state(cur)
        if unfinished and not resume:
            name = agent if isinstance(agent, str) else "your_module:run_day"
            raise SystemExit(f"Day {n} was delivered but never finished (stopped halfway?). Finish it with:\n"
                             f"  python simulator.py resume --agent {name}\n"
                             "or start the 28 days again with: python reset_sim.py")
        if resume and not unfinished:
            raise SystemExit("There is no interrupted day to resume.")
        if n > kit.SIM_DAYS:
            return None
        day = kit.sim_dates()[n - 1]
        print(f"Day {n} ({day:%a %d %b}){' resumed' if resume else ''}:")
        if not resume:
            approved, rejected = steward(conn, truth, day)
            if approved or rejected:
                print(f"  steward: {approved} approved, {rejected} rejected")
            with conn.cursor() as cur:
                deliver(cur, day)
                cur.execute("SELECT batch_id FROM batches WHERE batch_date = %s", (day,))
                for (bid,) in cur.fetchall():
                    profiler.profile_batch(cur, bid)
            conn.commit()

        t0 = time.time()
        agent_error = run_agent(agent, day, timeout)
        print(f"  agent {'failed: ' + agent_error if agent_error else 'ran'} ({time.time() - t0:.1f}s)")

        counts, promoted = promote(conn)
        last_morning = None
        if n == kit.SIM_DAYS:  # one last morning, so fixes proposed on the last day are decided too
            last_morning = day + timedelta(days=1)
            approved, rejected = steward(conn, truth, last_morning)
            if approved or rejected:
                print(f"  final morning: steward {approved} approved, {rejected} rejected")
            more, promoted2 = promote(conn)
            counts = {k: counts[k] + more[k] for k in counts}
            promoted += promoted2
        print(f"  promoted {counts['promoted']} rows, lost {counts['lost']}, merged {counts['merged']}")
        if truth.ok:
            d, health, right, wrong, fa = results(conn, truth, day, promoted, counts, agent_error, last_morning)
            print(f"  results: caught {len(d['caught'])} of {d['problems_today']} problems, fixes right {right},"
                  f" wrong {wrong}, false alarms {fa}, data health {health}")
        else:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sim_results (sim_day, details) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (day, json.dumps({"note": "results not scored", "agent_error": agent_error})))
            conn.commit()
            print("  (no scenario truth available: results not scored)")
    return n


def report():
    with kit.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT sim_day, caught, missed, fixed_correctly, wrongly_changed, false_alarms, data_health,"
                    " details FROM sim_results ORDER BY sim_day")
        rows = cur.fetchall()
    if not rows:
        raise SystemExit("No simulated days yet: python simulator.py step")
    print(f"{'day':>3} {'date':10} {'caught':>6} {'missed':>6} {'fixes ok':>8} {'wrong':>5} {'f.alarm':>7} {'health':>6}"
          "  missed a problem on")
    tot, healths = [0, 0, 0, 0, 0], []
    for d, c, m, ok, w, fa, h, det in rows:
        vals = [c or 0, m or 0, ok or 0, w or 0, fa or 0]
        tot = [a + b for a, b in zip(tot, vals)]
        if h is not None:
            healths.append(float(h))
        err = "  (agent error)" if det.get("agent_error") else ""
        print(f"{(d - kit.SIM_START).days + 1:>3} {d.isoformat():10} {vals[0]:>6} {vals[1]:>6} {vals[2]:>8} {vals[3]:>5}"
              f" {vals[4]:>7} {'' if h is None else f'{float(h):.3f}':>6}  {', '.join(det.get('missed', []))}{err}")
    mean = f"{sum(healths) / len(healths):.3f}" if healths else ""
    print(f"{'':>3} {'total':10} {tot[0]:>6} {tot[1]:>6} {tot[2]:>8} {tot[3]:>5} {tot[4]:>7} {mean:>6}  (mean health)")
    print("caught and missed count each day a problem is present (a three-day problem counts three times).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["step", "run", "resume", "report"])
    ap.add_argument("--agent", default="noop_agent:run_day", help="module:function of your async run_day")
    ap.add_argument("--days", type=int, help="with run: how many days (default: all remaining)")
    ap.add_argument("--timeout", type=int, default=900, help="seconds your agent may take per day")
    args = ap.parse_args()
    try:
        if args.command == "report":
            return report()
        if args.command == "resume":
            step(args.agent, args.timeout, resume=True)
            return
        for _ in range((args.days or kit.SIM_DAYS) if args.command == "run" else 1):
            if step(args.agent, args.timeout) is None:
                print(f"All {kit.SIM_DAYS} days are done. See: python simulator.py report"
                      "  (start again: python reset_sim.py)")
                break
    except kit.DatabaseUnavailable as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
