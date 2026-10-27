"""Sector-wise basket selection with QAOA.

The user (or the sector-mix page) fixes HOW MANY stocks to hold in each sector. QAOA then chooses WHICH stocks,
minimising one risk/return objective for the whole portfolio, so correlations between sectors count.

Circuit: one Dicke start state per sector (equal mix of every way to pick that sector's count) and an XY ring mixer
inside each sector. XY gates only swap a held stock with a not-held one in the same sector, so every measured
basket has exactly the requested number of stocks in every sector. Sectors with nothing to choose (hold none, or
hold all) take no qubits.
"""
import time

import numpy as np

from . import combo, config, progress, qlock
from .solver import RESTARTS, SHOTS, dicke_circuit, normalise, ring_edges


def allocate_counts(weights, sizes, k):
    """Split k holdings across sectors in proportion to weights (largest remainder), never above a sector's size."""
    w = np.array([max(float(x), 0.0) for x in weights])
    w = w / w.sum() if w.sum() > 0 else np.full(len(w), 1.0 / len(w))
    if k > sum(sizes):
        raise ValueError("More stocks requested than the sectors offer.")
    quota = k * w
    counts = [min(int(np.floor(v)), s) for v, s in zip(quota, sizes)]
    order = sorted(range(len(w)), key=lambda i: -(quota[i] - np.floor(quota[i])))
    while sum(counts) < k:
        progressed = False
        for i in order + sorted(range(len(w)), key=lambda i: -w[i]):
            if sum(counts) >= k:
                break
            if counts[i] < sizes[i]:
                counts[i] += 1
                progressed = True
        if not progressed:
            break
    return counts


def _layout(groups):
    free, fixed = [], []
    for g, c in groups:
        g = sorted(g)
        if c == 0:
            continue
        if c == len(g):
            fixed += g
        else:
            free.append((g, c))
    qubits = [i for g, _ in free for i in g]
    local, pos = [], 0
    for g, c in free:
        local.append((list(range(pos, pos + len(g))), c))
        pos += len(g)
    return qubits, local, fixed


def _energies(mu_s, sig_s, k, q, qubits, local, fixed):
    n, nf = len(mu_s), len(qubits)
    z = np.arange(2 ** nf)
    bits = ((z[:, None] >> np.arange(nf)) & 1).astype(float)
    X = np.zeros((2 ** nf, n))
    X[:, qubits] = bits
    X[:, fixed] = 1.0
    W = X / k
    f = (1 - q) * np.einsum("ij,jk,ik->i", W, sig_s, W) - q * (W @ mu_s)
    valid = np.ones(2 ** nf, dtype=bool)
    for idxs, c in local:
        valid &= bits[:, idxs].sum(axis=1) == c
    return f, valid, X


def _edges(local):
    return [(idxs[a], idxs[b]) for idxs, _ in local if len(idxs) > 1 for a, b in ring_edges(len(idxs))]


def _edge_index(nf, edges):
    z = np.arange(2 ** nf)
    return [(z[((z >> i) & 1) != ((z >> j) & 1)], (1 << i) | (1 << j)) for i, j in edges]


def _state(params, e_norm, valid, eidx, reps):
    gammas, betas = params[:reps], params[reps:]
    psi = valid.astype(complex)
    psi = psi / np.linalg.norm(psi)
    for g, b in zip(gammas, betas):
        psi = psi * np.exp(-1j * g * e_norm)
        c, s = np.cos(b), -1j * np.sin(b)
        for d, mask in eidx:
            new = psi.copy()
            new[d] = c * psi[d] + s * psi[d ^ mask]
            psi = new
    return psi


def _train(e_norm, valid, eidx, reps, maxiter, seed=7):
    from scipy.optimize import minimize

    rng = np.random.default_rng(seed)
    calls = [0]

    def cost(p):
        calls[0] += 1
        return float((np.abs(_state(p, e_norm, valid, eidx, reps)) ** 2) @ e_norm)

    best = None
    for _ in range(RESTARTS):
        x0 = np.concatenate([rng.uniform(0, 2 * np.pi, reps), rng.uniform(0, np.pi, reps)])
        res = minimize(cost, x0, method="COBYLA", options={"maxiter": maxiter, "rhobeg": 0.5})
        if best is None or res.fun < best.fun:
            best = res
    return best.x, calls[0]


