"""
Milestone 2B — Real QAOA Parameter Optimization on IBM Quantum Hardware

Runs a classical optimization loop (COBYLA) for the QAOA parameters (gamma, beta),
where each objective evaluation executes on REAL IBM Quantum hardware using SamplerV2.

Problem:
    Minimize C(x) = -x0 - 2*x1 + 3*(x0 + x1 - 1)^2
    Constraint: x0 + x1 = 1
    Optimal solution: x0 = 0, x1 = 1  (bitstring '10', objective = -2.0)
"""

import os
from collections import Counter
from typing import List, Tuple, Dict
from dotenv import load_dotenv

from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from scipy.optimize import minimize


# ------------------------------------------------------------
# Configuration & Environment
# ------------------------------------------------------------

load_dotenv()

IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN")
IBM_INSTANCE = os.getenv("IBM_QUANTUM_INSTANCE")
IBM_BACKEND = os.getenv("IBM_BACKEND_NAME", "ibm_marrakesh")

EVAL_SHOTS = 256    # Shots per COBYLA iteration to conserve QPU time
FINAL_SHOTS = 1024   # Shots for final evaluation of optimized parameters
MAX_ITER = 6         # Maximum COBYLA iterations for hardware experiment

PENALTY = 3.0


# ------------------------------------------------------------
# Cost & Feasibility Functions
# ------------------------------------------------------------

def objective_value(bitstring: str) -> float:
    """
    Evaluate original objective: -x0 - 2*x1
    Bitstring format is c1c0: LSB (bitstring[-1]) is x0, bitstring[-2] is x1.
    """
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])
    return -x0 - 2.0 * x1


def constraint_penalty(bitstring: str) -> float:
    """Return penalty for violating x0 + x1 = 1."""
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])
    return PENALTY * ((x0 + x1 - 1) ** 2)


def total_cost(bitstring: str) -> float:
    """Return complete QUBO cost = objective + penalty."""
    return objective_value(bitstring) + constraint_penalty(bitstring)


def is_feasible(bitstring: str) -> bool:
    """Check constraint x0 + x1 = 1."""
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])
    return (x0 + x1) == 1


def calculate_expected_cost(counts: Dict[str, int]) -> float:
    """Calculate expected QUBO cost over measurement distribution."""
    total_shots = sum(counts.values())
    if total_shots == 0:
        return 0.0
    expected_cost = 0.0
    for bitstring, count in counts.items():
        clean = bitstring.replace(" ", "")
        prob = count / total_shots
        cost = total_cost(clean)
        expected_cost += prob * cost
    return expected_cost


# ------------------------------------------------------------
# QAOA Circuit Construction
# ------------------------------------------------------------

def build_qaoa_circuit() -> QuantumCircuit:
    """
    Construct a p=1 QAOA circuit for 2-variable QUBO.
    Cost Hamiltonian C(x) = -4*x0 - 5*x1 + 6*x0*x1
    """
    gamma = Parameter("γ")
    beta = Parameter("β")

    qc = QuantumCircuit(2)

    # Initial state |++>
    qc.h(0)
    qc.h(1)

    # Cost Hamiltonian
    qc.rz(-4.0 * gamma, 0)
    qc.rz(-5.0 * gamma, 1)

    qc.cx(0, 1)
    qc.rz(6.0 * gamma, 1)
    qc.cx(0, 1)

    # Mixer Hamiltonian
    qc.rx(2.0 * beta, 0)
    qc.rx(2.0 * beta, 1)

    qc.measure_all()
    return qc


# ------------------------------------------------------------
# IBM Quantum Authentication
# ------------------------------------------------------------

def get_service() -> QiskitRuntimeService:
    """Authenticate with IBM Quantum Service."""
    if not IBM_TOKEN:
        raise RuntimeError("IBM_QUANTUM_TOKEN is missing from .env")
    if not IBM_INSTANCE:
        raise RuntimeError("IBM_QUANTUM_INSTANCE is missing from .env")

    return QiskitRuntimeService(
        channel="ibm_quantum_platform",
        token=IBM_TOKEN,
        instance=IBM_INSTANCE,
    )


# ------------------------------------------------------------
# Milestone 2B Main Hardware Optimization Loop
# ------------------------------------------------------------

