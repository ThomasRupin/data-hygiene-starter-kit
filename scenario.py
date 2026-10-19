"""Choose the scenario for the 28 simulation days: which files the feeds will deliver.

  python scenario.py                    list the scenarios
  python scenario.py dev                the development scenario: start here
  python scenario.py practice           the same kinds of problem on other days and feeds, with other counts
  python scenario.py chaos              the development scenario plus the bonus problems
  python scenario.py --bundle FILE.zip  a scenario the teaching team gives you

Finding the problems in a scenario is your agent's job: this script does not tell you where they are.
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

import kit


def meta(bundle):
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("meta.json"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="dev, practice or chaos")
    ap.add_argument("--bundle", help="path to a scenario .zip")
    args = ap.parse_args()

    bundles = sorted(kit.SCENARIOS.glob("*.zip"))
    if not args.name and not args.bundle:
        active, _ = kit.active_scenario()
        for b in bundles:
            m = meta(b)
            print(f"  {b.stem:10} {m['description']}{'   (active)' if b.stem == active else ''}")
        print("\nChoose one with: python scenario.py dev")
        return
    bundle = Path(args.bundle) if args.bundle else kit.SCENARIOS / f"{args.name}.zip"
    if not bundle.exists():
        sys.exit(f"No scenario {bundle}. The scenarios are: {', '.join(b.stem for b in bundles)}")
    m = meta(bundle)

    with kit.connect_or_exit() as conn:
        try:
            history_rows = conn.execute("SELECT coalesce(sum(row_count), 0) FROM batches WHERE batch_date < %s",
                                        (kit.SIM_START,)).fetchone()[0]
            started = conn.execute("SELECT count(*) FROM batches WHERE batch_date >= %s",
                                   (kit.SIM_START,)).fetchone()[0]
        except Exception:
            sys.exit("The database has no tables yet: run python generate_data.py first.")
    if history_rows == 0:
        sys.exit("The database holds no history yet: run python generate_data.py first.")
    if history_rows != m["history_rows"]:
        sys.exit(f"This scenario was built on the data from  generate_data.py --seed {m['data_seed']},"
                 " but your database holds other data. Start from scratch (README, section 6).")

    kit.clear_dir(kit.LANDING)
    with zipfile.ZipFile(bundle) as z:
        for name in z.namelist():
            if name.startswith("landing/") and name.endswith(".csv"):
                target = kit.DATA / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
    kit.DATA.mkdir(exist_ok=True)
    kit.ACTIVE.write_text(json.dumps({"name": m["name"], "bundle": str(bundle.resolve())}), encoding="utf-8")
    print(f"Scenario '{m['name']}' is ready: the files for the 28 days are in {kit.LANDING}")
    if started:
        print("Simulation days have already run on another scenario. Start them again on this one:\n"
              "  python reset_sim.py")
    else:
        print("Next: python simulator.py step   (one day with the do-nothing agent)")


if __name__ == "__main__":
    main()
