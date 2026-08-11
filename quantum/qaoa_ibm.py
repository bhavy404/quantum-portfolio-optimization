"""
Milestone 2A
Small QAOA-style optimization executed on real IBM Quantum hardware.

Purpose:
    Verify that we can construct a QAOA circuit, transpile it for an
    IBM QPU, execute it through SamplerV2, and recover a feasible
    optimization bitstring.

This module does NOT modify the existing portfolio optimizer.
"""

import os
from collections import Counter

from dotenv import load_dotenv
from qiskit import QuantumCircuit, transpile
from qiskit.circuit import Parameter
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

load_dotenv()

IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN")
IBM_INSTANCE = os.getenv("IBM_QUANTUM_INSTANCE")
IBM_BACKEND = os.getenv("IBM_BACKEND_NAME", "ibm_marrakesh")

SHOTS = 1024


# ------------------------------------------------------------
# QAOA test problem
# ------------------------------------------------------------
#
# We use a very small optimization problem:
#
#     maximize x0 + 2*x1
#
# subject to:
#
#     x0 + x1 = 1
#
# Therefore:
#
#     01  -> objective 2  (optimal)
#     10  -> objective 1
#
# This gives us an unambiguous expected answer.
#
# We encode the equality constraint as a penalty:
#
#     C(x) = -x0 - 2*x1 + A*(x0+x1-1)^2
#
# with A = 3.
#
# Minimizing C gives the optimal bitstring 01.
# ------------------------------------------------------------

PENALTY = 3.0


def objective_value(bitstring: str) -> float:
    """
    Evaluate the original objective:
        -x0 - 2*x1

    Qiskit bitstrings are returned in classical-register order,
    which is conventionally reversed relative to qubit indexing.
    We explicitly decode that here.
    """

    # counts strings are typically c1c0.
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])

    return -x0 - 2 * x1


def constraint_penalty(bitstring: str) -> float:
    """Return the equality-constraint penalty."""
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])

    violation = (x0 + x1 - 1) ** 2
    return PENALTY * violation


def total_cost(bitstring: str) -> float:
    """Return the complete QUBO cost."""
    return objective_value(bitstring) + constraint_penalty(bitstring)


def is_feasible(bitstring: str) -> bool:
    """Check x0 + x1 = 1."""
    x0 = int(bitstring[-1])
    x1 = int(bitstring[-2])
    return (x0 + x1) == 1


# ------------------------------------------------------------
# Build a small QAOA circuit
# ------------------------------------------------------------

def build_qaoa_circuit():
    """
    Construct a p=1 QAOA circuit for the 2-variable test problem.

    Cost Hamiltonian:
        C(x) = -x0 - 2*x1 + 3*(x0+x1-1)^2

    The mixer is the standard X mixer.
    """

    gamma = Parameter("γ")
    beta = Parameter("β")

    qc = QuantumCircuit(2)

    # Start in |++>
    qc.h(0)
    qc.h(1)

    # --------------------------------------------------------
    # Cost Hamiltonian
    #
    # Expand:
    #
    # 3*(x0+x1-1)^2
    # = 3*x0 + 3*x1 + 6*x0*x1 - 6*x0 - 6*x1 + 3
    #
    # Combined with -x0 -2*x1:
    #
    # = -4*x0 -5*x1 + 6*x0*x1 + constant
    #
    # For x = (1-Z)/2:
    # x0*x1 contributes ZZ, Z0 and Z1 terms.
    #
    # We omit the constant because it only shifts all energies.
    # --------------------------------------------------------

    # Z terms
    qc.rz(-4.0 * gamma, 0)
    qc.rz(-5.0 * gamma, 1)

    # ZZ interaction
    qc.cx(0, 1)
    qc.rz(6.0 * gamma, 1)
    qc.cx(0, 1)

    # --------------------------------------------------------
    # Mixer Hamiltonian
    # --------------------------------------------------------

    qc.rx(2.0 * beta, 0)
    qc.rx(2.0 * beta, 1)

    # Measurement
    qc.measure_all()

    return qc


# ------------------------------------------------------------
# IBM connection
# ------------------------------------------------------------

