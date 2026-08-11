"""
Milestone 3
Five-stock portfolio optimization using QAOA on real IBM Quantum hardware.

This file intentionally does NOT modify the original
quantum_portfolio_optimization.py.

Problem:
    Stocks: AAPL, MSFT, GOOGL, AMZN, META
    q = 0.5
    K = 2
    Date range = 2023-01-01 through 2024-01-01
    Train/Test split = 70/30

Execution:
    QUBO
      -> Ising Hamiltonian
      -> QAOAAnsatz
      -> IBM Runtime SamplerV2
      -> real IBM QPU
      -> measured bitstrings
      -> feasible portfolio
      -> financial evaluation
"""

import os
import itertools
from collections import Counter

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from scipy.optimize import minimize

from qiskit import transpile
from qiskit.circuit.library import QAOAAnsatz
from qiskit_optimization import QuadraticProgram
from qiskit_optimization.converters import LinearEqualityToPenalty
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2


# ============================================================
# CONFIGURATION
# ============================================================

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

START_DATE = "2023-01-01"
END_DATE = "2024-01-01"

Q = 0.5
K = 2

QAOA_REPS = 1

# Keep the first hardware experiment small.
OPTIMIZATION_SHOTS = 256
FINAL_SHOTS = 1024

MAXITER = 6

load_dotenv()

IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN")
IBM_INSTANCE = os.getenv("IBM_QUANTUM_INSTANCE")
IBM_BACKEND_NAME = os.getenv("IBM_BACKEND_NAME", "ibm_marrakesh")


# ============================================================
# 1. DOWNLOAD FINANCIAL DATA
# ============================================================

def download_data():
    print("\n=== S1: Download Dataset ===")
    print(f"Tickers: {TICKERS}")
    print(f"Date range: {START_DATE} -> {END_DATE}")

    data = pd.DataFrame()

    for ticker in TICKERS:
        df = yf.download(
            ticker,
            start=START_DATE,
            end=END_DATE,
            progress=False,
            auto_adjust=False,
        )

        if df.empty:
            raise RuntimeError(f"No Yahoo Finance data returned for {ticker}")

        if isinstance(df.columns, pd.MultiIndex):
            # Handle current yfinance MultiIndex format.
            if ("Close", ticker) in df.columns:
                data[ticker] = df[("Close", ticker)]
            elif ("Adj Close", ticker) in df.columns:
                data[ticker] = df[("Adj Close", ticker)]
            else:
                raise RuntimeError(
                    f"Could not find Close/Adj Close for {ticker}"
                )
        else:
            if "Close" in df.columns:
                data[ticker] = df["Close"]
            elif "Adj Close" in df.columns:
                data[ticker] = df["Adj Close"]
            else:
                raise RuntimeError(
                    f"Could not find Close/Adj Close for {ticker}"
                )

    data = data.dropna()

    print(f"Rows downloaded: {len(data)}")

    returns = data.pct_change().dropna()

    split_idx = int(len(returns) * 0.7)

    train_returns = returns.iloc[:split_idx]
    test_returns = returns.iloc[split_idx:]

    mu_train = train_returns.mean().values * 252
    sigma_train = train_returns.cov().values * 252

    mu_test = test_returns.mean().values * 252
    sigma_test = test_returns.cov().values * 252

    print(f"Train observations: {len(train_returns)}")
    print(f"Test observations:  {len(test_returns)}")

    print("\nAnnualized expected returns:")
    for i, ticker in enumerate(TICKERS):
        print(f"  {ticker}: {mu_train[i]:.6f}")

    print("\nAnnualized covariance matrix:")
    print(np.round(sigma_train, 6))

    return (
        data,
        returns,
        train_returns,
        test_returns,
        mu_train,
        sigma_train,
        mu_test,
        sigma_test,
    )


# ============================================================
# 2. BUILD ORIGINAL QUADRATIC PROGRAM
# ============================================================

