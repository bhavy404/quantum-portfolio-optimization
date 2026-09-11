"""
dashboard_api.py
================
FastAPI backend service for QAOA-based quantum portfolio optimization.

Features:
  - CORS middleware enabled for web dashboards
  - Secure credential management via python-dotenv (credentials never leaked/logged)
  - Robust Pydantic request validation (tickers, date ranges, budget, risk factor)
  - High-performance batch data downloading via yfinance
  - QUBO formulation and Qiskit 1.0+ QAOA optimization
"""

import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator
import yfinance as yf
import numpy as np
import pandas as pd

from qkd_bb84 import (
    BB84Session,
    encrypt_portfolio_payload,
    decrypt_portfolio_payload,
)

from qiskit_optimization import QuadraticProgram
from qiskit_optimization.converters import LinearEqualityToPenalty
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA
from qiskit.primitives import StatevectorSampler as Sampler
from qiskit_optimization.algorithms import MinimumEigenOptimizer

# ---------------------------------------------------------------------------
# Environment & Credential Loading (Strictly Masked)
# ---------------------------------------------------------------------------

load_dotenv()

def _mask(value: Optional[str], show_chars: int = 4) -> str:
    """Safely mask credential strings for logging."""
    if not value:
        return "<empty>"
    if len(value) <= show_chars * 2:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars * 2) + value[-show_chars:]

IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN", "").strip()
IBM_INSTANCE = os.getenv("IBM_QUANTUM_INSTANCE", "").strip()

if IBM_TOKEN:
    print(f"[config] IBM Quantum Token detected ({_mask(IBM_TOKEN)}).")
else:
    print("[config] No IBM Quantum Token configured. Local simulator will be used.")

# ---------------------------------------------------------------------------
# FastAPI Application & Middleware
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Quantum Portfolio Optimization API",
    description="S6 Dashboard API for QAOA-based portfolio optimization with Markowitz QUBO formulation.",
    version="1.0.0",
)

