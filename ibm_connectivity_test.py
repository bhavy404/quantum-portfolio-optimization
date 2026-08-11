"""
ibm_connectivity_test.py
========================
Milestone 1 — IBM Quantum Hardware Connectivity Verification

Performs a 2-qubit Bell-state test on real IBM Quantum hardware.

Compatibility: Python 3.9+, qiskit-ibm-runtime 0.40+

Security rules enforced:
  * Credentials are read ONLY from the local .env file (python-dotenv).
  * The actual token / instance values are NEVER printed, logged, or stored.
  * No silent fallback to AerSimulator when IBM credentials are present.
  * If IBM authentication or backend execution fails, the script reports
    the real error and exits — it does NOT pretend success via simulation.

Usage:
  .venv/bin/python ibm_connectivity_test.py
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv

from qiskit import QuantumCircuit
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask(value: str, show_chars: int = 4) -> str:
    """Return a safely masked representation of a credential string."""
    if not value:
        return "<empty>"
    if len(value) <= show_chars * 2:
        return "*" * len(value)
    return value[:show_chars] + "*" * (len(value) - show_chars * 2) + value[-show_chars:]


def load_credentials():
    """
    Load IBM Quantum credentials from .env file.

    Returns a tuple (token, instance, backend_name).
    Raises SystemExit if the token is missing.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        print(f"[config] Loading environment from: {env_path}")
        load_dotenv(env_path, override=True)
    else:
        print("[config] No .env file found. Reading system environment variables.")

    token = os.environ.get("IBM_QUANTUM_TOKEN", "").strip()
    instance = os.environ.get("IBM_QUANTUM_INSTANCE", "").strip()
    backend_name = os.environ.get("IBM_BACKEND_NAME", "").strip()

    # ---- Credential presence check (no actual values printed) ----
    print()
    print("=== Credential Detection ===")
    print(f"  IBM Quantum token detected:    {'YES' if token else 'NO  <-- Set IBM_QUANTUM_TOKEN in .env'}")
    print(f"  IBM Quantum instance detected: {'YES' if instance else 'NO  <-- Set IBM_QUANTUM_INSTANCE in .env (optional for open plan)'}")
    print(f"  IBM_BACKEND_NAME configured:   {backend_name if backend_name else 'NOT SET (will auto-select least-busy QPU)'}")

    if not token:
        print()
        print("[ERROR] IBM_QUANTUM_TOKEN is missing.")
        print("        Add your IBM Quantum API key to the .env file:")
        print("        IBM_QUANTUM_TOKEN=<your-api-key>")
        sys.exit(1)

    return token, instance or None, backend_name or None


def authenticate(token: str, instance: Optional[str]) -> QiskitRuntimeService:
    """Authenticate with IBM Quantum and return the runtime service."""
    print()
    print("=== IBM Quantum Authentication ===")
    print("  Connecting to IBM Quantum Platform… (token not shown)")
    try:
        kwargs = dict(channel="ibm_quantum_platform", token=token)
        if instance:
            kwargs["instance"] = instance
        service = QiskitRuntimeService(**kwargs)
        print("  Authentication: SUCCESS")
        return service
    except Exception as exc:
        print(f"  Authentication FAILED: {exc}")
        print()
        print("[FATAL] Cannot connect to IBM Quantum. Check your token and instance.")
        sys.exit(1)


def select_backend(service: QiskitRuntimeService, backend_name: Optional[str]):
    """Select the target IBM backend. Never falls back to a simulator silently."""
    print()
    print("=== Backend Selection ===")
    if backend_name:
        print(f"  Requesting backend '{backend_name}' …")
        try:
            backend = service.backend(backend_name)
            print(f"  Backend selected: {backend.name}")
            print(f"  Backend status:   {backend.status().status_msg}")
            return backend
        except Exception as exc:
            print(f"  [ERROR] Failed to fetch backend '{backend_name}': {exc}")
            print()
            print("  Tip: verify IBM_BACKEND_NAME in your .env matches a backend")
            print("       visible in your IBM Quantum account at https://quantum.ibm.com/")
            sys.exit(1)
    else:
        print("  IBM_BACKEND_NAME not set — searching for least busy physical QPU…")
        try:
            backend = service.least_busy(simulator=False, operational=True)
            print(f"  Auto-selected backend: {backend.name}")
            print(f"  Backend status:        {backend.status().status_msg}")
            return backend
        except Exception as exc:
            print(f"  [ERROR] Failed to find an operational physical backend: {exc}")
            print()
            print("  Tip: set IBM_BACKEND_NAME=<backend> in your .env, or check")
            print("       your IBM Quantum account for available systems.")
            sys.exit(1)


def build_bell_circuit() -> QuantumCircuit:
    """Construct the standard 2-qubit Bell state circuit: (|00⟩ + |11⟩) / √2."""
    qc = QuantumCircuit(2)
    qc.h(0)       # Hadamard on qubit 0 → superposition
    qc.cx(0, 1)   # CNOT(0→1) → entanglement
    qc.measure_all()
    return qc