def build_problem(mu_train, sigma_train):
    print("\n=== S2: Build Portfolio QUBO ===")

    qp = QuadraticProgram("five_stock_portfolio")

    for ticker in TICKERS:
        qp.binary_var(ticker)

    # Existing project objective:
    #
    # minimize:
    #     -q * return + (1-q) * risk
    #
    linear_dict = {
        TICKERS[i]: -Q * mu_train[i]
        for i in range(len(TICKERS))
    }

    quadratic_dict = {}

    for i in range(len(TICKERS)):
        for j in range(len(TICKERS)):
            quadratic_dict[
                (TICKERS[i], TICKERS[j])
            ] = (1 - Q) * sigma_train[i, j]

    qp.minimize(
        linear=linear_dict,
        quadratic=quadratic_dict,
    )

    # Exactly K stocks.
    qp.linear_constraint(
        linear={ticker: 1 for ticker in TICKERS},
        sense="==",
        rhs=K,
        name="budget",
    )

    print("\nOriginal constrained problem:")
    print(qp.prettyprint())

    # Convert equality constraint to penalty.
    converter = LinearEqualityToPenalty()
    qubo = converter.convert(qp)

    print("\nQUBO after penalty conversion:")
    print(qubo.prettyprint())

    return qp, qubo


# ============================================================
# 3. ENUMERATE CLASSICAL BASELINE
# ============================================================

def vector_from_indices(indices):
    x = np.zeros(len(TICKERS), dtype=int)

    for i in indices:
        x[i] = 1

    return x


def classical_baseline(qp, qubo):
    print("\n=== S3: Exact Classical Baseline ===")

    candidates = []

    for selected in itertools.combinations(range(len(TICKERS)), K):
        x = vector_from_indices(selected)

        original_cost = float(qp.objective.evaluate(x))
        qubo_cost = float(qubo.objective.evaluate(x))

        stocks = [
            TICKERS[i]
            for i in range(len(TICKERS))
            if x[i] == 1
        ]

        candidates.append(
            {
                "x": x,
                "stocks": stocks,
                "original_cost": original_cost,
                "qubo_cost": qubo_cost,
            }
        )

    candidates.sort(key=lambda item: item["qubo_cost"])

    print(f"Number of feasible portfolios: {len(candidates)}")

    print("\nAll feasible portfolios:")

    for rank, item in enumerate(candidates, start=1):
        bits = "".join(str(int(v)) for v in item["x"])

        print(
            f"{rank:2d}. {bits} "
            f"{item['stocks']} "
            f"QUBO={item['qubo_cost']:.8f}"
        )

    best = candidates[0]

    print("\nClassical optimum:")
    print(f"  Stocks: {best['stocks']}")
    print(f"  Binary: {best['x'].astype(int).tolist()}")
    print(f"  QUBO objective: {best['qubo_cost']:.8f}")

    return candidates


# ============================================================
# 4. BITSTRING DECODING
# ============================================================

def decode_bitstring(bitstring):
    """
    Qiskit measurement strings display classical bits with c0
    on the RIGHTMOST side.

    Therefore:
        q0/x0 -> rightmost character
        q4/x4 -> leftmost character

    Example:
        x = [1,0,0,0,1]
        measurement string = 10001
    """

    clean = bitstring.replace(" ", "")

    if len(clean) != len(TICKERS):
        raise ValueError(
            f"Expected {len(TICKERS)} bits, got {len(clean)}: {clean}"
        )

    x = np.array(
        [
            int(clean[-1 - i])
            for i in range(len(TICKERS))
        ],
        dtype=int,
    )

    return x


def verify_bit_mapping():
    """
    Internal consistency test.

    Known binary vectors are converted to the expected Qiskit
    classical measurement representation and decoded again.
    """

    tests = [
        np.array([1, 0, 0, 0, 0]),
        np.array([0, 1, 0, 0, 0]),
        np.array([0, 0, 1, 0, 0]),
        np.array([0, 0, 0, 1, 0]),
        np.array([0, 0, 0, 0, 1]),
        np.array([1, 0, 0, 0, 1]),
    ]

    for x in tests:
        measurement_string = "".join(
            str(int(v)) for v in x[::-1]
        )

        decoded = decode_bitstring(measurement_string)

        if not np.array_equal(x, decoded):
            raise RuntimeError(
                "Bit-order verification failed."
            )

    print("Bit-order verification: PASSED")


# ============================================================
# 5. IBM QUANTUM CONNECTION
# ============================================================

