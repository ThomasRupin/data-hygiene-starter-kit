"""Profile batches: what each column looks like, next to what it normally looks like.

For every column of a batch: null rate, distinct count, minimum, maximum and format match
(the share of non-empty values that match the column's expected format, below). The baseline
is the average over the rows of the same feed that reached the core tables in the 30 days
before, so quarantined or lost rows never become part of "normal".

The simulator runs this for every new batch; your agent reads the results in column_profiles.

  python profiler.py --day 3       print the profiles of simulation day 3
  python profiler.py 412           print the profile of batch 412
"""
import argparse
import re
from datetime import timedelta

import kit

# The expected format of every column the feeds normally send. A column not listed here
# (for example one that was renamed or added) gets no format match: that is itself a signal.
FORMATS = {
    "order_id": r"[PW]\d{13}",
    "store_id": r"S\d{2}",
    "customer_id": r"C\d{6}",
    "sku_id": r"SKU-\d{5}",
    "quantity": r"[1-9]\d*",
    "unit_price": r"\d+\.\d{2}",
    "order_ts": r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
    "created_at": r"\d{4}-\d{2}-\d{2}",
    "email": r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
    "customer_email": r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
    "name": r"[A-Za-z][A-Za-z'.-]*( [A-Za-z][A-Za-z'.-]*)+",   # words separated by single spaces
    "phone": r"0\d{2,4} \d{3,4} ?\d{3,4}",                       # UK, with spaces: 07700 900123, 0113 496 0123
    "postcode": r"[A-Z]{1,2}\d{1,2} \d[A-Z]{2}",                 # UK, upper case, one space: LS1 4AB
    "category": r"Womenswear|Menswear|Footwear|Accessories|Home|Beauty",
    "barcode": r"\d{13}",
}
FORMATS = {k: re.compile(v) for k, v in FORMATS.items()}


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profile_rows(rows):
    """Profile a list of records (dicts). An empty string counts as missing, like null."""
    columns = []
    for row in rows:
        columns += [c for c in row if c not in columns]
    out = {}
    for col in columns:
        values = [row.get(col) for row in rows]
        present = [str(v) for v in values if v not in (None, "")]
        nums = [_number(v) for v in present]
        if present and all(n is not None for n in nums):
            lo, hi = min(nums), max(nums)
            lo, hi = (int(lo), int(hi)) if lo.is_integer() and hi.is_integer() else (lo, hi)
        else:
            lo, hi = (min(present), max(present)) if present else (None, None)
        fmt = FORMATS.get(col)
        out[col] = {
            "null_rate": round(1 - len(present) / len(values), 4) if values else None,
            "distinct_count": len(set(present)),
            "min_value": None if lo is None else str(lo),
            "max_value": None if hi is None else str(hi),
            "format_match": (round(sum(bool(fmt.fullmatch(v)) for v in present) / len(present), 4)
                             if fmt and present else None),
        }
    return out


def save_promoted(cur, batch_id, profile):
    """Store the null rate and format match of the rows of a batch that reached the core tables."""
    for col, p in profile.items():
        cur.execute("UPDATE column_profiles SET promoted_null_rate = %s, promoted_format_match = %s"
                    " WHERE batch_id = %s AND column_name = %s", (p["null_rate"], p["format_match"], batch_id, col))


def profile_promoted(cur, batch_id, table):
    cur.execute(f"SELECT payload FROM staging_{table} WHERE batch_id = %s AND status = 'promoted'", (batch_id,))
    rows = [r[0] for r in cur.fetchall()]
    if rows:
        save_promoted(cur, batch_id, profile_rows(rows))


def save(cur, batch_id, profile, baseline=None):
    baseline = baseline or {}
    for col, p in profile.items():
        b = baseline.get(col, (None, None))
        cur.execute(
            "INSERT INTO column_profiles (batch_id, column_name, null_rate, distinct_count, min_value, max_value,"
            " format_match, baseline_null_rate, baseline_format_match) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (batch_id, column_name) DO UPDATE SET null_rate = EXCLUDED.null_rate,"
            " distinct_count = EXCLUDED.distinct_count, min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value,"
            " format_match = EXCLUDED.format_match, baseline_null_rate = EXCLUDED.baseline_null_rate,"
            " baseline_format_match = EXCLUDED.baseline_format_match",
            (batch_id, col, p["null_rate"], p["distinct_count"], p["min_value"], p["max_value"], p["format_match"],
             b[0], b[1]))


