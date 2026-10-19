"""Generate the synthetic retailer.

Writes one CSV per feed per day:
  data/history/<feed>/<date>.csv            90 days of clean history
  data/calendar.csv                         trading events known in advance (e.g. Black Friday)
and loads the history into the core tables (unless --files-only).

Then choose a scenario for the 28 simulation days: python scenario.py dev
All names, emails, phone numbers and postcodes are fictional: phone numbers come from the
ranges Ofcom reserves for drama, emails use example.com/.org/.net.
"""
import argparse
import random
from datetime import date, datetime, timedelta

import kit

FIRST = ["Olivia", "Amelia", "Isla", "Ava", "Mia", "Ivy", "Lily", "Grace", "Freya", "Emily", "Sophie", "Ella",
         "Chloe", "Ruby", "Lucy", "Hannah", "Zara", "Aisha", "Priya", "Nadia", "Oliver", "George", "Noah",
         "Arthur", "Leo", "Harry", "Oscar", "Jack", "Charlie", "Thomas", "James", "Samuel", "Daniel", "Adam",
         "Omar", "Ravi", "Luca", "Mateo", "Hugo", "Felix"]
LAST = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Patel", "Robinson",
        "Wright", "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke",
        "Khan", "Hughes", "Edwards", "Lewis", "Turner", "Hill", "Moore", "Cooper", "Ward", "Morris", "King",
        "Baker", "Harris", "Martin", "Ali", "Shah", "Murphy", "Kelly", "Price"]
DOMAINS = ["example.com", "example.org", "example.net", "mail.example.com"]

STORES = [  # store_id, name, city, region
    ("S01", "Leeds Briggate", "Leeds", "North"), ("S02", "Manchester Arndale", "Manchester", "North"),
    ("S03", "Liverpool One", "Liverpool", "North"), ("S04", "York Coppergate", "York", "North"),
    ("S05", "Newcastle Eldon", "Newcastle", "North"), ("S06", "Birmingham Bullring", "Birmingham", "Midlands"),
    ("S07", "Nottingham Victoria", "Nottingham", "Midlands"), ("S08", "Leicester Highcross", "Leicester", "Midlands"),
    ("S09", "Coventry West Orchards", "Coventry", "Midlands"), ("S10", "Derby Westfield", "Derby", "Midlands"),
    ("S11", "Bristol Cabot", "Bristol", "South"), ("S12", "Southampton WestQuay", "Southampton", "South"),
    ("S13", "Brighton Churchill", "Brighton", "South"), ("S14", "Exeter Princesshay", "Exeter", "South"),
    ("S15", "Oxford Westgate", "Oxford", "South"), ("S16", "London Oxford Street", "London", "London"),
    ("S17", "London Covent Garden", "London", "London"), ("S18", "London Westfield", "London", "London"),
    ("S19", "London Stratford", "London", "London"), ("S20", "London Canary Wharf", "London", "London"),
]

CATEGORIES = {  # category -> (price range, nouns)
    "Womenswear": ((18, 120), ["Dress", "Blouse", "Skirt", "Jumper", "Coat", "Jeans", "Cardigan"]),
    "Menswear": ((15, 140), ["Shirt", "Chinos", "Jacket", "Jumper", "Jeans", "Polo", "Overcoat"]),
    "Footwear": ((25, 110), ["Trainers", "Boots", "Loafers", "Sandals", "Brogues"]),
    "Accessories": ((6, 60), ["Scarf", "Belt", "Wallet", "Hat", "Gloves", "Tote"]),
    "Home": ((8, 90), ["Cushion", "Throw", "Candle", "Vase", "Mug Set", "Lamp"]),
    "Beauty": ((5, 45), ["Moisturiser", "Lip Balm", "Hand Cream", "Perfume", "Face Wash"]),
}
ADJ = ["Classic", "Linen", "Wool", "Cotton", "Organic", "Slim", "Relaxed", "Vintage", "Soft", "Merino",
       "Cord", "Quilted", "Striped", "Recycled", "Essential", "Heritage"]