def transpile_circuit(qc: QuantumCircuit, backend):
    """Transpile the logical circuit to an ISA circuit for the target backend."""
    print()
    print(f"=== Transpilation (backend: {backend.name}) ===")
    print(f"  Logical circuit — qubits: {qc.num_qubits}, depth: {qc.depth()}")
    try:
        pm = generate_preset_pass_manager(optimization_level=3, backend=backend)
        isa_qc = pm.run(qc)
        print(f"  Transpiled circuit — qubits: {isa_qc.num_qubits}, depth: {isa_qc.depth()}")
        print("  Transpilation: SUCCESS")
        return isa_qc
    except Exception as exc:
        print(f"  [ERROR] Transpilation failed: {exc}")
        sys.exit(1)


def run_on_hardware(isa_qc: QuantumCircuit, backend):
    """Submit the circuit to real IBM Quantum hardware and wait for results."""
    print()
    print(f"=== Job Submission → REAL IBM QUANTUM HARDWARE ({backend.name}) ===")
    print("  Initialising SamplerV2 …")
    try:
        sampler = Sampler(mode=backend)
        print("  Submitting job to IBM Quantum hardware…")
        job = sampler.run([(isa_qc,)])

        job_id = job.job_id()
        print(f"  Job submitted.  Job ID: {job_id}")
        print("  Waiting for job to complete on hardware (this may take several minutes)…")

        result = job.result()
        return job_id, result
    except Exception as exc:
        print(f"  [ERROR] Hardware job failed: {exc}")
        print()
        print("[FATAL] IBM Quantum hardware execution did not succeed.")
        print("        This test does NOT fall back to a simulator.")
        print("        Review the error above and check your account/backend.")
        sys.exit(1)


def analyse_results(result, backend_name: str, job_id: str, isa_qc: QuantumCircuit):
    """Extract counts and compute Bell-state statistics."""
    pub_result = result[0]
    counts = pub_result.data.meas.get_counts()

    total_shots = sum(counts.values())
    prob_00 = counts.get("00", 0) / total_shots
    prob_11 = counts.get("11", 0) / total_shots
    correlation = prob_00 + prob_11

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║          MILESTONE 1 — CONNECTIVITY VERIFICATION RESULTS    ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Execution mode    : REAL IBM QUANTUM HARDWARE               ║")
    print(f"║  Backend name      : {backend_name:<41}║")
    print(f"║  Job ID            : {job_id:<41}║")
    print(f"║  Test circuit      : 2-qubit Bell state (H + CX + measure)  ║")
    print(f"║  Circuit qubits    : {isa_qc.num_qubits:<41}║")
    print(f"║  Transpiled depth  : {isa_qc.depth():<41}║")
    print(f"║  Total shots       : {total_shots:<41}║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Measurement counts: {str(counts):<41}║")
    print(f"║  P(|00⟩)           : {prob_00:.4f}{'':<36}║")
    print(f"║  P(|11⟩)           : {prob_11:.4f}{'':<36}║")
    print(f"║  Correlation       : P(00)+P(11) = {correlation:.4f}{'':<22}║")
    print("╠══════════════════════════════════════════════════════════════╣")
    if correlation > 0.85:
        print("║  ✅  Bell State correlation VERIFIED (> 85%)                ║")
        print("║  ✅  IBM Quantum hardware connection: CONFIRMED              ║")
    elif correlation > 0.70:
        print("║  ⚠️   Moderate correlation (70–85%). Expected on noisy QPU.  ║")
        print("║  ✅  IBM Quantum hardware connection: CONFIRMED              ║")
    else:
        print("║  ⚠️   Low correlation. High noise — check backend calibration.║")
        print("║  ✅  IBM Quantum hardware connection: CONFIRMED (job ran)    ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    return {
        "backend": backend_name,
        "job_id": job_id,
        "counts": counts,
        "total_shots": total_shots,
        "prob_00": prob_00,
        "prob_11": prob_11,
        "correlation": correlation,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 66)
    print("  IBM Quantum Connectivity Verification — Milestone 1")
    print("=" * 66)

    # Step 1: Load credentials
    token, instance, backend_name = load_credentials()

    # Step 2: Authenticate
    service = authenticate(token, instance)

    # Step 3: Select backend (real hardware only)
    backend = select_backend(service, backend_name)

    # Step 4: Build Bell circuit
    print()
    print("=== Building 2-Qubit Bell State Circuit ===")
    qc = build_bell_circuit()
    print("  Circuit built: H(q0) → CX(q0,q1) → measure_all()")

    # Step 5: Transpile
    isa_qc = transpile_circuit(qc, backend)

    # Step 6: Execute on hardware
    job_id, result = run_on_hardware(isa_qc, backend)

    # Step 7: Analyse and print results
    analyse_results(result, backend.name, job_id, isa_qc)

    print()
    print("Milestone 1 complete. Next step: Milestone 2 — migrate QAOA to IBM hardware.")


if __name__ == "__main__":
    main()