# Enable CORS for browser-based dashboard frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust to specific frontend URLs in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class PortfolioRequest(BaseModel):
    tickers: List[str] = Field(
        default=["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
        description="List of stock ticker symbols (2 to 20 assets).",
    )
    start_date: str = Field(
        default="2023-01-01",
        description="Start date for historical price data (YYYY-MM-DD).",
    )
    end_date: str = Field(
        default="2024-01-01",
        description="End date for historical price data (YYYY-MM-DD).",
    )
    risk_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Risk tolerance factor q in [0, 1]. Higher means more weight on return.",
    )
    budget: int = Field(
        default=2,
        ge=1,
        description="Number of assets K to select in optimal portfolio.",
    )
    reps: int = Field(
        default=2,
        ge=1,
        le=5,
        description="QAOA circuit repetition depth p.",
    )
    maxiter: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Maximum classical optimizer iterations (COBYLA).",
    )

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, v: List[str]) -> List[str]:
        cleaned = [t.strip().upper() for t in v if t and t.strip()]
        unique_tickers = list(dict.fromkeys(cleaned))
        if len(unique_tickers) < 2:
            raise ValueError("At least 2 distinct ticker symbols must be provided.")
        if len(unique_tickers) > 20:
            raise ValueError("A maximum of 20 tickers is supported per optimization request.")
        return unique_tickers

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Date '{v}' is not in valid format 'YYYY-MM-DD'.")
        return v

    @model_validator(mode="after")
    def validate_dates_and_budget(self):
        start_dt = datetime.strptime(self.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(self.end_date, "%Y-%m-%d")
        if start_dt >= end_dt:
            raise ValueError(f"start_date ({self.start_date}) must be strictly earlier than end_date ({self.end_date}).")
        if self.budget > len(self.tickers):
            raise ValueError(f"Budget K ({self.budget}) cannot exceed number of tickers ({len(self.tickers)}).")
        return self


class PortfolioResponse(BaseModel):
    status: str
    selected_stocks: List[str]
    binary_configuration: List[int]
    budget_satisfied: bool
    individual_expected_returns: Dict[str, float]
    individual_risks_variance: Dict[str, float]
    train_return: float
    train_risk: float
    train_sharpe: float
    test_return: float
    test_risk: float
    test_sharpe: float


# ---------------------------------------------------------------------------
# QKD Request & Response Schemas
# ---------------------------------------------------------------------------

class QKDKeyRequest(BaseModel):
    num_qubits: int = Field(default=16, ge=8, le=64, description="Number of qubits to transmit in BB84 protocol.")
    enable_eve: bool = Field(default=False, description="Simulate Eve interception attack.")
    backend_name: Optional[str] = Field(default="ibm_fez", description="Target real IBM Quantum QPU name.")
    target: str = Field(default="hardware", description="'hardware' for Real IBM QPU, 'simulator' for Local Aer.")


class EncryptPayloadRequest(BaseModel):
    payload: Dict[str, Any]
    key_hex: Optional[str] = None


class DecryptPayloadRequest(BaseModel):
    nonce_hex: str
    ciphertext_hex: str
    key_hex: Optional[str] = None


# Global active session state
ACTIVE_QKD_STATE: Dict[str, Any] = {
    "key_hex": None,
    "key_bytes": None,
    "last_summary": None,
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
def serve_dashboard():
    """Serves the interactive QPortal web dashboard."""
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>QPortal Dashboard file not found</h1>")


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
def get_dashboard():
    return serve_dashboard()


@app.get("/health", tags=["Health"])
def health_check():
    return {
        "status": "ok",
        "ibm_token_configured": bool(IBM_TOKEN),
    }


@app.post("/optimize", response_model=PortfolioResponse, tags=["Optimization"])
def optimize_portfolio(request: PortfolioRequest):
    tickers = request.tickers

    # 1. Download Historical Financial Data in Batch
    try:
        raw_df = yf.download(
            tickers,
            start=request.start_date,
            end=request.end_date,
            progress=False,
            auto_adjust=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch market data from Yahoo Finance: {exc}",
        )

    if raw_df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No market data returned for the specified tickers and date range.",
        )

    # Extract price matrix safely
    if isinstance(raw_df.columns, pd.MultiIndex):
        if "Close" in raw_df.columns.levels[0]:
            data = raw_df["Close"][tickers].dropna()
        elif "Adj Close" in raw_df.columns.levels[0]:
            data = raw_df["Adj Close"][tickers].dropna()
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Price column ('Close' or 'Adj Close') not found in downloaded dataset.",
            )
    else:
        col = "Close" if "Close" in raw_df.columns else ("Adj Close" if "Adj Close" in raw_df.columns else raw_df.columns[0])
        data = raw_df[[col]].rename(columns={col: tickers[0]}).dropna()

    if len(data) < 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient trading days ({len(data)} rows) in selected date range. Minimum 20 days required.",
        )

    # 2. Compute Returns and Annualized Moments
    returns = data.pct_change().dropna()
    split_idx = int(len(returns) * 0.7)
    if split_idx < 5 or (len(returns) - split_idx) < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Data sample too small to compute train/test performance split.",
        )

    train_returns = returns.iloc[:split_idx]
    test_returns = returns.iloc[split_idx:]

    mu_train = train_returns.mean().values * 252
    sigma_train = train_returns.cov().values * 252

    mu_test = test_returns.mean().values * 252
    sigma_test = test_returns.cov().values * 252

    # 3. Formulate Markowitz QUBO
    qp = QuadraticProgram("portfolio_qaoa")
    for ticker in tickers:
        qp.binary_var(ticker)

    q = request.risk_factor
    linear_dict = {tickers[i]: -q * mu_train[i] for i in range(len(tickers))}
    quadratic_dict = {}
    for i in range(len(tickers)):
        for j in range(len(tickers)):
            quadratic_dict[(tickers[i], tickers[j])] = (1.0 - q) * sigma_train[i, j]

    qp.minimize(linear=linear_dict, quadratic=quadratic_dict)

    # Budget Constraint: sum(x_i) == K
    linear_constraint = {ticker: 1 for ticker in tickers}
    qp.linear_constraint(
        linear=linear_constraint,
        sense="==",
        rhs=request.budget,
        name="budget",
    )

    converter = LinearEqualityToPenalty()
    qubo = converter.convert(qp)

    # 4. Execute QAOA via StatevectorSampler
    try:
        sampler = Sampler(default_shots=1024)
        cobyla = COBYLA(maxiter=request.maxiter)
        qaoa = QAOA(sampler=sampler, optimizer=cobyla, reps=request.reps)
        qaoa_optimizer = MinimumEigenOptimizer(qaoa)
        result = qaoa_optimizer.solve(qubo)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"QAOA solver execution failed: {exc}",
        )

    binary_vector = [int(round(val)) for val in result.x]
    selected_stocks = [tickers[i] for i, val in enumerate(binary_vector) if val == 1]
    budget_satisfied = len(selected_stocks) == request.budget

    # 5. Compute Financial Portfolio Metrics
    if len(selected_stocks) > 0:
        weights = np.array(binary_vector, dtype=float) / sum(binary_vector)
        train_return = float(np.dot(weights, mu_train))
        train_risk = float(np.dot(weights.T, np.dot(sigma_train, weights)))
        test_return = float(np.dot(weights, mu_test))
        test_risk = float(np.dot(weights.T, np.dot(sigma_test, weights)))
        train_sharpe = float(train_return / np.sqrt(train_risk)) if train_risk > 1e-12 else 0.0
        test_sharpe = float(test_return / np.sqrt(test_risk)) if test_risk > 1e-12 else 0.0
    else:
        train_return = 0.0
        train_risk = 0.0
        train_sharpe = 0.0
        test_return = 0.0
        test_risk = 0.0
        test_sharpe = 0.0

    ind_returns = {tickers[i]: float(mu_train[i]) for i in range(len(tickers))}
    ind_risks = {tickers[i]: float(sigma_train[i][i]) for i in range(len(tickers))}

    return PortfolioResponse(
        status="success",
        selected_stocks=selected_stocks,
        binary_configuration=binary_vector,
        budget_satisfied=budget_satisfied,
        individual_expected_returns=ind_returns,
        individual_risks_variance=ind_risks,
        train_return=train_return,
        train_risk=train_risk,
        train_sharpe=train_sharpe,
        test_return=test_return,
        test_risk=test_risk,
        test_sharpe=test_sharpe,
    )