WEEKDAY = [0.85, 0.85, 0.9, 1.0, 1.15, 1.35, 0.9]  # Mon..Sun: Saturday is about 1.6 x Monday
POS_LINES, WEB_LINES = 640, 260                    # average lines per day across the week
INITIAL_CUSTOMERS, NEW_CUSTOMERS_PER_DAY = 3500, 12
INITIAL_PRODUCTS = 300

# Known trading events: (date, name, pos factor, web factor, category marked down, markdown factor)
EVENTS = [(date(2026, 11, 27), "Black Friday", 1.5, 1.8, "Womenswear", 0.6)]


def ofcom_numbers():
    """Every number in the ranges Ofcom reserves for drama: none of them can ring a real phone."""
    nums = [f"07700 900{n:03d}" for n in range(1000)] + [f"01632 960{n:03d}" for n in range(1000)]
    nums += [f"020 7946 0{n:03d}" for n in range(1000)]
    for area in ["0113", "0114", "0115", "0116", "0117", "0118", "0121", "0131", "0141", "0151", "0161"]:
        nums += [f"{area} 496 0{n:03d}" for n in range(1000)]
    nums += [f"028 9649 6{n:03d}" for n in range(1000)] + [f"029 2018 0{n:03d}" for n in range(1000)]
    return nums


