"""BB84 key exchange, batched: many sessions in ONE quantum job.

Each session sends QUBITS qubits. For every qubit Alice picks a random bit and basis and prepares the qubit; Bob
picks a random basis and measures. Alice and Bob keep the qubits where their bases agree ("sifting"), publicly
compare a random 30% sample to estimate the error rate, and keep the rest as secret key bits. All SESSIONS
circuits go to the quantum processor together (one queue wait, one job id) and the results are pooled.

Honest limit: Alice and Bob are both this server, so the circuits are a protocol implementation, not a quantum
link to the user's device. On real hardware, noise shows up as errors in the sample, which is why the pooled
error rate is bounded and a noisy run is rejected.
"""
import random
import secrets

SESSIONS = 10
QUBITS = 100
SAMPLE_FRACTION = 0.3


def _bits(n):
    return [secrets.randbelow(2) for _ in range(n)]


def build(sessions=SESSIONS, n=QUBITS):
    """Random bits and bases for Alice and Bob, and one preparation+measurement circuit per session."""
    from qiskit import QuantumCircuit

    alice_bits = [_bits(n) for _ in range(sessions)]
    alice_bases = [_bits(n) for _ in range(sessions)]
    bob_bases = [_bits(n) for _ in range(sessions)]
    circuits = []
    for s in range(sessions):
        qc = QuantumCircuit(n)
        for i in range(n):
            if alice_bits[s][i]:
                qc.x(i)
            if alice_bases[s][i]:
                qc.h(i)
        qc.barrier()
        for i in range(n):
            if bob_bases[s][i]:
                qc.h(i)
        qc.measure_all()
        circuits.append(qc)
    return alice_bits, alice_bases, bob_bases, circuits


def sift(alice_bits, alice_bases, bob_bases, bob_strings, n=QUBITS):
    """Pool all sessions. Returns the secret bits and the error-rate statistics."""
    rng = random.SystemRandom()
    secret, sifted_total, sample_total, errors = [], 0, 0, 0
    for s, bitstring in enumerate(bob_strings):
        bitstring = bitstring.replace(" ", "")
        bob_bits = [int(bitstring[len(bitstring) - 1 - i]) for i in range(n)]  # rightmost character = qubit 0
        keep = [i for i in range(n) if alice_bases[s][i] == bob_bases[s][i]]
        rng.shuffle(keep)
        cut = max(1, int(round(len(keep) * SAMPLE_FRACTION)))
        sample, rest = keep[:cut], keep[cut:]
        sifted_total += len(keep)
        sample_total += len(sample)
        errors += sum(1 for i in sample if alice_bits[s][i] != bob_bits[i])
        secret.extend(alice_bits[s][i] for i in rest)
    return secret, {"sifted_bits": sifted_total, "sample_bits": sample_total, "errors": errors}


def run(execute, sessions=SESSIONS, n=QUBITS):
    """execute(circuits) -> (list of one measured bitstring per circuit, job_id, backend_name)."""
    alice_bits, alice_bases, bob_bases, circuits = build(sessions, n)
    strings, job_id, backend = execute(circuits)
    secret, stats = sift(alice_bits, alice_bases, bob_bases, strings, n)
    return {"secret": secret, "sessions": sessions, "raw_qubits": sessions * n, "job_id": job_id, "backend": backend, **stats}


# ----- executors -----------------------------------------------------------------------------------
def execute_simulator(circuits):
    from qiskit_aer import AerSimulator

    sim = AerSimulator()
    res = sim.run(circuits, shots=1, memory=True).result()
    return [res.get_memory(i)[0] for i in range(len(circuits))], None, "Qiskit Aer simulator"


def execute_hardware(circuits):
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    from . import ibm, qlock

    service = ibm.connect()
    backend, backend_note = ibm.pick_backend(service, QUBITS)
    isa = qlock.run(lambda: generate_preset_pass_manager(optimization_level=1, backend=backend).run(circuits))
    job = SamplerV2(mode=backend).run([(c,) for c in isa], shots=1)  # one job; waiting happens off the Qiskit thread
    result = job.result()
    strings = [next(iter(r.data.meas.get_counts())) for r in result]
    return strings, job.job_id(), backend.name + (f" ({backend_note})" if backend_note else "")