def connect_ibm():
    if not IBM_TOKEN:
        raise RuntimeError(
            "IBM_QUANTUM_TOKEN missing from .env"
        )

    if not IBM_INSTANCE:
        raise RuntimeError(
            "IBM_QUANTUM_INSTANCE missing from .env"
        )

    if not IBM_BACKEND_NAME:
        raise RuntimeError(
            "IBM_BACKEND_NAME missing from .env"
        )

    print("\n=== S4: IBM Quantum Connection ===")
    print(f"Backend requested: {IBM_BACKEND_NAME}")

    service = QiskitRuntimeService(
        channel="ibm_quantum_platform",
        token=IBM_TOKEN,
        instance=IBM_INSTANCE,
    )

    backend = service.backend(IBM_BACKEND_NAME)

    status = backend.status()

    print(f"Backend: {backend.name}")
    print(f"Qubits: {backend.num_qubits}")
    print(f"Operational: {status.operational}")

    if not status.operational:
        raise RuntimeError(
            f"Backend {IBM_BACKEND_NAME} is not operational."
        )

    return service, backend


# ============================================================
# 6. BUILD QAOA CIRCUIT
# ============================================================

def build_qaoa_circuit(qubo):
    print("\n=== S5: Build QAOA Circuit ===")

    # Qiskit Optimization converts the QUBO into an Ising
    # SparsePauliOp plus a constant offset.
    #
    # Variables map to qubits in the same order.
    cost_operator, offset = qubo.to_ising()

    print(f"Ising offset: {offset}")
    print(f"Qubits required: {cost_operator.num_qubits}")

    if cost_operator.num_qubits != len(TICKERS):
        raise RuntimeError(
            "Unexpected number of qubits in QAOA operator."
        )

    # p=1 QAOA.
    circuit = QAOAAnsatz(
        cost_operator=cost_operator,
        reps=QAOA_REPS,
        flatten=True,
    )

    circuit.measure_all()

    print(f"Logical qubits: {circuit.num_qubits}")
    print(f"QAOA parameters: {circuit.parameters}")

    return circuit, cost_operator, offset


# ============================================================
# 7. PARAMETER BINDING
# ============================================================

def bind_qaoa_parameters(circuit, gamma, beta):
    """
    Use the ACTUAL Parameter objects belonging to the circuit.

    This avoids the parameter-binding bug encountered in
    Milestone 2A.
    """

    gamma_parameter = None
    beta_parameter = None

    for parameter in circuit.parameters:

        name = parameter.name

        if name.startswith("γ") or name.startswith("gamma"):
            gamma_parameter = parameter

        elif name.startswith("β") or name.startswith("beta"):
            beta_parameter = parameter

    if gamma_parameter is None or beta_parameter is None:
        raise RuntimeError(
            f"Could not identify QAOA parameters. "
            f"Available: {list(circuit.parameters)}"
        )

    bound = circuit.assign_parameters(
        {
            gamma_parameter: float(gamma),
            beta_parameter: float(beta),
        },
        inplace=False,
    )

    return bound


# ============================================================
# 8. HARDWARE EXECUTION
# ============================================================

def run_sampler_job(sampler, circuit, shots):
    job = sampler.run(
        [circuit],
        shots=shots,
    )

    job_id = job.job_id()

    print(f"  IBM Job ID: {job_id}")

    result = job.result()

    counts = result[0].data.meas.get_counts()

    return job_id, counts


# ============================================================
# 9. OBJECTIVE FROM MEASUREMENTS
# ============================================================

def expected_qubo_cost(counts, qubo):
    total_shots = sum(counts.values())

    expected = 0.0

    for bitstring, count in counts.items():

        x = decode_bitstring(bitstring)

        cost = float(
            qubo.objective.evaluate(x)
        )

        probability = count / total_shots

        expected += probability * cost

    return expected


# ============================================================
# 10. FEASIBLE STATE ANALYSIS
# ============================================================

def feasible_state_counts(counts):
    feasible = Counter()

    for bitstring, count in counts.items():

        x = decode_bitstring(bitstring)

        if int(np.sum(x)) == K:
            feasible[bitstring] += count

    return feasible


def state_to_stocks(bitstring):
    x = decode_bitstring(bitstring)

    return [
        TICKERS[i]
        for i in range(len(TICKERS))
        if x[i] == 1
    ]


