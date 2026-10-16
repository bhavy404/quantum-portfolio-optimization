"""Plain finance maths: per-stock metrics, portfolio back-tests, drawdowns, correlation, risk-return cloud.

Conventions (shown to users in the app): returns are annualised averages of daily returns,
risk is annualised volatility, "return per unit of risk" is return / volatility with no
risk-free rate, portfolios are equal-weighted and rebalanced daily. Past results do not
predict future results.
"""
import itertools
import math

import numpy as np
import pandas as pd

TD = 252
MIN_DAYS = 200


def clean(prices: pd.DataFrame) -> pd.DataFrame:
    """Drop stocks with too little history and fill short gaps (holidays that differ between listings)."""
    px = prices.dropna(axis=1, thresh=MIN_DAYS).sort_index().ffill(limit=3)
    return px


def max_drawdown(growth: pd.Series) -> float:
    peak = growth.cummax()
    return float((growth / peak - 1.0).min())


def series_metrics(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) < 5:
        return {"annual_return": 0.0, "volatility": 0.0, "ratio": 0.0, "total_return": 0.0, "max_drawdown": 0.0, "worst_month": 0.0}
    growth = (1 + ret).cumprod()
    ann = float(ret.mean() * TD)
    vol = float(ret.std(ddof=1) * math.sqrt(TD))
    month = (growth / growth.shift(21) - 1.0).dropna()
    return {
        "annual_return": ann,
        "volatility": vol,
        "ratio": ann / vol if vol > 1e-12 else 0.0,
        "total_return": float(growth.iloc[-1] - 1.0),
        "max_drawdown": max_drawdown(growth),
        "worst_month": float(month.min()) if len(month) else 0.0,
    }


def stock_table(prices: pd.DataFrame, info: dict) -> list:
    """One row per stock for the screener."""
    px = clean(prices)
    ret = px.pct_change().dropna(how="all")
    rows = []
    for sym in px.columns:
        r = ret[sym].dropna()
        if len(r) < MIN_DAYS - 1:
            continue
        m = series_metrics(r)
        p = px[sym].dropna()
        one_year = float(p.iloc[-1] / p.iloc[-TD - 1] - 1.0) if len(p) > TD else None
        meta = info.get(sym, {})
        rows.append({
            "symbol": sym,
            "name": meta.get("name", sym),
            "sector": meta.get("sector", "Other"),
            "price": float(p.iloc[-1]),
            "return_1y": one_year,
            "annual_return": m["annual_return"],
            "volatility": m["volatility"],
            "ratio": m["ratio"],
            "max_drawdown": m["max_drawdown"],
        })
    if rows:
        vols = np.array([r["volatility"] for r in rows])
        lo, hi = np.quantile(vols, [1 / 3, 2 / 3])
        for r in rows:
            r["risk_level"] = "Low" if r["volatility"] <= lo else ("Medium" if r["volatility"] <= hi else "High")
    return rows


def split_index(n: int, train_share: float = 0.7) -> int:
    return int(n * train_share)


def basket_returns(ret: pd.DataFrame, cols) -> pd.Series:
    """Equal-weight, daily-rebalanced basket."""
    return ret[list(cols)].mean(axis=1)


def growth_series(ret: pd.Series, start: float = 100000.0) -> pd.Series:
    return start * (1 + ret.fillna(0)).cumprod()


def thin(index, *series, max_points: int = 260):
    """Down-sample long daily series for the browser while keeping first/last points."""
    n = len(index)
    step = max(1, n // max_points)
    keep = list(range(0, n, step))
    if keep[-1] != n - 1:
        keep.append(n - 1)
    dates = [index[i].strftime("%Y-%m-%d") for i in keep]
    out = [[round(float(s.iloc[i]), 2) for i in keep] for s in series]
    return dates, out


def risk_return_cloud(mu: np.ndarray, sigma: np.ndarray, k: int, limit: int = 3000):
    """Every possible k-stock equal-weight basket: (annual return, volatility, index tuple)."""
    n = len(mu)
    pts = []
    for combo in itertools.combinations(range(n), k):
        w = np.zeros(n)
        w[list(combo)] = 1.0 / k
        pts.append((float(w @ mu), float(math.sqrt(max(w @ sigma @ w, 0.0))), combo))
        if len(pts) >= limit:
            break
    return pts


def correlation(ret: pd.DataFrame, cols):
    c = ret[list(cols)].corr().fillna(0.0)
    return [[round(float(v), 3) for v in row] for row in c.values]
