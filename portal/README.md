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

## Settings (environment variables or `.env`)

| Name | Meaning | Default |
|---|---|---|
| `QPORTAL_SECRET` | Secret for login tokens and key hardening. **Set this in production.** | auto-generated file in `portal/data/` |
| `QPORTAL_KEY_TARGET` | Where BB84 circuits run: `simulator` or `hardware` | `simulator` |
| `IBM_QUANTUM_TOKEN`, `IBM_BACKEND_NAME` | Enables the IBM quantum processor option and hardware BB84 keys | unset |
| `QPORTAL_ORIGINS` | Comma-separated allowed browser origins if the front end is hosted elsewhere | none |
| `QPORTAL_SYNTHETIC` | `1` = fake prices for automated tests only (the site shows a red banner) | `0` |

## How the pieces fit

```
portal/universe.py   stock list (starter set + NSE master list)
portal/prices.py     price cache in SQLite (nightly refresh)
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
