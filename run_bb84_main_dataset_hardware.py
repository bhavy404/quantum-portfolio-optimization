"""
run_bb84_main_dataset_hardware.py
=================================
End-to-end execution of:
  1. Portfolio Optimization on the Main Dataset (Indian Bluechips: RELIANCE.NS, TCS.NS, INFY.NS, HDFCBANK.NS, ICICIBANK.NS)
     using Markowitz QUBO + QAOA.
  2. Live BB84 Quantum Key Distribution (QKD) executed on a REAL IBM Quantum QPU (auto-selecting least busy backend).
  3. AES-256-GCM authenticated payload encryption of the computed portfolio results using the hardware-derived 256-bit quantum key.
  4. Client-side decryption and verification of data fidelity.
"""

import os
import sys
import json
import time
from typing import Dict, Any

from dotenv import load_dotenv
import numpy as np
import pandas as pd
import yfinance as yf

# Qiskit Optimization & QAOA
from qiskit_optimization import QuadraticProgram
from qiskit_optimization.converters import LinearEqualityToPenalty
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA
from qiskit.primitives import StatevectorSampler as Sampler
from qiskit_optimization.algorithms import MinimumEigenOptimizer

# IBM Quantum Runtime & BB84
from qiskit_ibm_runtime import QiskitRuntimeService
from qkd_bb84 import (
    BB84Session,
    encrypt_portfolio_payload,
    decrypt_portfolio_payload,
)

load_dotenv()