def _qubo_op(mu_s, sig_s, k, q, qubits, fixed):
    from qiskit_optimization import QuadraticProgram

    nf = len(qubits)
    qp = QuadraticProgram("sector")
    for j in range(nf):
        qp.binary_var(f"x{j}")
    a = (1 - q) / (k * k)
    lin = {f"x{j}": 2 * a * float(sum(sig_s[qubits[j], i] for i in fixed)) - q / k * float(mu_s[qubits[j]]) for j in range(nf)}
    quad = {(f"x{j}", f"x{l}"): a * float(sig_s[qubits[j], qubits[l]]) for j in range(nf) for l in range(nf)}
    qp.minimize(linear=lin, quadratic=quad)
    return qp.to_ising()[0]


def _circuit(op, nf, local, edges, params, reps, e_range):
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import PauliEvolutionGate, XXPlusYYGate

    gammas, betas = params[:reps], params[reps:]
    circ = QuantumCircuit(nf)
    for idxs, c in local:
        circ.compose(dicke_circuit(len(idxs), c), qubits=idxs, inplace=True)
    for g, b in zip(gammas, betas):
        circ.append(PauliEvolutionGate(op, time=float(g / e_range)), range(nf))
        for i, j in edges:
            circ.append(XXPlusYYGate(2 * float(b)), [i, j])
    return circ


def _depth(circ):
    def run():
        try:
            from qiskit import transpile

            t = transpile(circ.decompose(reps=3), basis_gates=["cx", "rz", "sx", "x"], optimization_level=1)
            return int(t.depth()), int(t.count_ops().get("cx", 0))
        except Exception:
            return None, None

    return qlock.run(run)


def _run_hardware(circ, nf):
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    from . import ibm

    service = ibm.connect()
    backend, note = ibm.pick_backend(service, nf)

    def build():
        c = circ.copy()
        c.measure_all()
        return generate_preset_pass_manager(optimization_level=1, backend=backend).run(c)

    isa = qlock.run(build)
    job = SamplerV2(mode=backend).run([isa], shots=SHOTS)
    progress.say(f"Sent the sector circuit to {backend.name}")
    counts = ibm.wait(job, "Sector QAOA on IBM")[0].data.meas.get_counts()
    return counts, backend.name + (f" ({note})" if note else ""), job.job_id(), int(isa.depth())


