from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import yfinance as yf
import numpy as np
import pandas as pd

from qiskit_optimization import QuadraticProgram
from qiskit_optimization.converters import LinearEqualityToPenalty
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA
from qiskit.primitives import StatevectorSampler as Sampler
from qiskit_optimization.algorithms import MinimumEigenOptimizer

from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
import os

app = FastAPI(
    title="Quantum Portfolio Optimization API", 
    description="S6 Dashboard API for QAOA-based portfolio optimization (IBM Simulator Supported)"
)

# Initialize IBM Qiskit Runtime Service if a token is available
# You can set this via environment variable IBM_QUANTUM_TOKEN before running the script.
# e.g., in terminal: set IBM_QUANTUM_TOKEN=your_token_here
IBM_TOKEN = os.environ.get("IBM_QUANTUM_TOKEN")
service = None
try:
    if IBM_TOKEN:
        service = QiskitRuntimeService(channel="ibm_quantum_platform", token=IBM_TOKEN)
        print("Successfully connected to IBM Quantum Runtime Service.")
    else:
        # Try loading saved account
        service = QiskitRuntimeService(channel="ibm_quantum_platform")
        print("Successfully loaded saved IBM Quantum account.")
except Exception as e:
    print(f"IBM Quantum Service not initialized. Falling back to local simulator. Reason: {e}")

class PortfolioRequest(BaseModel):
    tickers: List[str] = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META']
    start_date: str = "2023-01-01"
    end_date: str = "2024-01-01"
    risk_factor: float = 0.5  # q (0 to 1, higher means more weight on return)
    budget: int = 2           # K (number of stocks to select)
    use_ibm_simulator: bool = False # Set to True in the API to use IBM's cloud simulator

class PortfolioResponse(BaseModel):
    selected_stocks: List[str]
    individual_expected_returns: dict
    individual_risks_variance: dict
    train_return: float
    train_risk: float
    train_sharpe: float
    test_return: float
    test_risk: float
    test_sharpe: float

@app.post("/optimize", response_model=PortfolioResponse)
def optimize_portfolio(request: PortfolioRequest):
    if request.budget > len(request.tickers):
        raise HTTPException(status_code=400, detail="Budget K cannot be greater than the number of tickers.")
        
    tickers = request.tickers
    
    # Download dataset
    data = pd.DataFrame()
    for t in tickers:
        df = yf.download(t, start=request.start_date, end=request.end_date, progress=False)
        if df.empty:
            raise HTTPException(status_code=400, detail=f"Failed to download data for ticker {t}.")
            
        # Newer yfinance versions return a MultiIndex dataframe
        if isinstance(df.columns, pd.MultiIndex):
            data[t] = df[('Close', t)]
        else:
            data[t] = df['Close']
            
    if data.empty or data.isna().all().all():
        raise HTTPException(status_code=400, detail="Failed to download data for the provided tickers and dates.")
        
    # Calculate daily returns
    returns = data.pct_change().dropna()
    
    # Split data to approximate Train/Test performance metrics
    split_idx = int(len(returns) * 0.7)
    train_returns = returns.iloc[:split_idx]
    test_returns = returns.iloc[split_idx:]
    
    mu_train = train_returns.mean().values * 252
    sigma_train = train_returns.cov().values * 252
    
    # QUBO Formulation
    qp = QuadraticProgram()
    for ticker in tickers:
        qp.binary_var(ticker)
        
    q = request.risk_factor
    linear_dict = {tickers[i]: -q * mu_train[i] for i in range(len(tickers))}
    quadratic_dict = {}
    for i in range(len(tickers)):
        for j in range(len(tickers)):
            quadratic_dict[(tickers[i], tickers[j])] = (1-q) * sigma_train[i, j]
            
    qp.minimize(linear=linear_dict, quadratic=quadratic_dict)
    
    # Budget constraint: choose exactly K assets
    linear_constraint = {ticker: 1 for ticker in tickers}
    qp.linear_constraint(linear=linear_constraint, sense='==', rhs=request.budget, name='budget')
    
    lineq2penalty = LinearEqualityToPenalty()
    qubo = lineq2penalty.convert(qp)
    
    # Train QAOA Simulator
    if request.use_ibm_simulator and service:
        try:
            # Use IBM's high-performance cloud simulator
            backend = service.get_backend("ibmq_qasm_simulator")
            sampler = SamplerV2(backend=backend)
            print(f"Using IBM Cloud Simulator: {backend.name}")
        except Exception as e:
            print(f"Failed to use IBM Simulator, falling back to local. Error: {e}")
            sampler = Sampler()
    else:
        sampler = Sampler() # Local IBM-like Simulator
        
    cobyla = COBYLA(maxiter=100)
    qaoa = QAOA(sampler=sampler, optimizer=cobyla, reps=2)
    qaoa_optimizer = MinimumEigenOptimizer(qaoa)
    
    result = qaoa_optimizer.solve(qubo)
    
    selected_stocks = [tickers[i] for i in range(len(tickers)) if result.x[i] == 1.0]
    
    # Calculate Metrics
    weights = result.x / sum(result.x) if sum(result.x) > 0 else result.x
    
    train_port_return = float(np.dot(weights, mu_train))
    train_port_risk = float(np.dot(weights.T, np.dot(sigma_train, weights)))
    
    mu_test = test_returns.mean().values * 252
    sigma_test = test_returns.cov().values * 252
    
    test_port_return = float(np.dot(weights, mu_test))
    test_port_risk = float(np.dot(weights.T, np.dot(sigma_test, weights)))
    
    train_sharpe = train_port_return / np.sqrt(train_port_risk) if train_port_risk > 0 else 0.0
    test_sharpe = test_port_return / np.sqrt(test_port_risk) if test_port_risk > 0 else 0.0
    
    # Create dicts for raw dataset basis
    ind_returns = {tickers[i]: float(mu_train[i]) for i in range(len(tickers))}
    # The diagonal of the covariance matrix represents the individual variance (risk) of each stock
    ind_risks = {tickers[i]: float(sigma_train[i][i]) for i in range(len(tickers))}
    
    return PortfolioResponse(
        selected_stocks=selected_stocks,
        individual_expected_returns=ind_returns,
        individual_risks_variance=ind_risks,
        train_return=train_port_return,
        train_risk=train_port_risk,
        train_sharpe=train_sharpe,
        test_return=test_port_return,
        test_risk=test_port_risk,
        test_sharpe=test_sharpe
    )

if __name__ == "__main__":
    import uvicorn
    print("Starting Quantum Portfolio Dashboard API on http://127.0.0.1:8000")
    uvicorn.run("dashboard_api:app", host="127.0.0.1", port=8000, reload=True)