class Retailer:
    def __init__(self, seed):
        self.r = random.Random(seed)
        self.customers = {}      # customer_id -> dict (current state)
        self.products = {}       # sku_id -> dict (current state)
        self.weights = {}        # sku_id -> popularity
        self.next_customer = 1
        self.next_sku = 10001
        self.email_seq = 100     # makes every email address unique
        self.phones = ofcom_numbers()
        self.r.shuffle(self.phones)
        self.store_weight = {s[0]: self.r.uniform(0.6, 1.6) for s in STORES}

    # ---------- customers ----------
    def email(self, name):
        first, last = name.split(" ", 1)
        self.email_seq += self.r.randint(1, 9)
        return f"{first}.{last}{self.email_seq}@{self.r.choice(DOMAINS)}".lower()

    def postcode(self):
        r, L = self.r, "ABCDEFGHJKLMNPRSTUWYZ"
        area = r.choice(L) + (r.choice(L) if r.random() < 0.7 else "")
        return f"{area}{r.randint(1, 29)} {r.randint(0, 9)}{r.choice(L)}{r.choice(L)}"

    def new_customer(self, day):
        name = f"{self.r.choice(FIRST)} {self.r.choice(LAST)}"
        cid = f"C{self.next_customer:06d}"
        self.next_customer += 1
        c = {"customer_id": cid, "name": name, "email": self.email(name), "phone": self.phones.pop(),
             "postcode": self.postcode(), "created_at": day.isoformat()}
        self.customers[cid] = c
        return c

    def update_customer(self, cid):
        c = self.customers[cid]
        field = self.r.choice(["phone", "postcode", "email"])
        if field == "phone":
            c["phone"] = self.phones.pop()
        elif field == "postcode":
            c["postcode"] = self.postcode()
        else:
            c["email"] = self.email(c["name"])
        return c

    # ---------- products ----------
    def ean13(self):
        digits = [5, 0] + [self.r.randint(0, 9) for _ in range(10)]
        check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10) % 10
        return "".join(map(str, digits + [check]))

    def new_product(self):
        cat = self.r.choice(list(CATEGORIES))
        (lo, hi), nouns = CATEGORIES[cat]
        sku = f"SKU-{self.next_sku}"
        self.next_sku += 1
        p = {"sku_id": sku, "name": f"{self.r.choice(ADJ)} {self.r.choice(nouns)}", "category": cat,
             "unit_price": f"{round(self.r.uniform(lo, hi)) - 0.01:.2f}", "barcode": self.ean13()}
        self.products[sku] = p
        self.weights[sku] = self.r.paretovariate(1.3)
        return p

    def reprice(self, sku, factor=None):
        p = self.products[sku]
        factor = factor or self.r.choice([0.8, 0.85, 0.9, 1.05, 1.1])
        p["unit_price"] = f"{max(1.99, round(float(p['unit_price']) * factor) - 0.01):.2f}"
        return p

    # ---------- one day ----------
    def day(self, day, first):
        r, f = self.r, WEEKDAY[day.weekday()]
        event = next((e for e in EVENTS if e[0] == day), None)
        # Orders only use products and customers that existed before the day started.
        known_customers = list(self.customers)
        skus = list(self.products)
        if first:
            pim = [dict(self.new_product()) for _ in range(INITIAL_PRODUCTS)]
            crm = [dict(self.new_customer(day)) for _ in range(INITIAL_CUSTOMERS)]
            known_customers = list(self.customers)
        else:
            if event:
                pim = [dict(self.reprice(s, event[5])) for s in sorted(self.products)
                       if self.products[s]["category"] == event[4]]
            else:
                pim = [dict(self.reprice(s)) for s in r.sample(sorted(self.products), r.randint(0, 4))]
            if r.random() < 1 / 7:
                pim.append(dict(self.new_product()))
            updates = r.sample(known_customers, r.randint(2, 8))
            crm = [dict(self.new_customer(day)) for _ in range(max(1, round(r.gauss(NEW_CUSTOMERS_PER_DAY * f, 3))))]
            crm += [dict(self.update_customer(c)) for c in updates]
        if first:
            skus = list(self.products)
        weights = [self.weights[s] for s in skus]
        stores = [s[0] for s in STORES]
        sw = [self.store_weight[s] for s in stores]
        pos_f, web_f = (event[2], event[3]) if event else (1, 1)

        def line(channel, n):
            sku = r.choices(skus, weights)[0]
            items = r.choices([1, 2, 3, 4], [70, 20, 7, 3])[0]
            if channel == "pos":
                ts = datetime.combine(day, datetime.min.time()) + timedelta(minutes=r.randint(9 * 60, 20 * 60 - 1))
                cid = r.choice(known_customers) if r.random() < 0.35 else ""
                return {"order_id": f"P{day:%Y%m%d}{n:05d}", "store_id": r.choices(stores, sw)[0],
                        "customer_id": cid, "sku_id": sku, "quantity": items,
                        "unit_price": self.products[sku]["unit_price"], "order_ts": f"{ts:%Y-%m-%d %H:%M:%S}"}
            ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=r.randint(0, 86399))
            cid = r.choice(known_customers)
            return {"order_id": f"W{day:%Y%m%d}{n:05d}", "customer_id": cid,
                    "customer_email": self.customers[cid]["email"], "sku_id": sku, "quantity": items,
                    "unit_price": self.products[sku]["unit_price"], "order_ts": f"{ts:%Y-%m-%d %H:%M:%S}"}

        pos = [line("pos", i + 1) for i in range(round(POS_LINES * f * pos_f * r.uniform(0.9, 1.1)))]
        web = [line("web", i + 1) for i in range(round(WEB_LINES * f * web_f * r.uniform(0.9, 1.1)))]
        pos.sort(key=lambda x: x["order_ts"])
        web.sort(key=lambda x: x["order_ts"])
        return {"pim": pim, "crm": crm, "pos": pos, "web": web}


