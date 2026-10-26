"""Check the real IBM connection step by step and time each step.   Run from the repo root:  python check_ibm.py
Prints no secrets. Send the output back if any step fails."""
import time

from portal import config, ibm

def step(t, msg): print(f"[{time.time() - t:6.1f}s] {msg}", flush=True)

t0 = time.time()
print("token set:", bool(config.IBM_TOKEN), "| instance set:", bool(config.IBM_INSTANCE), "| preferred:", config.IBM_BACKEND)
svc = ibm.connect(); step(t0, "connected to IBM Quantum")
rows = []
for b in svc.backends(simulator=False, operational=True):
    try: rows.append((b.status().pending_jobs, b.name, b.num_qubits))
    except Exception: pass
for q, n, nq in sorted(rows): print(f"   {n:18s} {nq:4d} qubits  {q:4d} jobs waiting")
backend, note = ibm.pick_backend(svc, 5); step(t0, f"chosen: {backend.name} {('(' + note + ')') if note else ''}")

from qiskit import QuantumCircuit
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_ibm_runtime import SamplerV2
qc = QuantumCircuit(2); qc.h(0); qc.cx(0, 1); qc.measure_all()
isa = generate_preset_pass_manager(optimization_level=1, backend=backend).run(qc); step(t0, "compiled a 2-qubit test circuit")
job = SamplerV2(mode=backend).run([isa], shots=100); step(t0, f"submitted job {job.job_id()}")
t1 = time.time(); last = None
while True:
    st = job.status(); st = getattr(st, "name", str(st))
    if st != last or int(time.time() - t1) % 15 == 0: step(t0, f"job state: {st} (waited {int(time.time() - t1)}s)"); last = st
    if st in ("DONE", "COMPLETED"): break
    if st in ("ERROR", "CANCELLED", "FAILED"): raise SystemExit(f"job ended: {st}")
    if time.time() - t1 > 1200: raise SystemExit("still waiting after 20 min; IBM queue is slow today")
    time.sleep(1)
print("counts:", job.result()[0].data.meas.get_counts()); step(t0, "DONE - IBM path works end to end")
