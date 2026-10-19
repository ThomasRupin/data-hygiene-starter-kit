"""Shared settings and helpers for the starter kit scripts."""
import csv
import hashlib
import json
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("Python 3.11 or newer is needed (3.13 recommended). Check with: python --version")
if sys.version_info >= (3, 15):
    print("Warning: Python 3.15 is not supported yet (some packages have no 3.15 build). Use 3.13.", file=sys.stderr)
# Never crash on a character the console cannot show (e.g. a Windows console and text from an LLM).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent


def _read_env():
    """Settings from a .env file next to this one (Docker Compose reads the same file)."""
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Windows editors add a BOM
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = {**_read_env(), **os.environ}
# Set by the simulator when it runs your agent: the agent connects as 'agent'.
AGENT_MODE = ENV.get("WATCHDOG_AGENT_MODE") == "1"

DATA = ROOT / "data"
HISTORY = DATA / "history"              # 90 days of clean history, loaded into the core tables
LANDING = DATA / "landing"              # the 28 simulation days of the active scenario, as delivered
CALENDAR = DATA / "calendar.csv"        # trading events the retailer knows about in advance
ACTIVE = DATA / "scenario.json"         # which scenario is active
SCENARIOS = ROOT / "scenarios"          # ready-made scenarios: python scenario.py

SIM_START = date(2026, 11, 2)           # a Monday
HISTORY_DAYS = 90
SIM_DAYS = 28
DATA_SEED = 42                          # the scenarios are built on the data generated with this seed
PG_PORT = int(ENV.get("PG_PORT", 15432))

# feed -> (target table, columns as delivered)
FEEDS = {
    "crm": ("customers", ["customer_id", "name", "email", "phone", "postcode", "created_at"]),
    "pim": ("products", ["sku_id", "name", "category", "unit_price", "barcode"]),
    "pos": ("orders", ["order_id", "store_id", "customer_id", "sku_id", "quantity", "unit_price", "order_ts"]),
    "web": ("orders", ["order_id", "customer_id", "customer_email", "sku_id", "quantity", "unit_price", "order_ts"]),
}
# Feeds of the same day are delivered in this order, so orders can refer to customers and products.
FEED_ORDER = ["pim", "crm", "pos", "web"]


def history_dates():
    return [SIM_START - timedelta(days=HISTORY_DAYS - i) for i in range(HISTORY_DAYS)]


def sim_dates():
    return [SIM_START + timedelta(days=i) for i in range(SIM_DAYS)]


def feed_file(base, feed, day):
    return base / feed / f"{day.isoformat()}.csv"


def schema_hash(columns):
    return hashlib.sha1(",".join(columns).encode()).hexdigest()[:12]


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


class DatabaseUnavailable(RuntimeError):
    pass


def connect(user=None, password=None):
    """A connection to the kit's database. Your agent code gets the restricted 'agent' login by default."""
    import psycopg  # imported here so the file-only steps run without a database driver
    default_user = "agent" if AGENT_MODE else "watchdog"
    host = ENV.get("PGHOST", "localhost")
    try:
        return psycopg.connect(
            host=host, port=PG_PORT,
            user=user or ENV.get("PGUSER", default_user), password=password or ENV.get("PGPASSWORD", default_user),
            dbname=ENV.get("PGDATABASE", "retail"), connect_timeout=5)
    except psycopg.OperationalError as e:
        raise DatabaseUnavailable(
            f"Cannot connect to PostgreSQL on {host}:{PG_PORT}.\n"
            f"Is Docker running and did 'docker compose up -d --wait' succeed? "
            f"See Troubleshooting in README.md.\n\n{e}") from None


def connect_or_exit(user=None, password=None):
    try:
        return connect(user, password)
    except DatabaseUnavailable as e:
        sys.exit(str(e))


def clear_dir(path):
    """Delete a generated folder; on Windows a file open in Excel cannot be deleted."""
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except PermissionError as e:
        sys.exit(f"Cannot delete {e.filename}: is it open in Excel or another program? Close it and run again.")


def apply_schema(conn):
    """Create any missing tables. Safe to run again: everything is IF NOT EXISTS."""
    conn.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))


# ---------- scenario bundles ----------
# A scenario is a zip: the files the feeds deliver (landing/...) plus what the simulator scores with
# (truth.bin). truth.bin is packed so it is not readable by accident; it is not a secret, and the final
# session uses a scenario you have not seen.
_PAD = b"data-hygiene-watchdog/scenario"


def pack_truth(obj):
    import zlib
    raw = zlib.compress(json.dumps(obj).encode("utf-8"), 9)
    return bytes(b ^ _PAD[i % len(_PAD)] for i, b in enumerate(raw))


def unpack_truth(blob):
    import zlib
    raw = bytes(b ^ _PAD[i % len(_PAD)] for i, b in enumerate(blob))
    return json.loads(zlib.decompress(raw).decode("utf-8"))


def active_scenario():
    """(name, bundle path) of the active scenario, or (None, None)."""
    if not ACTIVE.exists():
        return None, None
    a = json.loads(ACTIVE.read_text(encoding="utf-8"))
    return a["name"], Path(a["bundle"])
