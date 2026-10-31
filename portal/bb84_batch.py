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


def _prepare(sessions, n, eve):
    """Random bits and bases for Alice and Bob, plus the state that actually reaches Bob.

    Without an eavesdropper Bob receives exactly what Alice sent. With an intercept-and-resend eavesdropper (Eve)
    each qubit is measured in a random basis and re-sent: when Eve guesses Alice's basis the state is untouched,
    otherwise it collapses to a random bit in Eve's basis. That is what makes about 25% of the kept bits wrong.
    """
    alice_bits = [_bits(n) for _ in range(sessions)]
    alice_bases = [_bits(n) for _ in range(sessions)]
    bob_bases = [_bits(n) for _ in range(sessions)]
    sent_bits = [row[:] for row in alice_bits]
    sent_bases = [row[:] for row in alice_bases]
    if eve:
        for s in range(sessions):
            for i in range(n):
                eve_basis = secrets.randbelow(2)
                if eve_basis != alice_bases[s][i]:
                    sent_bits[s][i] = secrets.randbelow(2)
                    sent_bases[s][i] = eve_basis
    return alice_bits, alice_bases, bob_bases, sent_bits, sent_bases


def build(sessions=SESSIONS, n=QUBITS, eve=False):
    """Random bits and bases for Alice and Bob, and one preparation+measurement circuit per session."""
    from qiskit import QuantumCircuit

    alice_bits, alice_bases, bob_bases, sent_bits, sent_bases = _prepare(sessions, n, eve)
    circuits = []
    for s in range(sessions):
        qc = QuantumCircuit(n)
        for i in range(n):
            if sent_bits[s][i]:
                qc.x(i)
            if sent_bases[s][i]:
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
    trace = {"kept_positions": [], "sample_positions": [], "sample_alice_bits": [], "sample_bob_bits": []}
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
        if s == 0:
            trace = {
                "kept_positions": keep[:24],
                "sample_positions": sample[:24],
                "sample_alice_bits": [alice_bits[s][i] for i in sample[:24]],
                "sample_bob_bits": [bob_bits[i] for i in sample[:24]],
            }
    return secret, {"sifted_bits": sifted_total, "sample_bits": sample_total, "errors": errors, "trace": trace}


def run(execute, sessions=SESSIONS, n=QUBITS, eve=False):
    """execute(circuits) -> (list of one measured bitstring per circuit, job_id, backend_name)."""
    alice_bits, alice_bases, bob_bases, circuits = build(sessions, n, eve)
    strings, job_id, backend = execute(circuits)
    secret, stats = sift(alice_bits, alice_bases, bob_bases, strings, n)
    trace = {
        "alice_bits": [row[:24] for row in alice_bits[:1]][0],
        "alice_bases": [row[:24] for row in alice_bases[:1]][0],
        "bob_bases": [row[:24] for row in bob_bases[:1]][0],
        "kept_positions": [i for i in range(min(n, 24)) if alice_bases[0][i] == bob_bases[0][i]],
        "eve": bool(eve),
    }
    sample_trace = stats.pop("trace", {})
    return {"secret": secret, "sessions": sessions, "raw_qubits": sessions * n, "job_id": job_id,
            "backend": backend, "trace": {**trace, **sample_trace}, **stats}


def run_model(sessions=SESSIONS, n=QUBITS, eve=False):
    """The same protocol without Qiskit: Bob's result is computed from the exact BB84 measurement rule
    (same basis as the sent state: the bit; different basis: a fair coin). Only used when Qiskit is not installed
    on the server, and it is labelled as such in the result so nobody mistakes it for a circuit run."""
    alice_bits, alice_bases, bob_bases, sent_bits, sent_bases = _prepare(sessions, n, eve)
    strings = []
    for s in range(sessions):
        bob = [sent_bits[s][i] if bob_bases[s][i] == sent_bases[s][i] else secrets.randbelow(2) for i in range(n)]
        strings.append("".join(str(b) for b in reversed(bob)))  # rightmost character = qubit 0, like Qiskit
    secret, stats = sift(alice_bits, alice_bases, bob_bases, strings, n)
    sample_trace = stats.pop("trace", {})
    trace = {"alice_bits": alice_bits[0][:24], "alice_bases": alice_bases[0][:24],
             "bob_bases": bob_bases[0][:24],
             "eve": bool(eve), **sample_trace}
    return {"secret": secret, "sessions": sessions, "raw_qubits": sessions * n, "job_id": None,
            "backend": "Python BB84 model (Qiskit not installed)", "trace": trace, **stats}


# ----- executors -----------------------------------------------------------------------------------
def execute_simulator(circuits):
    from qiskit_aer import AerSimulator

    sim = AerSimulator()
    res = sim.run(circuits, shots=1, memory=True).result()
    return [res.get_memory(i)[0] for i in range(len(circuits))], None, "Qiskit Aer simulator"


def execute_hardware(circuits):
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    from . import ibm, progress, qlock

    progress.say("Making the BB84 key: connecting to IBM")
    service = ibm.connect()
    backend, backend_note = ibm.pick_backend(service, QUBITS)
    isa = qlock.run(lambda: generate_preset_pass_manager(optimization_level=1, backend=backend).run(circuits))
    job = SamplerV2(mode=backend).run([(c,) for c in isa], shots=1)  # one job; waiting happens off the Qiskit thread
    result = ibm.wait(job, "BB84 key on IBM")
    strings = [next(iter(r.data.meas.get_counts())) for r in result]
    return strings, job.job_id(), backend.name + (f" ({backend_note})" if backend_note else "")