def solve_sector(mu, sigma, k, q, groups, engine="qaoa_sim", reps=2, maxiter=100):
    """groups = [(stock indices in one sector, how many to hold there), ...] with the counts adding up to k."""
    mu_s, sig_s = normalise(mu, sigma)
    n = len(mu_s)
    t0 = time.time()
    te = time.time()
    table = combo.ranked(mu_s, sig_s, k, q, groups)
    exact_seconds = round(time.time() - te, 4)
    rank_of = {b: r + 1 for r, (_, b) in enumerate(table)}
    best_exact = table[0]
    qubits, local, fixed = _layout(groups)
    nf = len(qubits)
    out = {
        "engine": engine, "qubits": nf, "reps": reps, "maxiter": maxiter, "mixer": "xy",
        "exact": {"idx": list(best_exact[1]), "fval": best_exact[0]},
        "feasible_baskets": len(table), "backend": None, "job_id": None, "circuit_depth": None, "cx_gates": None,
        "samples": [], "note": "", "shots": 0, "p_best": None, "p_best_blind": 1.0 / len(table),
        "exact_seconds": exact_seconds, "objective_exact": float(best_exact[0]), "train_evals": 0,
    }
    if engine == "classical" or nf == 0:
        out.update(idx=list(best_exact[1]), fval=best_exact[0], rank=1, matches_exact=True, feasible_mass=1.0,
                   label="Classical exact search", qaoa_seconds=0.0, seconds=round(time.time() - t0, 4),
                   objective_selected=float(best_exact[0]), objective_gap_pct=0.0, engine="classical")
        return out

    f, valid, X = _energies(mu_s, sig_s, k, q, qubits, local, fixed)
    e_range = float(f[valid].max() - f[valid].min()) or 1.0
    e_norm = np.where(valid, (f - f[valid].min()) / e_range, 0.0)
    eidx = _edge_index(nf, _edges(local))
    progress.say(f"Training the sector circuit on a simulator ({nf} qubits)")
    params, evals = _train(e_norm, valid, eidx, reps, maxiter)
    out["train_evals"] = evals
    probs = np.abs(_state(params, e_norm, valid, eidx, reps)) ** 2
    probs = probs / probs.sum()
    out["shots"] = SHOTS
    edges = _edges(local)
    circ = _circuit(_qubo_op(mu_s, sig_s, k, q, qubits, fixed), nf, local, edges, params, reps, e_range)
    out["circuit_depth"], out["cx_gates"] = _depth(circ)

    shot_counts, hw_fail = None, None
    if engine == "qaoa_hw":
        try:
            progress.say("Connecting to IBM Quantum")
            counts, name, job_id, hw_depth = _run_hardware(circ, nf)
            measured = {}
            for bits, c in counts.items():
                zz = sum(int(b) << i for i, b in enumerate(bits[::-1][:nf]))
                measured[zz] = measured.get(zz, 0) + c
            shot_counts, total = measured, sum(counts.values())
            out.update(label="Sector QAOA (IBM quantum processor)", backend=name, job_id=job_id, circuit_depth=hw_depth)
            out["note"] = "Circuit angles were trained on a simulator, then the circuit was run on the IBM processor."
        except Exception as ex:
            hw_fail = f"IBM hardware could not finish this run ({ex}); the simulator result is shown instead."
            print("[ibm]", hw_fail)
            out["engine"] = engine = "qaoa_sim"
    if shot_counts is None:
        draws = np.random.default_rng(11).choice(len(probs), size=SHOTS, p=probs)
        shot_counts, total = dict(zip(*np.unique(draws, return_counts=True))), SHOTS
        out.update(label="Sector QAOA (quantum circuit simulator)", backend="Exact state-vector simulation")

    def full_idx(zz):
        return tuple(sorted(fixed + [qubits[j] for j in range(nf) if (zz >> j) & 1]))

    rows = []
    for zz, c in shot_counts.items():
        zz = int(zz)
        idx = full_idx(zz)
        ok = bool(valid[zz])
        rows.append({"idx": list(idx), "prob": c / total, "feasible": ok, "fval": float(f[zz]) if ok else None, "rank": rank_of.get(idx)})
    feas = [r for r in rows if r["feasible"]]
    out["feasible_mass"] = float(sum(r["prob"] for r in feas))
    best_key = tuple(best_exact[1])
    out["p_best"] = float(next((r["prob"] for r in rows if tuple(r["idx"]) == best_key), 0.0))
    if engine == "qaoa_sim":
        zbest = sum(1 << j for j in range(nf) if qubits[j] in best_key)
        out["p_best"] = float(probs[zbest])
    rows.sort(key=lambda r: -r["prob"])
    out["samples"] = rows[:12]
    if feas:
        pick = min(feas, key=lambda r: (r["fval"], -r["prob"]))
        out.update(idx=pick["idx"], fval=pick["fval"], rank=pick["rank"], matches_exact=tuple(pick["idx"]) == best_key)
    else:
        out.update(idx=list(best_exact[1]), fval=best_exact[0], rank=1, matches_exact=True)
        out["note"] = "The circuit produced no valid basket this time, so the exact answer is shown."
    if hw_fail:
        out["note"] = hw_fail + (" " + out["note"] if out["note"] else "")
    out["seconds"] = round(time.time() - t0, 4)
    out["qaoa_seconds"] = round(max(0.0, out["seconds"] - exact_seconds), 4)
    out["objective_selected"] = float(out["fval"])
    denom = abs(float(best_exact[0]))
    out["objective_gap_pct"] = (100.0 * (float(out["fval"]) - float(best_exact[0])) / denom) if denom > 1e-12 else None
    return out