def step_1_optimize_main_dataset() -> Dict[str, Any]:
    print("\n" + "=" * 70)
    print("  STEP 1: PORTFOLIO OPTIMIZATION ON MAIN DATASET (INDIAN BLUECHIPS)")
    print("=" * 70)

    tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]
    start_date = "2023-01-01"
    end_date = "2024-01-01"
    q = 0.5  # Risk tolerance factor
    K = 2    # Budget constraint: choose 2 assets

    print(f"[*] Downloading market prices for {tickers}...")
    print(f"[*] Period: {start_date} to {end_date}")

    raw_df = yf.download(tickers, start=start_date, end=end_date, progress=False, auto_adjust=False)
    if raw_df.empty:
        raise RuntimeError("Failed to fetch historical data from Yahoo Finance.")

    if isinstance(raw_df.columns, pd.MultiIndex):
        if "Close" in raw_df.columns.levels[0]:
            data = raw_df["Close"][tickers].dropna()
        elif "Adj Close" in raw_df.columns.levels[0]:
            data = raw_df["Adj Close"][tickers].dropna()
        else:
            raise KeyError("Neither 'Close' nor 'Adj Close' found.")
    else:
        col = "Close" if "Close" in raw_df.columns else "Adj Close"
        data = raw_df[[col]].rename(columns={col: tickers[0]}).dropna()

    print(f"[*] Successfully downloaded {len(data)} trading days.")

    returns = data.pct_change().dropna()
    split_idx = int(len(returns) * 0.7)
    train_returns = returns.iloc[:split_idx]
    test_returns = returns.iloc[split_idx:]

    mu_train = train_returns.mean().values * 252
    sigma_train = train_returns.cov().values * 252

    mu_test = test_returns.mean().values * 252
    sigma_test = test_returns.cov().values * 252

    print("\n[*] Annualized Expected Returns (Train):")
    for i, t in enumerate(tickers):
        print(f"    - {t:<15}: {mu_train[i] * 100:.2f}%")

    print("\n[*] Formulating Markowitz QUBO (q = 0.5, budget K = 2)...")
    qp = QuadraticProgram("indian_bluechips_portfolio")
    for ticker in tickers:
        qp.binary_var(ticker)

    linear_dict = {tickers[i]: -q * mu_train[i] for i in range(len(tickers))}
    quadratic_dict = {
        (tickers[i], tickers[j]): (1.0 - q) * sigma_train[i, j]
        for i in range(len(tickers))
        for j in range(len(tickers))
    }
    qp.minimize(linear=linear_dict, quadratic=quadratic_dict)

    # Budget constraint: sum(x_i) == K
    linear_constraint = {ticker: 1 for ticker in tickers}
    qp.linear_constraint(linear=linear_constraint, sense="==", rhs=K, name="budget")

    # Quadratic Penalty formulation
    qubo = LinearEqualityToPenalty().convert(qp)

    print("[*] Solving Markowitz QUBO using QAOA (depth p=2, COBYLA)...")
    sampler = Sampler(default_shots=1024)
    cobyla = COBYLA(maxiter=100)
    qaoa = QAOA(sampler=sampler, optimizer=cobyla, reps=2)
    qaoa_optimizer = MinimumEigenOptimizer(qaoa)
    result = qaoa_optimizer.solve(qubo)

    binary_vector = [int(round(val)) for val in result.x]
    selected_stocks = [tickers[i] for i, val in enumerate(binary_vector) if val == 1]
    weights = np.array(binary_vector, dtype=float) / sum(binary_vector)

    train_return = float(np.dot(weights, mu_train))
    train_risk = float(np.dot(weights.T, np.dot(sigma_train, weights)))
    test_return = float(np.dot(weights, mu_test))
    test_risk = float(np.dot(weights.T, np.dot(sigma_test, weights)))
    train_sharpe = float(train_return / np.sqrt(train_risk)) if train_risk > 1e-12 else 0.0
    test_sharpe = float(test_return / np.sqrt(test_risk)) if test_risk > 1e-12 else 0.0

    print("\n" + "-" * 70)
    print("  QAOA OPTIMIZATION RESULTS")
    print("-" * 70)
    print(f"  Selected Optimal Assets : {selected_stocks}")
    print(f"  Binary Allocation Vector: {binary_vector}")
    print(f"  Train Expected Return   : {train_return * 100:.2f}%")
    print(f"  Train Risk (Variance)   : {train_risk:.4f}")
    print(f"  Train Sharpe Ratio      : {train_sharpe:.4f}")
    print(f"  Test Expected Return    : {test_return * 100:.2f}%")
    print(f"  Test Sharpe Ratio       : {test_sharpe:.4f}")
    print("-" * 70)

    portfolio_payload = {
        "status": "success",
        "dataset_name": "Indian_Nifty_Bluechips",
        "date_range": f"{start_date} to {end_date}",
        "all_tickers": tickers,
        "selected_stocks": selected_stocks,
        "binary_configuration": binary_vector,
        "budget": K,
        "risk_factor_q": q,
        "train_annualized_return_pct": round(train_return * 100, 2),
        "train_variance_risk": round(train_risk, 6),
        "train_sharpe_ratio": round(train_sharpe, 4),
        "test_annualized_return_pct": round(test_return * 100, 2),
        "test_sharpe_ratio": round(test_sharpe, 4),
        "individual_annualized_returns": {
            tickers[i]: f"{mu_train[i] * 100:.2f}%" for i in range(len(tickers))
        },
        "timestamp_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    }
    return portfolio_payload


def step_2_execute_bb84_on_real_ibm_qpu() -> BB84Session:
    print("\n" + "=" * 70)
    print("  STEP 2: BB84 QKD PROTOCOL ON REAL IBM QUANTUM HARDWARE")
    print("=" * 70)

    token = os.getenv("IBM_QUANTUM_TOKEN", "").strip()
    instance = os.getenv("IBM_QUANTUM_INSTANCE", "").strip()
    if not token:
        raise ValueError("IBM_QUANTUM_TOKEN not found in environment/.env.")

    kwargs = dict(channel="ibm_quantum_platform", token=token)
    if instance:
        kwargs["instance"] = instance

    print("[*] Connecting to IBM Quantum Runtime Service...")
    service = QiskitRuntimeService(**kwargs)

    # Check online QPU backends
    backends = service.backends(simulator=False, operational=True)
    print("[*] Available operational IBM QPUs:")
    for b in backends:
        status = b.status()
        print(f"    - {b.name:<18} | Qubits: {b.num_qubits:<3} | Pending Jobs: {status.pending_jobs}")

    # Auto-select least busy QPU
    least_busy_qpu = service.least_busy(simulator=False, operational=True)
    print(f"\n[*] Automatically selected least busy QPU: {least_busy_qpu.name} (Queue: {least_busy_qpu.status().pending_jobs})")

    # Initialize 16-qubit BB84 session
    num_qubits = 16
    session = BB84Session(num_qubits=num_qubits, enable_eve=False, backend_name=least_busy_qpu.name)

    print(f"[*] Initialized {num_qubits}-qubit BB84 Session:")
    print(f"    Alice's random bits : {session.alice_bits}")
    basis_symbols = {0: "Z (+)", 1: "X (×)"}
    print(f"    Alice's random bases: {[basis_symbols[b] for b in session.alice_bases]}")
    print(f"    Bob's random bases  : {[basis_symbols[b] for b in session.bob_bases]}")

    print(f"\n[*] Compiling circuit to native ISA gates & submitting to {least_busy_qpu.name} via SamplerV2...")
    print("    (Please hold while physical superconducting qubits execute in the dilution refrigerator...)")

    t0 = time.time()
    summary = session.execute_on_ibm_qpu(service=service)
    elapsed = time.time() - t0

    print(f"\n[*] Hardware execution finished in {elapsed:.1f}s!")
    print("\n" + "-" * 70)
    print("  IBM QUANTUM HARDWARE EXECUTION SUMMARY")
    print("-" * 70)
    print(f"  Physical QPU Target  : {summary['qpu_name']}")
    print(f"  IBM Quantum Job ID   : {summary['job_id']}")
    print(f"  Total Photons Sent   : {summary['num_qubits']}")
    print(f"  Matching Bases (Sift): {summary['sifted_bits_count']} / {summary['num_qubits']} ({summary['sifted_efficiency']})")
    print(f"  Physical QBER        : {summary['qber_percentage']}")
    print(f"  Security Status      : {summary['security_status']}")
    print(f"  Derived 256-bit Key  : {summary['derived_key_hex']}")
    print("-" * 70)

    if summary["security_status"] != "SECURE_KEY_ESTABLISHED":
        raise RuntimeError(f"Key establishment aborted: {summary['security_status']}")

    return session


def step_3_encrypt_and_verify_payload(portfolio_payload: Dict[str, Any], session: BB84Session):
    print("\n" + "=" * 70)
    print("  STEP 3: QUANTUM PAYLOAD ENCRYPTION (AES-256-GCM)")
    print("=" * 70)

    quantum_key_bytes = session.derived_key_bytes
    print(f"[*] Active Quantum Symmetric Key (SHA-256 HKDF): {session.derived_key_hex}")

    print("\n[*] Original Main Dataset Portfolio Payload to Encrypt:")
    print(json.dumps(portfolio_payload, indent=2))

    # Encrypt payload using AES-256-GCM with quantum key
    encrypted_packet = encrypt_portfolio_payload(portfolio_payload, quantum_key_bytes)

    print("\n[*] AES-256-GCM Encryption Complete:")
    print(f"    Algorithm       : {encrypted_packet['algorithm']}")
    print(f"    Key Origin      : {encrypted_packet['key_origin']}")
    print(f"    Random Nonce    : {encrypted_packet['nonce_hex']}")
    print(f"    Ciphertext (hex): {encrypted_packet['ciphertext_hex']}")

    # Client-side decryption and verification
    print("\n" + "=" * 70)
    print("  STEP 4: CLIENT-SIDE DECRYPTION & DATA INTEGRITY VERIFICATION")
    print("=" * 70)

    decrypted_payload = decrypt_portfolio_payload(encrypted_packet, quantum_key_bytes)
    print("[*] Decrypted Payload from Ciphertext:")
    print(json.dumps(decrypted_payload, indent=2))

    # Integrity assertions
    assert decrypted_payload["dataset_name"] == portfolio_payload["dataset_name"]
    assert decrypted_payload["selected_stocks"] == portfolio_payload["selected_stocks"]
    assert decrypted_payload["binary_configuration"] == portfolio_payload["binary_configuration"]
    assert decrypted_payload["train_sharpe_ratio"] == portfolio_payload["train_sharpe_ratio"]

    print("\n" + "=" * 70)
    print("  ✅ SUCCESS: 100% DATA FIDELITY VERIFIED!")
    print(f"  Portfolio data for {decrypted_payload['selected_stocks']} successfully protected")
    print(f"  by BB84 Quantum Key generated on physical IBM QPU ({session.qpu_name}).")
    print("=" * 70)

    # Save output report
    output_record = {
        "execution_timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "ibm_hardware": {
            "qpu_name": session.qpu_name,
            "job_id": session.job_id,
            "num_qubits": session.num_qubits,
            "sifted_bits_count": len(session.sifted_indices),
            "physical_qber": session.qber,
            "security_status": session.security_status,
            "derived_key_hex": session.derived_key_hex,
        },
        "original_portfolio": portfolio_payload,
        "encrypted_transmission": encrypted_packet,
        "decrypted_verification": decrypted_payload,
    }

    report_path = "bb84_hardware_main_dataset_result.json"
    with open(report_path, "w") as f:
        json.dump(output_record, f, indent=2)
    print(f"\n[*] Execution audit saved to: {report_path}")


def main():
    print("=" * 70)
    print("  QPortal: Live BB84 on Real IBM Quantum Hardware + Main Dataset")
    print("=" * 70)

    try:
        portfolio_payload = step_1_optimize_main_dataset()
        session = step_2_execute_bb84_on_real_ibm_qpu()
        step_3_encrypt_and_verify_payload(portfolio_payload, session)
    except Exception as exc:
        print(f"\n[!] Execution failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