def run_hardware_optimization():
    print("=" * 66)
    print("  MILESTONE 2B — QAOA PARAMETER OPTIMIZATION ON IBM HARDWARE")
    print("=" * 66)

    # 1. Connect & Select Backend
    print("\nConnecting to IBM Quantum...")
    service = get_service()
    backend = service.backend(IBM_BACKEND)

    print(f"  Backend     : {backend.name}")
    print(f"  Qubits      : {backend.num_qubits}")
    print(f"  Operational : {backend.status().operational}")

    if not backend.status().operational:
        raise RuntimeError(f"Backend {backend.name} is not operational.")

    # 2. Build & Transpile Parameterized Circuit ONCE
    print("\nBuilding QAOA circuit...")
    circuit = build_qaoa_circuit()

    print(f"Transpiling parameterized circuit for '{backend.name}' (ISA)...")
    isa_circuit = transpile(circuit, backend=backend, optimization_level=1)
    print(f"  Transpiled Qubits : {isa_circuit.num_qubits}")
    print(f"  Transpiled Depth  : {isa_circuit.depth()}")
    print(f"  Transpiled Params : {isa_circuit.parameters}")

    # Initialize SamplerV2
    sampler = SamplerV2(mode=backend)

    # Track optimization progress
    iteration_counter = 0
    job_records = []  # List of tuples: (iter_num, gamma, beta, expected_cost, job_id)

    # 3. Objective Evaluation Function for Classical Optimizer
    def evaluate_qaoa(params: Tuple[float, float]) -> float:
        nonlocal iteration_counter
        iteration_counter += 1

        gamma_val, beta_val = params[0], params[1]

        # Bind parameters to exact parameter instances in transpiled circuit
        param_map = {}
        for p in isa_circuit.parameters:
            if p.name == "γ":
                param_map[p] = gamma_val
            elif p.name == "β":
                param_map[p] = beta_val

        bound_circuit = isa_circuit.assign_parameters(param_map, inplace=False)

        # Submit job to real IBM Quantum hardware
        job = sampler.run([bound_circuit], shots=EVAL_SHOTS)
        job_id = job.job_id()

        result = job.result()
        pub_result = result[0]
        counts = pub_result.data.meas.get_counts()

        expected_cost = calculate_expected_cost(counts)

        print(f"\nIteration {iteration_counter}:")
        print(f"  gamma         = {gamma_val:.4f}")
        print(f"  beta          = {beta_val:.4f}")
        print(f"  expected_cost = {expected_cost:.4f}")
        print(f"  job_id        = {job_id}")

        job_records.append((iteration_counter, gamma_val, beta_val, expected_cost, job_id))

        return expected_cost

    # 4. Run Classical Optimization Loop (COBYLA)
    initial_gamma = 0.5
    initial_beta = 0.25
    initial_params = [initial_gamma, initial_beta]

    print("\nStarting COBYLA classical optimization loop on REAL IBM QPU...")
    print(f"  Initial gamma = {initial_gamma}")
    print(f"  Initial beta  = {initial_beta}")
    print(f"  Max iterations= {MAX_ITER}")
    print(f"  Shots/eval    = {EVAL_SHOTS}")

    opt_result = minimize(
        evaluate_qaoa,
        initial_params,
        method="COBYLA",
        options={"maxiter": MAX_ITER, "rhobeg": 0.1},
    )

    opt_gamma, opt_beta = opt_result.x[0], opt_result.x[1]

    print("\nCOBYLA Optimization Finished.")
    print(f"  Optimized gamma = {opt_gamma:.4f}")
    print(f"  Optimized beta  = {opt_beta:.4f}")

    # 5. Final Hardware Execution with Optimized Parameters (Higher Shot Count)
    print("\nExecuting FINAL evaluation with optimized parameters...")
    print(f"  Final shots = {FINAL_SHOTS}")

    final_param_map = {}
    for p in isa_circuit.parameters:
        if p.name == "γ":
            final_param_map[p] = opt_gamma
        elif p.name == "β":
            final_param_map[p] = opt_beta

    final_bound_circuit = isa_circuit.assign_parameters(final_param_map, inplace=False)

    final_job = sampler.run([final_bound_circuit], shots=FINAL_SHOTS)
    final_job_id = final_job.job_id()
    print(f"  Final Job ID: {final_job_id}")
    print("  Waiting for final job to complete...")

    final_result = final_job.result()
    final_pub_result = final_result[0]
    final_counts = final_pub_result.data.meas.get_counts()

    final_expected_cost = calculate_expected_cost(final_counts)

    # Analyze feasible states
    feasible_counts = Counter()
    total_shots = sum(final_counts.values())

    for bitstring, count in final_counts.items():
        clean = bitstring.replace(" ", "")
        if is_feasible(clean):
            feasible_counts[clean] += count

    most_frequent_feasible = (
        feasible_counts.most_common(1)[0][0] if feasible_counts else "None"
    )
    freq_count = feasible_counts.get(most_frequent_feasible, 0)
    prob_most_frequent = freq_count / total_shots if total_shots > 0 else 0.0

    # Decode bitstring
    x0 = int(most_frequent_feasible[-1]) if most_frequent_feasible != "None" else None
    x1 = int(most_frequent_feasible[-2]) if most_frequent_feasible != "None" else None
    satisfied = (x0 + x1 == 1) if (x0 is not None and x1 is not None) else False

    # 6. Final Report Summary
    print()
    print("=" * 66)
    print("MILESTONE 2B — OPTIMIZED QAOA ON REAL IBM HARDWARE")
    print("=" * 66)
    print(f"Execution mode          : REAL IBM QUANTUM HARDWARE")
    print(f"Backend                 : {backend.name}")
    print(f"Total IBM Jobs Submitted: {len(job_records) + 1}")
    print(f"Optimization Iterations : {len(job_records)}")
    print()
    print(f"Initial gamma           : {initial_gamma:.4f}")
    print(f"Initial beta            : {initial_beta:.4f}")
    print(f"Optimized gamma         : {opt_gamma:.4f}")
    print(f"Optimized beta          : {opt_beta:.4f}")
    print()
    print(f"Final IBM Job ID        : {final_job_id}")
    print(f"Final Measurement Counts: {dict(final_counts)}")
    print(f"Feasible State Counts   : {dict(feasible_counts)}")
    print(f"Most Frequent Feasible  : {most_frequent_feasible}")
    print(f"Probability of Best     : {prob_most_frequent:.4f}")
    print()
    print("Decoded Solution:")
    print(f"  x0 = {x0}")
    print(f"  x1 = {x1}")
    print("Constraint:")
    print("  x0 + x1 = 1")
    print(f"Constraint Satisfied    : {'YES' if satisfied else 'NO'}")
    print(f"Final Expected QUBO Cost: {final_expected_cost:.4f}")
    print("=" * 66)

    return {
        "job_records": job_records,
        "final_job_id": final_job_id,
        "opt_gamma": opt_gamma,
        "opt_beta": opt_beta,
        "final_counts": final_counts,
        "most_frequent_feasible": most_frequent_feasible,
        "satisfied": satisfied,
        "final_expected_cost": final_expected_cost,
    }


if __name__ == "__main__":
    run_hardware_optimization()