def baseline(cur, feed, day):
    """Average null rate and format match per column over the rows of the feed promoted in the last 30 days."""
    cur.execute(
        "SELECT p.column_name, avg(p.promoted_null_rate), avg(p.promoted_format_match) FROM column_profiles p"
        " JOIN batches b USING (batch_id) WHERE b.feed_name = %s AND p.promoted_null_rate IS NOT NULL"
        " AND b.batch_date >= %s AND b.batch_date < %s GROUP BY p.column_name",
        (feed, day - timedelta(days=30), day))
    return {c: (None if n is None else round(float(n), 4), None if f is None else round(float(f), 4))
            for c, n, f in cur.fetchall()}


def profile_batch(cur, batch_id):
    """Profile one delivered batch from staging and store it, with its baseline, in column_profiles."""
    cur.execute("SELECT feed_name, target_table, batch_date, status FROM batches WHERE batch_id = %s", (batch_id,))
    feed, table, day, status = cur.fetchone()
    if status == "missing":
        return {}
    cur.execute(f"SELECT payload FROM staging_{table} WHERE batch_id = %s ORDER BY row_num", (batch_id,))
    prof = profile_rows([r[0] for r in cur.fetchall()])
    save(cur, batch_id, prof, baseline(cur, feed, day))
    return prof


def profile_history(conn):
    """Profile the clean history files, so baselines exist from the first simulation day."""
    with conn.cursor() as cur:
        cur.execute("SELECT batch_id, feed_name, batch_date FROM batches WHERE batch_date < %s", (kit.SIM_START,))
        for bid, feed, day in cur.fetchall():
            prof = profile_rows(kit.read_csv(kit.feed_file(kit.HISTORY, feed, day))[1])
            save(cur, bid, prof)
            save_promoted(cur, bid, prof)  # the history is clean: every row reached the core tables
    conn.commit()


def show(cur, batch_id):
    cur.execute("SELECT feed_name, batch_date, row_count, status FROM batches WHERE batch_id = %s", (batch_id,))
    feed, day, n, status = cur.fetchone()
    print(f"\nbatch {batch_id}: {feed} {day}, {n} rows, {status}")
    print(f"  {'column':16} {'null':>6} {'base':>6} {'format':>7} {'base':>6} {'distinct':>8}  min .. max")
    cur.execute("SELECT column_name, null_rate, baseline_null_rate, format_match, baseline_format_match,"
                " distinct_count, min_value, max_value FROM column_profiles WHERE batch_id = %s", (batch_id,))
    f = lambda v: "" if v is None else f"{float(v):.2f}"
    for c, nr, bn, fm, bf, dc, lo, hi in cur.fetchall():
        print(f"  {c:16} {f(nr):>6} {f(bn):>6} {f(fm):>7} {f(bf):>6} {dc:>8}  {lo} .. {hi}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch_id", nargs="?", type=int)
    ap.add_argument("--day", type=int, help="simulation day number, 1 to 28")
    args = ap.parse_args()
    with kit.connect_or_exit() as conn, conn.cursor() as cur:
        if args.day:
            cur.execute("SELECT batch_id FROM batches WHERE batch_date = %s ORDER BY batch_id",
                        (kit.SIM_START + timedelta(days=args.day - 1),))
            ids = [r[0] for r in cur.fetchall()]
            if not ids:
                raise SystemExit(f"Day {args.day} has not been delivered yet.")
        elif args.batch_id:
            ids = [args.batch_id]
        else:
            raise SystemExit("Give a batch id or --day N")
        for bid in ids:
            show(cur, bid)


if __name__ == "__main__":
    main()
