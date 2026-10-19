-- Data Hygiene Watchdog: schema.
-- Safe to run again: every statement only creates what is missing.
--
-- Core tables hold clean, promoted data. New batches land in the staging_* tables as raw
-- JSON, one row per source record, exactly as delivered, so a batch whose columns changed
-- (schema drift) or with text in a number column can still land and be inspected.
-- In staging, a missing value is JSON null.

CREATE TABLE IF NOT EXISTS stores (
    store_id   text PRIMARY KEY,
    name       text NOT NULL,
    city       text NOT NULL,
    region     text NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id     bigserial PRIMARY KEY,
    feed_name    text NOT NULL CHECK (feed_name IN ('pos', 'web', 'crm', 'pim')),
    target_table text NOT NULL CHECK (target_table IN ('orders', 'customers', 'products')),
    batch_date   date NOT NULL,
    row_count    int  NOT NULL,
    schema_hash  text,                 -- hash of the column names as delivered
    status       text NOT NULL DEFAULT 'landed'
                 CHECK (status IN ('landed', 'missing', 'handed_off', 'promoted')),
    loaded_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (feed_name, batch_date)
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id text PRIMARY KEY,
    name        text,
    email       text,
    phone       text,
    postcode    text,
    created_at  date,
    batch_id    bigint REFERENCES batches   -- batch that last wrote this row
);

CREATE TABLE IF NOT EXISTS products (
    sku_id     text PRIMARY KEY,
    name       text,
    category   text,
    unit_price numeric(10, 2),
    barcode    text,
    batch_id   bigint REFERENCES batches
);

-- One row per order line. POS lines have a store_id and an optional loyalty customer_id;
-- web lines always have a customer_id and the email typed at checkout.
CREATE TABLE IF NOT EXISTS orders (
    order_id       text PRIMARY KEY,
    channel        text NOT NULL CHECK (channel IN ('pos', 'web')),
    store_id       text REFERENCES stores,
    customer_id    text REFERENCES customers,
    customer_email text,
    sku_id         text NOT NULL REFERENCES products,
    quantity       int  NOT NULL,
    unit_price     numeric(10, 2) NOT NULL,
    order_ts       timestamp NOT NULL,
    batch_id       bigint REFERENCES batches
);
CREATE INDEX IF NOT EXISTS orders_batch_idx    ON orders (batch_id);
CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders (customer_id);
CREATE INDEX IF NOT EXISTS orders_sku_idx      ON orders (sku_id);

-- status: pending (just landed), clean (checked), quarantined, awaiting_approval; then the simulator sets
-- promoted, merged (a duplicate merged into an existing customer) or lost (could not be converted).
CREATE TABLE IF NOT EXISTS staging_customers (
    batch_id bigint REFERENCES batches,
    row_num  int,
    payload  jsonb NOT NULL,              -- your agent may change this
    original jsonb,                       -- exactly as delivered; never changes
    status   text NOT NULL DEFAULT 'pending' CHECK (status IN
             ('pending', 'clean', 'quarantined', 'awaiting_approval', 'promoted', 'merged', 'lost')),
    reason   text,
    PRIMARY KEY (batch_id, row_num)
);
CREATE TABLE IF NOT EXISTS staging_products (
    batch_id bigint REFERENCES batches,
    row_num  int,
    payload  jsonb NOT NULL,              -- your agent may change this
    original jsonb,                       -- exactly as delivered; never changes
    status   text NOT NULL DEFAULT 'pending' CHECK (status IN
             ('pending', 'clean', 'quarantined', 'awaiting_approval', 'promoted', 'merged', 'lost')),
    reason   text,
    PRIMARY KEY (batch_id, row_num)
);
CREATE TABLE IF NOT EXISTS staging_orders (
    batch_id bigint REFERENCES batches,
    row_num  int,
    payload  jsonb NOT NULL,              -- your agent may change this
    original jsonb,                       -- exactly as delivered; never changes
    status   text NOT NULL DEFAULT 'pending' CHECK (status IN
             ('pending', 'clean', 'quarantined', 'awaiting_approval', 'promoted', 'merged', 'lost')),
    reason   text,
    PRIMARY KEY (batch_id, row_num)
);

-- Written by profiler.py: one row per batch and column, with the baseline beside it.
CREATE TABLE IF NOT EXISTS column_profiles (
    batch_id          bigint REFERENCES batches,
    column_name       text,
    null_rate         numeric,
    distinct_count    int,
    min_value         text,
    max_value         text,
    format_match      numeric,
    baseline_null_rate    numeric,       -- average over the feed's last 30 days of promoted rows
    baseline_format_match numeric,
    promoted_null_rate    numeric,       -- the same measures over the rows that reached the core tables
    promoted_format_match numeric,
    PRIMARY KEY (batch_id, column_name)
);

