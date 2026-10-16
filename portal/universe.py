"""Stock universe: a curated large-cap starter list plus the full NSE equity master list.

The starter list (tier "core") is what the screener analyses by default, so pages load fast.
The NSE master list (tier "listed") makes every listed company searchable; a stock's prices
are downloaded the first time someone analyses it.
Index membership changes over time, so treat the starter list as a starting point.
"""
import csv
import io
import threading
import urllib.request

from . import db

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BENCHMARK = "^NSEI"  # Nifty 50 index, used as the comparison line

# (symbol without .NS, company name, sector)
CORE = [
    ("HDFCBANK", "HDFC Bank", "Banks"), ("ICICIBANK", "ICICI Bank", "Banks"),
    ("SBIN", "State Bank of India", "Banks"), ("KOTAKBANK", "Kotak Mahindra Bank", "Banks"),
    ("AXISBANK", "Axis Bank", "Banks"), ("INDUSINDBK", "IndusInd Bank", "Banks"),
    ("BANKBARODA", "Bank of Baroda", "Banks"), ("PNB", "Punjab National Bank", "Banks"),
    ("BAJFINANCE", "Bajaj Finance", "Finance"), ("BAJAJFINSV", "Bajaj Finserv", "Finance"),
    ("SHRIRAMFIN", "Shriram Finance", "Finance"), ("CHOLAFIN", "Cholamandalam Investment", "Finance"),
    ("MUTHOOTFIN", "Muthoot Finance", "Finance"), ("SBILIFE", "SBI Life Insurance", "Insurance"),
    ("HDFCLIFE", "HDFC Life Insurance", "Insurance"),
    ("TCS", "Tata Consultancy Services", "IT"), ("INFY", "Infosys", "IT"),
    ("HCLTECH", "HCL Technologies", "IT"), ("WIPRO", "Wipro", "IT"),
    ("TECHM", "Tech Mahindra", "IT"), ("LTIM", "LTIMindtree", "IT"),
    ("RELIANCE", "Reliance Industries", "Energy"), ("ONGC", "Oil & Natural Gas Corp", "Energy"),
    ("BPCL", "Bharat Petroleum", "Energy"), ("IOC", "Indian Oil Corporation", "Energy"),
    ("GAIL", "GAIL (India)", "Energy"), ("COALINDIA", "Coal India", "Energy"),
    ("NTPC", "NTPC", "Power"), ("POWERGRID", "Power Grid Corporation", "Power"),
    ("ADANIPORTS", "Adani Ports & SEZ", "Infrastructure"), ("ADANIENT", "Adani Enterprises", "Infrastructure"),
    ("LT", "Larsen & Toubro", "Infrastructure"), ("SIEMENS", "Siemens", "Industrials"),
    ("HAVELLS", "Havells India", "Industrials"), ("BEL", "Bharat Electronics", "Industrials"),
    ("HAL", "Hindustan Aeronautics", "Industrials"),
    ("HINDUNILVR", "Hindustan Unilever", "Consumer"), ("ITC", "ITC", "Consumer"),
    ("NESTLEIND", "Nestle India", "Consumer"), ("BRITANNIA", "Britannia Industries", "Consumer"),
    ("TATACONSUM", "Tata Consumer Products", "Consumer"), ("DABUR", "Dabur India", "Consumer"),
    ("GODREJCP", "Godrej Consumer Products", "Consumer"), ("ASIANPAINT", "Asian Paints", "Consumer"),
    ("PIDILITIND", "Pidilite Industries", "Consumer"), ("TITAN", "Titan Company", "Consumer"),
    ("TRENT", "Trent", "Retail"), ("DMART", "Avenue Supermarts (DMart)", "Retail"),
    ("MARUTI", "Maruti Suzuki", "Auto"), ("M&M", "Mahindra & Mahindra", "Auto"),
    ("BAJAJ-AUTO", "Bajaj Auto", "Auto"), ("EICHERMOT", "Eicher Motors", "Auto"),
    ("HEROMOTOCO", "Hero MotoCorp", "Auto"), ("TVSMOTOR", "TVS Motor", "Auto"),
    ("SUNPHARMA", "Sun Pharmaceutical", "Healthcare"), ("DRREDDY", "Dr. Reddy's Laboratories", "Healthcare"),
    ("CIPLA", "Cipla", "Healthcare"), ("DIVISLAB", "Divi's Laboratories", "Healthcare"),
    ("LUPIN", "Lupin", "Healthcare"), ("APOLLOHOSP", "Apollo Hospitals", "Healthcare"),
    ("ULTRACEMCO", "UltraTech Cement", "Materials"), ("GRASIM", "Grasim Industries", "Materials"),
    ("TATASTEEL", "Tata Steel", "Metals"), ("JSWSTEEL", "JSW Steel", "Metals"),
    ("HINDALCO", "Hindalco Industries", "Metals"), ("VEDL", "Vedanta", "Metals"),
    ("BHARTIARTL", "Bharti Airtel", "Telecom"), ("IRCTC", "IRCTC", "Services"),
]

SECTOR_BY_SYMBOL = {f"{s}.NS": sec for s, _, sec in CORE}


def _yahoo(sym: str) -> str:
    return f"{sym}.NS"


def seed_core():
    """Insert the curated starter list (idempotent)."""
    rows = [(_yahoo(s), n, sec, "core") for s, n, sec in CORE]
    db.executemany(
        "INSERT INTO stocks(symbol,name,sector,tier) VALUES (?,?,?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, sector=excluded.sector, tier='core'",
        rows,
    )


def refresh_from_nse(timeout: int = 20) -> int:
    """Download NSE's official list of listed companies and add them to the search index."""
    req = urllib.request.Request(
        NSE_LIST_URL,
        headers={"User-Agent": "Mozilla/5.0 (QPortal)", "Accept": "text/csv,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    rows = []
    for rec in csv.DictReader(io.StringIO(text)):
        rec = {k.strip(): (v or "").strip() for k, v in rec.items() if k}
        if rec.get("SERIES") != "EQ":
            continue
        sym = rec.get("SYMBOL")
        if not sym:
            continue
        rows.append((_yahoo(sym), rec.get("NAME OF COMPANY") or sym, "Other", "listed"))
    # Core rows keep their curated sector/tier; only new symbols are added.
    db.executemany("INSERT OR IGNORE INTO stocks(symbol,name,sector,tier) VALUES (?,?,?,?)", rows)
    return len(rows)


def refresh_from_nse_async():
    def run():
        try:
            n = refresh_from_nse()
            print(f"[universe] NSE list loaded: {n} companies")
        except Exception as exc:  # network blocked, NSE changed format, etc.
            print(f"[universe] NSE list not loaded ({exc}). Using the starter list only.")

    threading.Thread(target=run, daemon=True).start()


def search(q: str, limit: int = 20):
    q = f"%{q.strip().upper()}%"
    return db.query(
        "SELECT symbol,name,sector,tier FROM stocks "
        "WHERE upper(symbol) LIKE ? OR upper(name) LIKE ? "
        "ORDER BY CASE tier WHEN 'core' THEN 0 ELSE 1 END, symbol LIMIT ?",
        (q, q, limit),
    )


def core_symbols():
    return [r["symbol"] for r in db.query("SELECT symbol FROM stocks WHERE tier='core' ORDER BY symbol")]


def info(symbols):
    if not symbols:
        return {}
    marks = ",".join("?" * len(symbols))
    return {r["symbol"]: r for r in db.query(f"SELECT symbol,name,sector,tier FROM stocks WHERE symbol IN ({marks})", tuple(symbols))}