# ============================================================
# 11. PORTFOLIO FINANCIAL METRICS
# ============================================================

def portfolio_metrics(x, mu_train, sigma_train, mu_test, sigma_test):

    x = np.asarray(x, dtype=float)

    if np.sum(x) == 0:
        raise ValueError("Portfolio contains no selected stocks.")

    weights = x / np.sum(x)

    train_return = float(
        np.dot(weights, mu_train)
    )

    train_risk = float(
        np.dot(
            weights.T,
            np.dot(sigma_train, weights),
        )
    )

    test_return = float(
        np.dot(weights, mu_test)
    )

    test_risk = float(
        np.dot(
            weights.T,
            np.dot(sigma_test, weights),
        )
    )

    train_sharpe = (
        train_return / np.sqrt(train_risk)
        if train_risk > 0
        else 0.0
    )

    test_sharpe = (
        test_return / np.sqrt(test_risk)
        if test_risk > 0
        else 0.0
    )

    return {
        "train_return": train_return,
        "train_risk": train_risk,
        "train_sharpe": train_sharpe,
        "test_return": test_return,
        "test_risk": test_risk,
        "test_sharpe": test_sharpe,
    }


# ============================================================
# 12. MAIN
# ============================================================

def main():

    print("=" * 70)
    print("MILESTONE 3 — FIVE-STOCK QAOA ON REAL IBM HARDWARE")
    print("=" * 70)

    # --------------------------------------------------------
    # Validation before spending QPU time.
    # --------------------------------------------------------

    verify_bit_mapping()

    # --------------------------------------------------------
    # Financial data.
    # --------------------------------------------------------

    (
        data,
        returns,
        train_returns,
        test_returns,
        mu_train,
        sigma_train,
        mu_test,
        sigma_test,
    ) = download_data()

    # --------------------------------------------------------
    # Build QUBO.
    # --------------------------------------------------------

    qp, qubo = build_problem(
        mu_train,
        sigma_train,
    )

    if qubo.get_num_vars() != 5:
        raise RuntimeError(
            f"Expected 5 QUBO variables, got {qubo.get_num_vars()}"
        )

    # --------------------------------------------------------
    # Classical exact baseline.
    # --------------------------------------------------------

    classical_candidates = classical_baseline(
        qp,
        qubo,
    )

    classical_best = classical_candidates[0]

    # --------------------------------------------------------
    # IBM connection.
    # --------------------------------------------------------

    service, backend = connect_ibm()

    # --------------------------------------------------------
    # Build QAOA.
    # --------------------------------------------------------

    circuit, cost_operator, offset = build_qaoa_circuit(
        qubo
    )

    if circuit.num_qubits != 5:
        raise RuntimeError(
            "QAOA circuit does not contain exactly 5 logical qubits."
        )

    # --------------------------------------------------------
    # Transpile ONCE.
    # --------------------------------------------------------

    print("\n=== S6: Transpile for IBM Hardware ===")

    isa_circuit = transpile(
        circuit,
        backend=backend,
        optimization_level=1,
    )

    print(f"Transpiled qubits: {isa_circuit.num_qubits}")
    print(f"Transpiled depth: {isa_circuit.depth()}")
    print(f"Transpiled parameters: {list(isa_circuit.parameters)}")

    # --------------------------------------------------------
    # SamplerV2.
    # --------------------------------------------------------

    sampler = SamplerV2(
        mode=backend
    )

    # --------------------------------------------------------
    # Objective function for classical optimizer.
    # --------------------------------------------------------

    job_history = []

    def hardware_objective(params):

        gamma = float(params[0])
        beta = float(params[1])

        print(
            f"\nEvaluation {len(job_history) + 1}: "
            f"gamma={gamma:.6f}, beta={beta:.6f}"
        )

        bound = bind_qaoa_parameters(
            isa_circuit,
            gamma,
            beta,
        )

        job_id, counts = run_sampler_job(
            sampler,
            bound,
            OPTIMIZATION_SHOTS,
        )

        cost = expected_qubo_cost(
            counts,
            qubo,
        )

        job_history.append(
            {
                "gamma": gamma,
                "beta": beta,
                "cost": cost,
                "job_id": job_id,
                "counts": counts,
            }
        )

        print(f"  Expected QUBO cost: {cost:.8f}")

        return cost

    # --------------------------------------------------------
    # Classical parameter optimization.
    # --------------------------------------------------------

    print("\n=== S7: Hardware QAOA Parameter Optimization ===")

    initial_point = np.array(
        [0.5, 0.25],
        dtype=float,
    )

    optimization_result = minimize(
        hardware_objective,
        initial_point,
        method="COBYLA",
        options={
            "maxiter": MAXITER,
            "rhobeg": 0.4,
            "tol": 0.01,
        },
    )

    optimized_gamma = float(
        optimization_result.x[0]
    )

    optimized_beta = float(
        optimization_result.x[1]
    )

    print("\nClassical optimizer finished.")
    print(f"Initial gamma: {initial_point[0]:.6f}")
    print(f"Initial beta:  {initial_point[1]:.6f}")
    print(f"Optimized gamma: {optimized_gamma:.6f}")
    print(f"Optimized beta:  {optimized_beta:.6f}")
    print(f"Iterations/evaluations: {len(job_history)}")

    # --------------------------------------------------------
    # FINAL HARDWARE RUN
    # --------------------------------------------------------

    print("\n=== S8: FINAL IBM HARDWARE RUN ===")

    final_circuit = bind_qaoa_parameters(
        isa_circuit,
        optimized_gamma,
        optimized_beta,
    )

    final_job_id, final_counts = run_sampler_job(
        sampler,
        final_circuit,
        FINAL_SHOTS,
    )

    final_expected_cost = expected_qubo_cost(
        final_counts,
        qubo,
    )

    # --------------------------------------------------------
    # Analyze final distribution.
    # --------------------------------------------------------

    feasible = feasible_state_counts(
        final_counts
    )

    total_final_shots = sum(
        final_counts.values()
    )

    feasible_shots = sum(
        feasible.values()
    )

    feasible_probability = (
        feasible_shots / total_final_shots
        if total_final_shots > 0
        else 0.0
    )

    print("\nFinal measurement counts:")
    print(final_counts)

    print("\nFeasible measurement counts:")
    print(dict(feasible))

    print(
        f"\nFeasible probability mass: "
        f"{feasible_probability:.4f}"
    )

    if not feasible:
        raise RuntimeError(
            "No feasible portfolio was measured by the QPU."
        )

    # Most frequently measured feasible state.
    most_frequent_feasible = (
        feasible.most_common(1)[0][0]
    )

    # Lowest-cost feasible state that was actually observed.
    lowest_cost_state = min(
        feasible.keys(),
        key=lambda bitstring: qubo.objective.evaluate(
            decode_bitstring(bitstring)
        ),
    )

    most_frequent_x = decode_bitstring(
        most_frequent_feasible
    )

    lowest_cost_x = decode_bitstring(
        lowest_cost_state
    )

    # --------------------------------------------------------
    # Financial metrics for the MOST FREQUENT feasible state.
    # --------------------------------------------------------

    metrics = portfolio_metrics(
        most_frequent_x,
        mu_train,
        sigma_train,
        mu_test,
        sigma_test,
    )

    # --------------------------------------------------------
    # Compare with exact classical optimum.
    # --------------------------------------------------------

    hardware_qubo_cost = float(
        qubo.objective.evaluate(
            most_frequent_x
        )
    )

    classical_qubo_cost = float(
        classical_best["qubo_cost"]
    )

    found_classical_optimum = bool(
        np.isclose(
            hardware_qubo_cost,
            classical_qubo_cost,
            rtol=1e-7,
            atol=1e-7,
        )
    )

    # --------------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("MILESTONE 3 — FIVE-STOCK QAOA ON REAL IBM HARDWARE")
    print("=" * 70)

    print("Execution mode          : REAL IBM QUANTUM HARDWARE")
    print(f"Backend                 : {backend.name}")
    print(f"QAOA reps               : {QAOA_REPS}")
    print(f"Risk/return q           : {Q}")
    print(f"Budget K                : {K}")

    print()
    print(f"IBM jobs submitted     : {len(job_history) + 1}")
    print(f"Optimization evaluations: {len(job_history)}")

    print()
    print(f"Initial gamma           : {initial_point[0]:.6f}")
    print(f"Initial beta            : {initial_point[1]:.6f}")
    print(f"Optimized gamma         : {optimized_gamma:.6f}")
    print(f"Optimized beta          : {optimized_beta:.6f}")

    print()
    print(f"Final IBM Job ID        : {final_job_id}")
    print(f"Final shots             : {FINAL_SHOTS}")

    print("\n------------------------------------------------------------")
    print("MEASUREMENT RESULTS")
    print("------------------------------------------------------------")

    print("Final measurement counts:")
    print(final_counts)

    print("\nFeasible states:")
    print(dict(feasible))

    print(
        f"\nFeasible probability mass : "
        f"{feasible_probability:.4f}"
    )

    best_probability = (
        feasible[most_frequent_feasible]
        / total_final_shots
    )

    print(
        f"Most frequent feasible   : "
        f"{most_frequent_feasible}"
    )

    print(
        f"Probability of best      : "
        f"{best_probability:.4f}"
    )

    print(
        f"Lowest-cost feasible seen: "
        f"{lowest_cost_state}"
    )

    print("\n------------------------------------------------------------")
    print("SELECTED PORTFOLIO")
    print("------------------------------------------------------------")

    selected_stocks = [
        TICKERS[i]
        for i in range(len(TICKERS))
        if most_frequent_x[i] == 1
    ]

    print(f"Selected stocks          : {selected_stocks}")
    print(
        f"Binary vector            : "
        f"{most_frequent_x.astype(int).tolist()}"
    )

    print(
        f"Constraint sum(x) = {int(np.sum(most_frequent_x))}"
    )

    print(
        f"Constraint satisfied     : "
        f"{int(np.sum(most_frequent_x)) == K}"
    )

    print(
        f"Hardware QUBO objective  : "
        f"{hardware_qubo_cost:.8f}"
    )

    print(
        f"Final expected QUBO cost : "
        f"{final_expected_cost:.8f}"
    )

    print("\n------------------------------------------------------------")
    print("CLASSICAL BASELINE")
    print("------------------------------------------------------------")

    print(
        f"Classical optimal stocks  : "
        f"{classical_best['stocks']}"
    )

    print(
        f"Classical optimal binary  : "
        f"{classical_best['x'].astype(int).tolist()}"
    )

    print(
        f"Classical optimal QUBO    : "
        f"{classical_qubo_cost:.8f}"
    )

    print(
        f"IBM-QAOA portfolio        : "
        f"{selected_stocks}"
    )

    print(
        f"IBM-QAOA QUBO objective   : "
        f"{hardware_qubo_cost:.8f}"
    )

    print(
        f"Classical optimum found   : "
        f"{found_classical_optimum}"
    )

    print("\n------------------------------------------------------------")
    print("FINANCIAL METRICS")
    print("------------------------------------------------------------")

    print("\nTRAIN")
    print(
        f"Expected return           : "
        f"{metrics['train_return']:.6f}"
    )
    print(
        f"Risk / variance           : "
        f"{metrics['train_risk']:.6f}"
    )
    print(
        f"Sharpe ratio              : "
        f"{metrics['train_sharpe']:.6f}"
    )

    print("\nTEST")
    print(
        f"Expected return           : "
        f"{metrics['test_return']:.6f}"
    )
    print(
        f"Risk / variance           : "
        f"{metrics['test_risk']:.6f}"
    )
    print(
        f"Sharpe ratio              : "
        f"{metrics['test_sharpe']:.6f}"
    )

    print("\n------------------------------------------------------------")
    print("OPTIMIZATION JOB HISTORY")
    print("------------------------------------------------------------")

    for i, record in enumerate(job_history, start=1):
        print(
            f"{i}. "
            f"gamma={record['gamma']:.6f}, "
            f"beta={record['beta']:.6f}, "
            f"cost={record['cost']:.8f}, "
            f"job={record['job_id']}"
        )

    print("\n")
    print("=" * 70)
    print("MILESTONE 3 COMPLETE")
    print("=" * 70)

    print(
        "\nScientific note:"
        "\nThis experiment demonstrates QAOA execution of the "
        "portfolio QUBO on real IBM Quantum hardware."
        "\nIt does NOT establish quantum advantage."
        "\nThe exact classical enumeration of the 10 feasible "
        "portfolios is used as the benchmark."
    )


if __name__ == "__main__":
    main()