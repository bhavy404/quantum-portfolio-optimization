# QPortal web app

A website where a signed-in user compares Indian stocks, sets a comfort level (Safety ↔ Growth), and gets a
suggested basket with charts. The basket is chosen with QAOA and cross-checked against an exact search.
Results are saved encrypted with a key built from BB84 circuits.

## Run it (from the repository root)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r portal/requirements.txt
uvicorn portal.main:app --port 8000
# open http://127.0.0.1:8000
```

The first start downloads about three years of daily prices for the starter list of large companies and
loads NSE's list of listed companies for search. Prices come from yfinance (an unofficial source: fine for a
project; use a licensed data feed before running a public service).

> Want the IBM quantum features? Follow [docs/IBM_SETUP.md](../docs/IBM_SETUP.md).

## Settings (environment variables or `.env`)

| Name | Meaning | Default |
|---|---|---|
| `QPORTAL_SECRET` | Secret for login tokens and key hardening. **Set this in production.** | auto-generated file in `portal/data/` |
| `QPORTAL_KEY_TARGET` | Where BB84 circuits run: `simulator` or `hardware` | `simulator` |
| `IBM_QUANTUM_TOKEN`, `IBM_QUANTUM_INSTANCE`, `IBM_BACKEND_NAME`, `IBM_WAIT_SECONDS` | Enables the IBM quantum processor option and hardware BB84 keys | unset |
| `QPORTAL_ORIGINS` | Comma-separated allowed browser origins if the front end is hosted elsewhere | none |
| `QPORTAL_SYNTHETIC` | `1` = fake prices for automated tests only (the site shows a red banner) | `0` |

## How the pieces fit

```
portal/universe.py   stock list (starter set + NSE master list)
portal/prices.py     price cache in SQLite (four-hour refresh)
portal/analytics.py  growth, risk, drawdown, correlation, back-test maths
portal/solver.py     QUBO + QAOA (exact simulator or IBM hardware) + exact classical check
portal/keybank.py    BB84 key bank, per-user keys, AES-256-GCM encryption of saved results
portal/jobs.py       the recommendation job
portal/main.py       FastAPI routes + serves portal/static (the website)
```

## Honest notes

* QAOA does not beat a normal computer at this size (12 stocks = 4,096 states). The results page shows how
  the circuit did and always shows the exact classical answer next to it.
* The BB84 circuits run on the server, so this is not a quantum link to the user's device. The user key is
  also mixed with a server secret, so it is never weaker than normal server-side key management.
* Results look backwards. They are not investment advice.


### Market index data

QPortal makes NIFTY 50 (`^NSEI`) and BSE SENSEX (`^BSESN`) searchable in the stock picker. They are index series, not directly tradable individual stocks. The background worker attempts to refresh the core stock universe and both indices every four hours while the app process is running. The default cache freshness window is also four hours (`QPORTAL_PRICE_MAX_AGE_HOURS` can override it). Prices come from Yahoo Finance via yfinance and may be delayed, unavailable, or unchanged outside market hours; this is not a guaranteed live quote feed.


### Market index cards

The Market Overview page requests NIFTY 50 and BSE SENSEX quotes every 30 seconds while the page is open. The backend tries Yahoo Finance 1-minute intraday bars and falls back to the latest daily close when intraday data is unavailable. Yahoo Finance is an unofficial source and may delay, throttle, or omit quotes; this is not guaranteed real-time market data.

## Fundamental analysis

The signed-in app includes a **Fundamentals** page for comparing up to eight individual NSE-listed companies across ten provider-reported metrics: trailing P/E, EPS, price-to-book, ROE, ROCE (when supplied), debt-to-equity, revenue growth, net profit margin, dividend yield, and market capitalization. Search and select companies, load the comparison, and filter by sector or metric. Values come from Yahoo Finance's company information and are cached in memory for up to six hours; fields may be missing, delayed, or provider-defined. This feature is informational and is not investment advice. Market indices are not individual companies and are excluded from fundamental comparisons.


## Mutual funds and sector-mix tools

QPortal now includes two additional signed-in pages in the dashboard:

- **Mutual funds** (`#/mutual-funds`): search Indian mutual-fund schemes through MFAPI, compare latest reported NAV and approximate 1-year return / 3-year CAGR / 5-year CAGR from NAV history, and use an illustrative SIP calculator. NAV history is cached in memory for one hour. The fund comparison does not currently include expense ratio, exit load, tax, or risk-adjusted metrics.
- **Sector mix** (`#/sector-mix`): select at least two sectors, assign relative weights, and create an illustrative allocation across example stocks in QPortal's curated starter universe. Sector weights are normalized to 100%; up to four starter stocks per chosen sector are split equally within that sector. This is an allocation illustration, not a risk/return optimizer or trade execution feature.

Mutual-fund data is retrieved from the public MFAPI service, which republishes reported Indian AMFI NAV history. The service requires internet access and may be unavailable or delayed. Historical performance is not a guarantee of future returns; verify scheme documents and current NAV data before investing.

## Fair quantum-vs-classical comparison

Every completed analysis result includes a **Fair comparison: QAOA vs classical search** panel in the result view. Both approaches use the same available stock universe, training-period return/risk estimates, objective, and exactly-`k` stock constraint. The exact classical search enumerates every feasible basket and is the reference baseline for the current problem size.

The panel reports the exact and selected objective scores, classical search time, QAOA/extra time, whether the selected basket matches the exact optimum, and the objective gap where that percentage is meaningful. The “How this was chosen” section additionally shows the exact basket and sampling information. Selecting the classical engine is clearly labelled as a baseline run, not as a quantum comparison.

**Interpretation limit:** this project does not claim quantum advantage. The simulator runs on a classical computer, and exact enumeration is practical for the small shortlist used here. Hardware runs train circuit angles on a simulator before executing the circuit on IBM hardware, so the comparison is not a fully hardware-trained end-to-end timing comparison.

### Verify the comparison integration

From the repository root, run:

```bash
python -m unittest discover -s tests -v
```

The regression tests check that the exact classical baseline returns the expected comparison metadata and that the result UI includes the comparison panel. A real IBM hardware run additionally requires valid IBM Quantum credentials and a supported backend; these unit tests do not submit paid/queued hardware jobs.


## Sector-wise quantum pick and the method comparison

* **Sector mix, step 4** (Sector mix page): after the sector weights are set, "Run sector-wise quantum pick" short-lists the best-looking few stocks in each chosen sector, turns the weights into per-sector stock counts, and runs QAOA over those stocks. One XY ring and one Dicke start state per sector keep every measured basket at the right count in every sector (`portal/sector.py`). It runs on the simulator or on IBM (about 9 qubits for 3 sectors of 3 stocks).
* **Quantum vs ordinary methods** (every result page): the same problem is solved by exact search, greedy selection, simulated annealing and random search (`portal/combo.py`), and shown next to QAOA with the gap to the best basket, its rank, the number of objective evaluations and the time.
* **Benchmark over many instances:** `python -m benchmarks.compare_methods_benchmark out.json` (seeded, about 4 minutes). At these sizes QAOA's most frequent basket is the exact best only 20-70% of the time, and greedy or annealing with a small budget usually match it. No quantum advantage is claimed.
* **Tests:** `python -m unittest discover -s tests -v`.