def load_history(conn, retailer_days):
    """Load the clean history straight into the core tables, one promoted batch per feed per day."""
    with conn.cursor() as cur:
        with cur.copy("COPY stores (store_id, name, city, region) FROM STDIN") as cp:
            for s in STORES:
                cp.write_row(s)
        customers, products, orders = {}, {}, []
        for day, feeds in retailer_days:
            for feed in kit.FEED_ORDER:
                table, cols = kit.FEEDS[feed]
                cur.execute("INSERT INTO batches (feed_name, target_table, batch_date, row_count, schema_hash, status)"
                            " VALUES (%s, %s, %s, %s, %s, 'promoted') RETURNING batch_id",
                            (feed, table, day, len(feeds[feed]), kit.schema_hash(cols)))
                bid = cur.fetchone()[0]
                for row in feeds[feed]:
                    if feed == "crm":
                        customers[row["customer_id"]] = (row, bid)
                    elif feed == "pim":
                        products[row["sku_id"]] = (row, bid)
                    else:
                        orders.append((feed, row, bid))
        with cur.copy("COPY customers (customer_id, name, email, phone, postcode, created_at, batch_id) FROM STDIN") as cp:
            for row, bid in customers.values():
                cp.write_row([row[c] for c in kit.FEEDS["crm"][1]] + [bid])
        with cur.copy("COPY products (sku_id, name, category, unit_price, barcode, batch_id) FROM STDIN") as cp:
            for row, bid in products.values():
                cp.write_row([row[c] for c in kit.FEEDS["pim"][1]] + [bid])
        with cur.copy("COPY orders (order_id, channel, store_id, customer_id, customer_email, sku_id, quantity,"
                      " unit_price, order_ts, batch_id) FROM STDIN") as cp:
            for feed, row, bid in orders:
                cp.write_row([row["order_id"], feed, row.get("store_id") or None, row.get("customer_id") or None,
                              row.get("customer_email") or None, row["sku_id"], row["quantity"], row["unit_price"],
                              row["order_ts"], bid])
    conn.commit()
    print(f"Loaded {len(customers)} customers, {len(products)} products, {len(orders)} order lines "
          f"from {len(retailer_days)} days of history.")


def simulate(seed):
    """The retailer's 90 days of history and the 28 simulation days that follow, all clean."""
    shop = Retailer(seed)
    history = [(day, shop.day(day, first=(i == 0))) for i, day in enumerate(kit.history_dates())]
    sim = [(day, shop.day(day, first=False)) for day in kit.sim_dates()]
    return history, sim


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=kit.DATA_SEED,
                    help="leave the default: the scenarios in scenarios/ are built for it")
    ap.add_argument("--files-only", action="store_true", help="write the CSV files but do not touch the database")
    args = ap.parse_args()

    conn = None
    if not args.files_only:
        conn = kit.connect_or_exit()
        kit.apply_schema(conn)
        if conn.execute("SELECT count(*) FROM batches").fetchone()[0]:
            raise SystemExit("The database already holds data. To start again from scratch:\n"
                             "  docker compose down -v\n  docker compose up -d --wait\n"
                             "then run generate_data.py and scenario.py again.")

    kit.clear_dir(kit.HISTORY)
    history, _ = simulate(args.seed)
    for day, feeds in history:
        for feed, rows in feeds.items():
            kit.write_csv(kit.feed_file(kit.HISTORY, feed, day), kit.FEEDS[feed][1], rows)
    kit.write_csv(kit.CALENDAR, ["date", "event", "expected_effect"], [
        {"date": e[0].isoformat(), "event": e[1],
         "expected_effect": f"POS about +{round((e[2] - 1) * 100)}%, web about +{round((e[3] - 1) * 100)}%; "
                            f"{e[4]} marked down {round((1 - e[5]) * 100)}%, so the PIM batch that morning "
                            f"carries every {e[4]} price"} for e in EVENTS])
    print(f"Wrote {kit.HISTORY_DAYS} days of history to {kit.HISTORY}")
    if conn:
        load_history(conn, history)
        import profiler
        profiler.profile_history(conn)
        print("Profiled the history, so every column has a baseline from day 1.")
        conn.close()
    print("Next: python scenario.py dev")


if __name__ == "__main__":
    main()