-- ---------- written by your agent ----------
CREATE TABLE IF NOT EXISTS issues (
    issue_id      bigserial PRIMARY KEY,
    batch_id      bigint REFERENCES batches,
    issue_key     text NOT NULL,          -- feed:table:column:check, the same every day for the same problem
    check_type    text NOT NULL,
    column_name   text,
    affected_rows int,
    description   text,
    status        text NOT NULL DEFAULT 'open',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fixes (
    fix_id           bigserial PRIMARY KEY,
    issue_id         bigint REFERENCES issues,
    batch_id         bigint REFERENCES batches,
    table_name       text,
    column_name      text,
    fix_type         text NOT NULL,       -- e.g. standardise_format, map_column, fill_from_source, merge, correct_value
    record_id        text,                -- row_num in staging, or the business key
    before_value     text,
    after_value      text,
    reasoning        text,
    status           text NOT NULL DEFAULT 'proposed'
                     CHECK (status IN ('proposed', 'applied', 'awaiting_approval', 'approved', 'rejected', 'undone')),
    decided_by       text,                -- agent, steward or a person's name
    decided_on       date,                -- simulation day of the steward's (or a person's) decision
    applied_on       date,                -- simulation day an approved fix was applied by the simulator
    rejection_reason text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quarantine (
    quarantine_id bigserial PRIMARY KEY,
    batch_id      bigint REFERENCES batches,
    table_name    text NOT NULL,
    row_num       int NOT NULL,
    issue_id      bigint REFERENCES issues,
    reason        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    released_at   timestamptz
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id     bigserial PRIMARY KEY,
    issue_key     text NOT NULL,
    title         text NOT NULL,
    root_cause    text,
    affected_rows int,
    sample        jsonb,
    drafted_fix   text,
    priority      text NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'escalated')),
    status        text NOT NULL DEFAULT 'open',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hand_offs (
    hand_off_id bigserial PRIMARY KEY,
    batch_id    bigint REFERENCES batches,
    to_agent    text NOT NULL DEFAULT 'data_sync_monitor',
    reason      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_log (
    log_id     bigserial PRIMARY KEY,
    sim_day    date,
    step       text,
    decision   text,
    reasoning  text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- written by the simulator; your agent cannot read it ----------
CREATE TABLE IF NOT EXISTS sim_results (
    sim_day        date PRIMARY KEY,
    caught         int,
    fixed_correctly int,
    missed         int,
    wrongly_changed int,
    false_alarms   int,
    data_health    numeric,
    details        jsonb
);

-- ---------- a restricted login for your agent and MCP server ----------
-- user: agent   password: agent
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent') THEN
        CREATE ROLE agent LOGIN PASSWORD 'agent';
    END IF;
END $$;
GRANT SELECT ON stores, batches, customers, products, orders, column_profiles TO agent;
GRANT SELECT ON staging_customers, staging_products, staging_orders TO agent;
GRANT UPDATE (payload, status, reason) ON staging_customers, staging_products, staging_orders TO agent;
GRANT SELECT, INSERT, UPDATE ON issues, fixes, quarantine, tickets, hand_offs, agent_log TO agent;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO agent;
REVOKE ALL ON sim_results FROM agent;
-- An agent that forgets to commit must not block the simulator.
ALTER ROLE agent SET idle_in_transaction_session_timeout = '30s';

-- Only the data steward (the simulator, or a person through your UI) approves or rejects fixes.
CREATE OR REPLACE FUNCTION fixes_guard() RETURNS trigger AS $$
BEGIN
    IF current_user = 'agent' THEN
        IF NEW.status NOT IN ('proposed', 'applied', 'awaiting_approval', 'undone')
           OR NEW.decided_on IS NOT NULL OR NEW.applied_on IS NOT NULL THEN
            RAISE EXCEPTION 'your agent may propose, apply or undo a fix; approving or rejecting is the data steward''s job';
        END IF;
        IF TG_OP = 'UPDATE' AND OLD.status IN ('approved', 'rejected') THEN
            RAISE EXCEPTION 'fix % was already decided by the data steward', OLD.fix_id;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER fixes_guard BEFORE INSERT OR UPDATE ON fixes FOR EACH ROW EXECUTE FUNCTION fixes_guard();

-- Your agent sets pending, clean, quarantined or awaiting_approval; only the simulator promotes rows.
CREATE OR REPLACE FUNCTION staging_guard() RETURNS trigger AS $$
BEGIN
    IF current_user = 'agent' AND (NEW.status NOT IN ('pending', 'clean', 'quarantined', 'awaiting_approval')
                                   OR OLD.status IN ('promoted', 'merged', 'lost')) THEN
        RAISE EXCEPTION 'your agent may set a row to pending, clean, quarantined or awaiting_approval, and only before it is promoted';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER staging_guard BEFORE UPDATE ON staging_customers FOR EACH ROW EXECUTE FUNCTION staging_guard();
CREATE OR REPLACE TRIGGER staging_guard BEFORE UPDATE ON staging_products FOR EACH ROW EXECUTE FUNCTION staging_guard();
CREATE OR REPLACE TRIGGER staging_guard BEFORE UPDATE ON staging_orders FOR EACH ROW EXECUTE FUNCTION staging_guard();