# ---------------------------------------------------------------------------
# QKD Protocol & Cryptography Endpoints
# ---------------------------------------------------------------------------

@app.post("/qkd/generate-key", tags=["QKD Security"])
def generate_qkd_key(request: QKDKeyRequest):
    """
    Executes BB84 Quantum Key Distribution on real IBM Quantum QPUs or local simulator.
    Performs state preparation, sifting, QBER estimation, and derives a 256-bit AES key.
    """
    session = BB84Session(
        num_qubits=request.num_qubits,
        enable_eve=request.enable_eve,
        backend_name=request.backend_name,
    )

    try:
        if request.target == "hardware":
            summary = session.execute_on_ibm_qpu()
        else:
            summary = session.execute_local_simulation()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"BB84 quantum execution failed: {exc}",
        )

    # Save active key if successfully established
    if session.derived_key_bytes:
        ACTIVE_QKD_STATE["key_hex"] = session.derived_key_hex
        ACTIVE_QKD_STATE["key_bytes"] = session.derived_key_bytes
        ACTIVE_QKD_STATE["last_summary"] = summary
    else:
        ACTIVE_QKD_STATE["key_hex"] = None
        ACTIVE_QKD_STATE["key_bytes"] = None
        ACTIVE_QKD_STATE["last_summary"] = summary

    return summary


@app.post("/qkd/encrypt-portfolio", tags=["QKD Security"])
def encrypt_portfolio(request: EncryptPayloadRequest):
    """
    Encrypts a portfolio allocation payload using AES-256-GCM with the active QPU quantum key.
    """
    key_bytes = None
    if request.key_hex:
        try:
            key_bytes = bytes.fromhex(request.key_hex)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hexadecimal key format.")
    elif ACTIVE_QKD_STATE["key_bytes"]:
        key_bytes = ACTIVE_QKD_STATE["key_bytes"]

    if not key_bytes or len(key_bytes) != 32:
        # Generate an emergency key via simulator if none established yet
        fallback_session = BB84Session(num_qubits=16, enable_eve=False)
        fallback_session.execute_local_simulation()
        key_bytes = fallback_session.derived_key_bytes
        ACTIVE_QKD_STATE["key_bytes"] = key_bytes
        ACTIVE_QKD_STATE["key_hex"] = fallback_session.derived_key_hex

    try:
        encrypted_result = encrypt_portfolio_payload(request.payload, key_bytes)
        return {
            "status": "success",
            "key_hex": key_bytes.hex(),
            "encrypted": encrypted_result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Encryption failed: {exc}")


@app.post("/qkd/decrypt-payload", tags=["QKD Security"])
def decrypt_payload(request: DecryptPayloadRequest):
    """
    Decrypts an encrypted portfolio payload using AES-256-GCM and the quantum key.
    """
    key_bytes = None
    if request.key_hex:
        try:
            key_bytes = bytes.fromhex(request.key_hex)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid hexadecimal key format.")
    elif ACTIVE_QKD_STATE["key_bytes"]:
        key_bytes = ACTIVE_QKD_STATE["key_bytes"]

    if not key_bytes:
        raise HTTPException(status_code=400, detail="No active quantum key found. Run BB84 first.")

    try:
        decrypted = decrypt_portfolio_payload(
            {"nonce_hex": request.nonce_hex, "ciphertext_hex": request.ciphertext_hex},
            key_bytes,
        )
        return decrypted
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Decryption / authentication tag verification failed: {exc}",
        )


@app.get("/qkd/status", tags=["QKD Security"])
def qkd_status():
    """Returns active QKD session status and key metadata."""
    return {
        "active_key_established": bool(ACTIVE_QKD_STATE["key_bytes"]),
        "key_hex": ACTIVE_QKD_STATE["key_hex"],
        "last_summary": ACTIVE_QKD_STATE["last_summary"],
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting Quantum Portfolio Dashboard API on http://127.0.0.1:8000")
    uvicorn.run("dashboard_api:app", host="127.0.0.1", port=8000, reload=True)