def get_service():
    """Connect to IBM Quantum Runtime."""

    if not IBM_TOKEN:
        raise RuntimeError(
            "IBM_QUANTUM_TOKEN is missing from .env"
        )

    if not IBM_INSTANCE:
        raise RuntimeError(
            "IBM_QUANTUM_INSTANCE is missing from .env"
        )

    print("Connecting to IBM Quantum...")
    print(f"Backend requested: {IBM_BACKEND}")

    service = QiskitRuntimeService(
        channel="ibm_quantum_platform",
        token=IBM_TOKEN,
        instance=IBM_INSTANCE,
    )

    return service


# ------------------------------------------------------------
# Run QAOA hardware test
# ------------------------------------------------------------

def run_hardware_test():
    service = get_service()

    backend = service.backend(IBM_BACKEND)

    print()
    print("IBM Quantum backend:")
    print(f"  Name: {backend.name}")
    print(f"  Qubits: {backend.num_qubits}")
    print(f"  Operational: {backend.status().operational}")

    if not backend.status().operational:
        raise RuntimeError(
            f"Backend {IBM_BACKEND} is not operational."
        )

    # Build circuit
    circuit = build_qaoa_circuit()

    print()
    print("Original QAOA circuit:")
    print(circuit)

    # --------------------------------------------------------
    # For real hardware, transpile to the backend ISA.
    # --------------------------------------------------------

    print("Transpiling for IBM hardware...")

    isa_circuit = transpile(
        circuit,
        backend=backend,
        optimization_level=1,
    )

    print()
    print("Transpiled circuit:")
    print(f"  Qubits: {isa_circuit.num_qubits}")
    print(f"  Depth:  {isa_circuit.depth()}")

    # --------------------------------------------------------
    # Bind one reasonable p=1 parameter point.
    #
    # This is deliberately a hardware-pipeline test.
    # We are NOT claiming that this is the optimized QAOA
    # parameter set yet.
    # --------------------------------------------------------

    print("Transpiled parameters:", isa_circuit.parameters)

    param_map = {}
    for p in isa_circuit.parameters:
        if p.name == "γ":
            param_map[p] = 0.5
        elif p.name == "β":
            param_map[p] = 0.25

    print("Parameter mapping:", param_map)

    bound_circuit = isa_circuit.assign_parameters(
        param_map,
        inplace=False,
    )

    print()
    print("Submitting to REAL IBM QUANTUM HARDWARE...")
    print(f"Shots: {SHOTS}")

    sampler = SamplerV2(mode=backend)

    job = sampler.run(
        [bound_circuit],
        shots=SHOTS,
    )

    print(f"Job ID: {job.job_id()}")
    print("Waiting for IBM Quantum job to complete...")

    result = job.result()

    # --------------------------------------------------------
    # SamplerV2 measurement extraction
    # --------------------------------------------------------

    pub_result = result[0]

    # measure_all() creates the classical register "meas".
    counts = pub_result.data.meas.get_counts()

    print()
    print("=" * 60)
    print("MILESTONE 2A — QAOA HARDWARE TEST")
    print("=" * 60)

    print(f"Execution mode : REAL IBM QUANTUM HARDWARE")
    print(f"Backend        : {backend.name}")
    print(f"Job ID         : {job.job_id()}")
    print(f"Shots          : {SHOTS}")

    print()
    print("Measurement counts:")
    print(counts)

    # --------------------------------------------------------
    # Analyze feasible states.
    # --------------------------------------------------------

    feasible_counts = Counter()

    for bitstring, count in counts.items():

        # Remove possible spaces from multi-register output.
        clean = bitstring.replace(" ", "")

        if is_feasible(clean):
            feasible_counts[clean] += count

    print()
    print("Feasible states (x0 + x1 = 1):")
    print(dict(feasible_counts))

    if not feasible_counts:
        print()
        print("WARNING: No feasible states were measured.")
        print("This can happen on noisy hardware.")
        return

    # Most frequently measured feasible state
    best_bitstring = feasible_counts.most_common(1)[0][0]

    print()
    print(f"Most frequent feasible state: {best_bitstring}")
    print(f"Total QUBO cost: {total_cost(best_bitstring):.4f}")

    # Expected optimum is x0=0, x1=1 -> bitstring 10/01
    # depending on classical-bit ordering.
    print()
    print("Expected optimal assignment:")
    print("  x0 = 0")
    print("  x1 = 1")
    print("  Original objective = -2")

    print()
    print("Milestone 2A completed: IBM QAOA circuit executed.")
    print("=" * 60)


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

if __name__ == "__main__":
    run_hardware_test()