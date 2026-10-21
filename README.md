# Data Hygiene Watchdog: starter kit

This kit gives you everything around your agent:
- a retailer's database, with 90 days of clean history;
- ready-made scenarios: 28 simulation days with data problems planted in them;
- a profiler;
- a simulator that runs the days and reports on your agent;
- a working "hello world" MCP server and ADK agent to start from.

All names, emails, phone numbers and postcodes are fictional.

## 1. Before the first session (at home, about 30 minutes)

| You need | Install | Check it works |
|---|---|---|
| Docker | **Windows / macOS:** [Docker Desktop](https://www.docker.com/products/docker-desktop/). Windows also needs WSL2: the installer offers it, then restart. If Docker says virtualisation is off, enable it in the BIOS. **Linux:** Docker Engine plus the Compose plugin, then `sudo usermod -aG docker $USER` and log out and in. | `docker compose version` (with a space) |
| Python **3.13** (3.11 to 3.14 also work; **not 3.15 yet**: some packages have no 3.15 build) | **Windows / macOS:** [python.org](https://www.python.org/downloads/). Pick 3.13 in "Looking for a specific release?". On Windows, tick "Add python.exe to PATH" if offered, and click "Disable path length limit" on the last screen. **Windows on ARM (Snapdragon):** install the 64-bit **x64** Python, not ARM64. **Linux:** Ubuntu 24.04 has 3.12. Ubuntu 22.04 has 3.10 (too old): run `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv`. **macOS:** the built-in `python3` may be 3.9 (too old). | Windows: `py -0` lists your Pythons · macOS/Linux: `python3.13 --version` |
| Node.js LTS (20 or 22) | [nodejs.org](https://nodejs.org), for your UI in week 2 | `node --version` |
| git (optional) | [git-scm.com](https://git-scm.com/downloads), or download the kit as a zip | `git --version` |

Then, at home rather than on the university network:

```
docker pull postgres:16
```

This downloads the database image (about 150 MB), so 30 laptops don't download it at once in class.

**Behind a proxy:** set it in Docker Desktop → Settings → Resources → Proxies. For Python packages, use `pip install --proxy http://host:port -r requirements.txt`.

## 2. Get the kit

```
git clone https://github.com/albertocalsa/data-hygiene-starter-kit.git
cd data-hygiene-starter-kit
```

Or download the zip from GitHub (Code → Download ZIP) and unzip it.

Put it at a short path in your home folder:
- Windows: `C:\Users\<you>\watchdog`.
- macOS / Linux: `~/watchdog`.

Avoid OneDrive, Dropbox, iCloud and `/tmp`. On Windows, Documents and Desktop are often inside OneDrive.

Open a terminal in the kit folder. On Windows use **PowerShell**; on macOS or Linux use **Terminal**.

## 3. Start the database

Start Docker Desktop first, then run:

```
docker compose up -d --wait
```

You should see `Container ... Healthy`.

The database listens on port **15432**, so it won't clash with a PostgreSQL you may already have installed. To change the port, create your settings file and set `PG_PORT` in it. Both Docker and the scripts read that file.
- Windows: `Copy-Item .env.example .env; notepad .env`
- macOS / Linux: `cp .env.example .env`, then edit `.env`

The Compose file also starts a FastAPI service on port **8000**. After the database has been initialized with `python generate_data.py`, you can retrieve a customer through the API:

```
curl http://localhost:8000/health
curl http://localhost:8000/users/<customer_id>
```

The API maps `customer_id` from the `customers` table to `user_id` in its response. Set `API_PORT` in `.env` if port 8000 is already in use.

## 4. Python environment and data

**Windows (PowerShell)**
```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python generate_data.py
python scenario.py dev
python simulator.py step
```

If `Activate.ps1` is blocked, run this once and answer Y:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```
On a university machine where that is not allowed, use `Set-ExecutionPolicy -Scope Process Bypass` (for the current window only).

**macOS / Linux**
```bash
python3.13 -m venv .venv          # or python3.12 / python3.11
source .venv/bin/activate
pip install -r requirements.txt
python generate_data.py
python scenario.py dev
python simulator.py step
```

While the environment is active, your prompt starts with `(.venv)`. Run the activate line again (the second line above) in every new terminal. Installing takes about a minute and some 450 MB.

What you should see:
- `generate_data.py` creates the tables and prints `Loaded 4580 customers, 313 products, 80347 order lines from 90 days of history.`, then that it profiled the history.
- `scenario.py dev` unpacks the **development scenario**: the files the four feeds will deliver over the 28 simulation days, with data problems planted in them. Where they are, and what they are, is for your agent to find out. `python scenario.py` lists the other scenarios.
- `simulator.py step` runs day 1 (Monday 2 November) with the do-nothing agent. It shows the four batches (`pim: 3 rows`, `crm: 18 rows`, `pos: 561 rows`, `web: 213 rows`), then `promoted 795 rows` and a results line.

## 5. Look at the data

**From the command line** (nothing to install):
```
docker compose exec postgres psql -U watchdog -d retail
```

**From a SQL client** (DBeaver, pgAdmin, VS Code):

| Setting | Value |
|---|---|
| host | `localhost` |
| port | `15432` (or your `PG_PORT`) |
| database | `retail` |
| user / password | `watchdog` / `watchdog` (you see everything) |

A pgAdmin running inside Docker must use the host `host.docker.internal`. On Linux, also start that container with `--add-host=host.docker.internal:host-gateway`.

Start with these queries:

```sql
SELECT batch_id, feed_name, batch_date, row_count, status FROM batches WHERE batch_date >= '2026-11-02' ORDER BY batch_id;
SELECT payload FROM staging_orders LIMIT 5;                 -- staging is raw JSON, exactly as delivered
SELECT payload->>'email' AS email FROM staging_customers;    -- read a field with ->>
SELECT channel, count(*) FROM orders GROUP BY channel;       -- the clean core tables
```

To see one day's profiles next to their baselines: `python profiler.py --day 1`.

## 6. The simulator

| To… | Run |
|---|---|
| Run the next day with the do-nothing agent | `python simulator.py step` |
| Run the next day with your agent | `python simulator.py step --agent my_agent:run_day` |
| Run every remaining day | `python simulator.py run --agent my_agent:run_day` (or `--days 7`) |
| Finish a day that was interrupted | `python simulator.py resume --agent my_agent:run_day` |
| See the results report | `python simulator.py report` |
| Replay the 28 days (keeps the history) | `python reset_sim.py` |
| Try another scenario | `python scenario.py practice` (same kinds of problem, other days, feeds and counts), then `python reset_sim.py` |
| Add the bonus problems | `python scenario.py chaos`, then `python reset_sim.py` |
| Start completely from scratch | `docker compose down -v`, then `docker compose up -d --wait`, then `python generate_data.py` and `python scenario.py dev` |

When you get a new version of the kit, start completely from scratch.

**Each simulated day runs in this order:**
1. **Morning.** The data steward decides on the fixes awaiting approval from earlier days, and applies those a person approved in your UI. An approved fix is applied to its staging row. A rejected one gets a `rejection_reason`, which your agent can read the same day.
2. **Delivery.** The day's batches arrive in staging. A feed with no file is recorded as `missing`.
3. **Profiling.** Every new batch is profiled into `column_profiles`, next to its baseline.
4. **Your agent.** Its `async def run_day(day)` runs once (`day` is a `datetime.date`):
   - in its own process, logged in as `agent`;
   - with a time limit (`--timeout`, 900 seconds by default).

   If it crashes or times out, the day goes on without it, and the report notes "agent error".
5. **Promotion.** Every staging row still `pending` or `clean` is converted to the core types and promoted. A row that fails the conversion (a missing column, `£` in a price, a date in the wrong format) or points to an unknown customer or product becomes `lost`.
6. **Results.** A line is added to the results report:
   - problems caught and missed;
   - fixes right and wrong (a fix the steward rejects counts as wrong);
   - false alarms (issues or hand-offs on a feed with no problem that day);
   - **data health**: of the rows that should have reached the core tables that day, the share that did, with their true values.

   Rows that should not exist count against data health. Rows held for approval count on the day they land.

After day 28 one last morning runs, so fixes proposed on the last day are decided too.

`python simulator.py run --agent noop_agent:run_day` gives your baseline: every problem missed. The report shows on which feed you missed a problem, not what it was. It is not a ranking. "Missed" counts each day a problem is present, so a three-day problem counts three times.

To call the simulator from your REST API, use `simulator.step("my_agent:run_day")`. In FastAPI, call it in a thread: `await run_in_threadpool(simulator.step, "my_agent:run_day")`.

## 7. Contract with the simulator

Your agent and MCP server connect with the restricted user **`agent` / `agent`**. In code the simulator runs, `kit.connect()` gives you that login automatically. It can:
- read the data;
- change a staging row's `payload`, `status` and `reason`;
- write `issues`, `fixes`, `quarantine`, `tickets`, `hand_offs` and `agent_log`.

It cannot read `sim_results`, change the core tables, delete anything, or approve its own fixes. Each staging row also keeps `original`, the row exactly as delivered, which nobody can change.

Your **REST API** (for the UI) connects as `watchdog`. That is how a person approves or rejects fixes. Never give that login to the agent.

Your agent tells the simulator what to do through these tables. Every column and allowed value is commented in `schema.sql`.

| You want to… | Write |
|---|---|
| Record a problem | A row in `issues` with `batch_id`, `issue_key` (`feed:table:column:check`, e.g. `crm:customers:email:completeness`) and `check_type` |
| Keep a row out of the core tables | `staging_*.status = 'quarantined'`, and a row in `quarantine` with the `reason`. Set it back to `pending` to release it. |
| Say a row is fine | `staging_*.status = 'clean'` (optional: `pending` rows are promoted too) |
| Change a value yourself (safe-list fix) | Update the staging `payload`, and log it in `fixes` with `status = 'applied'` |
| Ask the steward first | A row in `fixes` with `status = 'awaiting_approval'`, and `staging_*.status = 'awaiting_approval'` on that row. The steward applies it next morning if it is right. |
| Map a renamed column | `fixes.fix_type = 'map_column'`, `record_id` empty, `before_value` = delivered name, `after_value` = usual name. Either apply it yourself (rename the key in every `payload`) or ask the steward. |
| Merge a duplicate customer | `fixes.fix_type = 'merge'`, `column_name = 'customer_id'`, `record_id` = the duplicate's `row_num`, `after_value` = the existing `customer_id` |
| Hand a batch to the Data Sync Monitor | A row in `hand_offs` with the `batch_id` and a `reason`. The batch is left alone and never promoted. |
| Approve or reject in your UI (a person) | As `watchdog`: `fixes.status = 'approved'` or `'rejected'`, `decided_by` = the person's name, and a `rejection_reason`. The simulator applies approved fixes next morning. |

In `fixes`:
- `batch_id`, `fix_type` (e.g. `correct_value`, `standardise_format`, `fill_from_source`, `map_column`, `merge`), `table_name` (`orders`, `customers` or `products`) and `column_name` are always needed. So is `after_value`, except for `map_column`, which needs `before_value` too.
- `record_id` is the staging `row_num`, not the business id.
- The steward judges every fix against the true data and rejects wrong ones with a reason that never gives the answer away. A fix that changes nothing is not counted.

## 8. Your first agent: the hello examples

- **`hello_mcp.py`** is an MCP server with one tool, `list_batches(day)`. It is built with the MCP Python SDK 2.x, where the class `FastMCP` is now called `MCPServer`.
  - `python hello_mcp.py --test 1` shows what the tool returns, without an LLM (run that day first).
- **`hello_agent.py`** is an ADK agent that starts that server over stdio, using ADK's `McpToolset`. It asks the LLM to check a day's batches.
  - It passes the environment on to the server: MCP passes only a few variables by default. Copy that line into your own agent.

Choose your LLM in `.env` (see section 3):

- **Gemini (recommended).** Get a free API key at [Google AI Studio](https://aistudio.google.com) (Get API key), then set `GOOGLE_API_KEY=...`.
  - `.env` is never committed, so the key stays private.
  - Each student should use their own key. The free tier limits requests per minute and per day ([current limits](https://ai.google.dev/gemini-api/docs/rate-limits)).
  - Google may use free-tier prompts to improve its models, which is fine for this synthetic data.
- **Or a local model.** Install [Ollama](https://ollama.com), run `ollama pull qwen3:4b` (about 2.5 GB; 8 GB of RAM or more), then set `WATCHDOG_MODEL=ollama_chat/qwen3:4b`.
  - Small models such as `qwen3:1.7b` or Mistral 7B often call tools wrongly.

Then run:

```
python hello_agent.py 1                               # ask about day 1 (run that day first)
python simulator.py step --agent hello_agent:run_day  # let the simulator call it
```

You should see `[tool call] list_batches(...)`, then a two- or three-sentence answer. A local model takes 20 to 40 seconds.

Your own agent follows the same pattern: an MCP server with your tools, and an `async def run_day(day)` that runs an ADK agent over them.

## 9. The data

**Feeds.** Every feed is a CSV file with one row per record. A blank value is missing; in staging it is stored as JSON `null`.

| Feed | Lands in | What it contains | Normal volume |
|---|---|---|---|
| `pos` | `staging_orders` | Till lines from 20 stores. Columns: `order_id`, `store_id`, `customer_id`, `sku_id`, `quantity`, `unit_price`, `order_ts`. | About 530 on Monday, 865 on Saturday |
| `web` | `staging_orders` | Website order lines. Same columns as POS, but `customer_email` (the email typed at checkout) replaces `store_id`. | About 220 on Monday, 350 on Saturday |
| `crm` | `staging_customers` | New customers and changed ones (same `customer_id`, one field changed). A changed customer is not a duplicate. | About 10 to 25 a day |
| `pim` | `staging_products` | Price changes and the odd new product. | 0 to 5 a day. An empty batch is normal. |

POS and web both land in `staging_orders`; `batches.feed_name` tells you which feed a batch came from.

**What normal looks like.**
- **POS lines:**
  - About 65% have no `customer_id`, because most shoppers don't scan a loyalty card.
  - `order_ts` falls between 09:00 and 20:00.
  - `quantity` is 1 to 4, mostly 1.
  - `unit_price` is the catalogue price that day, in pounds with two decimals.
- **Web lines:**
  - They always have a `customer_id`.
  - `order_ts` falls at any hour.
  - The checkout email can be older than the CRM email, because customers update their details later.
- **Formats:** the profiler checks every column against these. They are listed in `profiler.py`.

  | Field | Format |
  |---|---|
  | `order_id` | `P` or `W`, then the date, then 5 digits |
  | `customer_id` | `C` + 6 digits |
  | `sku_id` | `SKU-` + 5 digits |
  | `store_id` | `S01` to `S20` |
  | dates | `yyyy-mm-dd`; times `yyyy-mm-dd HH:MM:SS` |
  | emails | lower case |
  | phones | UK numbers with spaces, e.g. `07700 900123` or `0113 496 0123` |
  | postcodes | UK format with a space, e.g. `LS1 4AB` (format only, not always a real area) |
  | barcodes | EAN-13 |

- **Customers:** many genuine customers share a name.
- **Volumes:** they follow the day of the week. `data/calendar.csv` lists trading events known in advance. Black Friday (27 November) brings much higher volumes and a markdown, which are expected, not a problem.

**Tables.**

| Table | What it is for |
|---|---|
| `batches` | One row per feed per day: row count, a hash of the column names as delivered, and `status` (`landed`, `missing`, `handed_off` or `promoted`). |
| `staging_orders`, `staging_customers`, `staging_products` | One row per delivered record:<br>• `payload`: the raw JSON, which your agent may repair<br>• `original`: the row as delivered<br>• `status`: `pending`, `clean`, `quarantined` or `awaiting_approval`; then the simulator's `promoted`, `merged` or `lost`<br>• `reason` |
| `customers`, `products`, `orders`, `stores` | Clean, promoted data with foreign keys: the source of truth for what is known. |
| `column_profiles` | Per batch and column:<br>• null rate, distinct count, minimum and maximum, format match<br>• the 30-day baseline of null rate and format match, learned only from rows that reached the core tables |
| `issues`, `fixes`, `quarantine`, `tickets`, `hand_offs`, `agent_log` | Written by your agent. |
| `sim_results` | Written by the simulator. Your agent cannot read it. |

**Rules of the game.**
- Your agent reads the data only through your MCP tools.
- The final session runs your agent on a scenario you have not seen, which the teaching team keeps: the same kinds of problem on other days and feeds, with other counts and variants.
- Each scenario bundle also holds what the simulator scores with, packed so it is not readable by accident. Unpacking it to read the answers would only defeat the point: your agent must judge from the data.
- Code that recognises a planted problem by its date, row count or column name will fail. Detect from profiles and baselines.

## 10. Troubleshooting

| You see | Do this |
|---|---|
| `relation "batches" does not exist` | The tables are created by `python generate_data.py`: run it first. |
| `Cannot connect to PostgreSQL` / `connection refused` | Start Docker Desktop, then `docker compose up -d --wait`. Check that `PG_PORT` in `.env` matches the port in use. |
| `password authentication failed for user "watchdog"` | You are connected to another PostgreSQL. Check the port is 15432 (or your `PG_PORT`). |
| `port is already allocated` | Set `PG_PORT` in `.env` to another port, e.g. `15433`. |
| `Ports are not available ... forbidden by its access permissions` (Windows) | Windows reserved that port. See the reserved ranges with `netsh int ipv4 show excludedportrange protocol=tcp` and set `PG_PORT` in `.env` to a port outside them. |
| `permission denied ... docker.sock` (Linux) | `sudo usermod -aG docker $USER`, then log out and in. |
| `Activate.ps1 cannot be loaded` (Windows) | See section 4: `Set-ExecutionPolicy`, or skip activation and run `.venv\Scripts\python.exe ...`. |
| `python` opens the Microsoft Store (Windows) | Use `py -3.13` instead, or reinstall Python with "Add to PATH" ticked. |
| `No matching distribution found for google-adk` | Your Python is older than 3.11. Create the `.venv` again with 3.13. |
| `Failed to build ... wheels` / `gcc` / `linker cc not found` | Your Python is 3.15, too new for some packages. Create the `.venv` again with 3.13. |
| `Cannot delete ...: is it open in Excel` | Close the CSV file in Excel, then run the script again. |
| `docker-compose: command not found`, or `--wait` is unknown | Use `docker compose` (with a space). On Linux, install the Compose plugin. |
| `toomanyrequests` while pulling the image | Docker Hub's download limit: wait a few minutes, or run `docker pull postgres:16` at home. |
| `The database already holds data` | You ran `generate_data.py` twice. See "Start completely from scratch" in section 6. |
| `This scenario was built on the data from generate_data.py --seed 42` | Your database holds other data (another `--seed`). Start completely from scratch (section 6). |
| `No scenario chosen yet` | Run `python scenario.py dev` first. |
| `Day N was delivered but never finished` | The simulator stopped halfway through a day (Ctrl+C, or a crash). Run `python simulator.py resume --agent ...`, or `python reset_sim.py` to start again. |
| `agent failed: timed out` / `exit code 1` | Your agent took longer than `--timeout` or crashed (its error is printed above). The day went on without it. |
| `No LLM configured` | Set `GOOGLE_API_KEY` or `WATCHDOG_MODEL` in `.env` (section 8). |
| `429` or `RESOURCE_EXHAUSTED` from Gemini | You hit the free-tier limit. Wait a minute, add retries with a pause, and use `--days` rather than rerunning all 28 days. |
| `ModuleNotFoundError: mcp.server.fastmcp` in code you found online | That code is for MCP 1.x. In 2.x use `from mcp.server import MCPServer`. |
| `your agent may ...` from the database | You tried something only the steward or the simulator may do (approve a fix, promote a row). See section 7. |
