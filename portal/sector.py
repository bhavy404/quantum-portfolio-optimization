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
    """Solve sector-constrained portfolio selection with QAOA or exact search.

    Timing fields distinguish exact enumeration, QAOA pipeline stages, and
    total wall-clock time. The legacy fields remain available for compatibility.
    """
    t0 = time.perf_counter()

    # Prepare the normalized inputs and sector layout.
    t_pre = time.perf_counter()
    mu_s, sig_s = normalise(mu, sigma)
    qubits, local, fixed = _layout(groups)
    n = len(mu_s)
    nf = len(qubits)
    preprocessing_seconds = time.perf_counter() - t_pre

    # Exact enumeration is retained as the reference solution.
    t_exact = time.perf_counter()
    table = combo.ranked(mu_s, sig_s, k, q, groups)
    exact_seconds = time.perf_counter() - t_exact

    if not table:
        raise ValueError("No feasible baskets exist for the supplied sector constraints.")

    rank_of = {b: r + 1 for r, (_, b) in enumerate(table)}
    best_exact = table[0]
    best_key = tuple(best_exact[1])

    out = {
        "engine": engine,
        "qubits": nf,
        "reps": reps,
        "maxiter": maxiter,
        "mixer": "xy",
        "exact": {"idx": list(best_exact[1]), "fval": best_exact[0]},
        "feasible_baskets": len(table),
        "backend": None,
        "job_id": None,
        "circuit_depth": None,
        "cx_gates": None,
        "samples": [],
        "note": "",
        "shots": 0,
        "p_best": None,
        "p_best_blind": 1.0 / len(table),
        "p_best_statevector": None,
        "p_best_empirical": None,
        "p_best_source": None,
        "statevector_feasible_mass": None,
        "feasible_mass": None,
        "qaoa_sample_found_feasible": False,
        "used_exact_fallback": False,
        "exact_seconds": round(exact_seconds, 6),
        "preprocessing_seconds": round(preprocessing_seconds, 6),
        "training_seconds": 0.0,
        "statevector_seconds": 0.0,
        "circuit_build_seconds": 0.0,
        "sampling_seconds": 0.0,
        "postprocessing_seconds": 0.0,
        "objective_exact": float(best_exact[0]),
        "train_evals": 0,
    }

    # Exact-only cases do not execute a QAOA circuit.
    if engine == "classical" or nf == 0:
        total_seconds = time.perf_counter() - t0
        out.update(
            idx=list(best_exact[1]),
            fval=float(best_exact[0]),
            rank=1,
            matches_exact=True,
            feasible_mass=1.0,
            statevector_feasible_mass=None,
            label="Classical exact search",
            qaoa_seconds=0.0,
            seconds=round(total_seconds, 6),
            total_seconds=round(total_seconds, 6),
            objective_selected=float(best_exact[0]),
            objective_gap_pct=0.0,
            engine="classical",
            p_best_source="not_applicable_classical",
        )
        return out

    # Construct the sector-constrained objective and feasible-state mask.
    f, valid, X = _energies(mu_s, sig_s, k, q, qubits, local, fixed)
    e_range = float(f[valid].max() - f[valid].min()) or 1.0
    e_norm = np.where(valid, (f - f[valid].min()) / e_range, 0.0)
    edges = _edges(local)
    eidx = _edge_index(nf, edges)

    # Optimize QAOA angles on the statevector simulator.
    progress.say(f"Training the sector circuit on a simulator ({nf} qubits)")
    t_train = time.perf_counter()
    params, evals = _train(e_norm, valid, eidx, reps, maxiter)
    training_seconds = time.perf_counter() - t_train
    out["train_evals"] = int(evals)

    # Calculate ideal statevector probabilities separately from shot counts.
    t_state = time.perf_counter()
    probs = np.abs(_state(params, e_norm, valid, eidx, reps)) ** 2
    prob_sum = float(probs.sum())
    if prob_sum <= 0.0:
        raise RuntimeError("The sector statevector has zero total probability.")
    probs = probs / prob_sum

    zbest = sum(
        1 << j for j in range(nf)
        if qubits[j] in best_key
    )
    p_best_statevector = float(probs[zbest])
    statevector_feasible_mass = float(probs[valid].sum())
    statevector_seconds = time.perf_counter() - t_state

    # Build and transpile the QAOA circuit.
    t_circuit = time.perf_counter()
    op = _qubo_op(mu_s, sig_s, k, q, qubits, fixed)
    circ = _circuit(op, nf, local, edges, params, reps, e_range)
    circuit_depth, cx_gates = _depth(circ)
    circuit_build_seconds = time.perf_counter() - t_circuit

    out.update(
        circuit_depth=circuit_depth,
        cx_gates=cx_gates,
        p_best_statevector=p_best_statevector,
        statevector_feasible_mass=statevector_feasible_mass,
        shots=SHOTS,
    )

    # Execute either on IBM hardware or by sampling the statevector distribution.
    shot_counts = None
    hw_fail = None
    total_shots = 0
    sampling_engine = engine

    if engine == "qaoa_hw":
        t_sampling = time.perf_counter()
        try:
            progress.say("Connecting to IBM Quantum")
            counts, name, job_id, hw_depth = _run_hardware(circ, nf)

            measured = {}
            for bits, count in counts.items():
                zz = sum(
                    int(bit) << i
                    for i, bit in enumerate(bits[::-1][:nf])
                )
                measured[zz] = measured.get(zz, 0) + count

            shot_counts = measured
            total_shots = sum(measured.values())

            if total_shots <= 0:
                raise RuntimeError("IBM hardware returned no measurement shots.")

            out.update(
                label="Sector QAOA (IBM quantum processor)",
                backend=name,
                job_id=job_id,
                circuit_depth=hw_depth,
                note=(
                    "Circuit angles were trained on a simulator, then the "
                    "circuit was run on the IBM processor."
                ),
            )
        except Exception as ex:
            hw_fail = (
                f"IBM hardware could not finish this run ({ex}); "
                "the simulator result is shown instead."
            )
            print("[ibm]", hw_fail)
            sampling_engine = "qaoa_sim"
            out["engine"] = "qaoa_sim"

        if shot_counts is None:
            draws = np.random.default_rng(11).choice(
                len(probs), size=SHOTS, p=probs
            )
            unique, counts = np.unique(draws, return_counts=True)
            shot_counts = dict(zip(unique.tolist(), counts.tolist()))
            total_shots = SHOTS
            out.update(
                label="Sector QAOA (quantum circuit simulator)",
                backend="Exact state-vector simulation",
            )

        sampling_seconds = time.perf_counter() - t_sampling

    else:
        t_sampling = time.perf_counter()
        draws = np.random.default_rng(11).choice(
            len(probs), size=SHOTS, p=probs
        )
        unique, counts = np.unique(draws, return_counts=True)
        shot_counts = dict(zip(unique.tolist(), counts.tolist()))
        total_shots = SHOTS
        sampling_seconds = time.perf_counter() - t_sampling
        out.update(
            label="Sector QAOA (quantum circuit simulator)",
            backend="Exact state-vector simulation",
        )

    # Convert measured bitstrings into full stock-index baskets.
    t_post = time.perf_counter()

    def full_idx(zz):
        return tuple(sorted(
            fixed + [
                qubits[j]
                for j in range(nf)
                if (zz >> j) & 1
            ]
        ))

    rows = []
    empirical_counts = {}

    for zz, count in shot_counts.items():
        zz = int(zz)
        idx = full_idx(zz)
        empirical_counts[idx] = empirical_counts.get(idx, 0) + int(count)

        ok = bool(valid[zz])
        rows.append({
            "idx": list(idx),
            "prob": int(count) / total_shots,
            "feasible": ok,
            "fval": float(f[zz]) if ok else None,
            "rank": rank_of.get(idx),
        })

    feasible_rows = [row for row in rows if row["feasible"]]
    feasible_shots = sum(
        int(count)
        for zz, count in shot_counts.items()
        if valid[int(zz)]
    )
    sampled_feasible_mass = (
        float(feasible_shots / total_shots) if total_shots else 0.0
    )

    p_best_empirical = (
        float(empirical_counts.get(best_key, 0) / total_shots)
        if total_shots else 0.0
    )

    out.update(
        feasible_mass=sampled_feasible_mass,
        qaoa_sample_found_feasible=bool(feasible_rows),
        p_best_empirical=p_best_empirical,
    )

    # Preserve the historical p_best meaning while declaring its source.
    if sampling_engine == "qaoa_sim":
        out["p_best"] = p_best_statevector
        out["p_best_source"] = "ideal_statevector_probability"
    else:
        out["p_best"] = p_best_empirical
        out["p_best_source"] = "hardware_empirical_shot_frequency"

    rows.sort(key=lambda row: -row["prob"])
    out["samples"] = rows[:12]

    if feasible_rows:
        pick = min(
            feasible_rows,
            key=lambda row: (row["fval"], -row["prob"]),
        )
        out.update(
            idx=pick["idx"],
            fval=float(pick["fval"]),
            rank=pick["rank"],
            matches_exact=tuple(pick["idx"]) == best_key,
        )
    else:
        # Explicitly mark the classical fallback; do not count it as a QAOA hit.
        out.update(
            idx=list(best_exact[1]),
            fval=float(best_exact[0]),
            rank=1,
            matches_exact=True,
            used_exact_fallback=True,
        )
        out["note"] = (
            "The circuit produced no valid measured basket; "
            "the exact classical optimum is shown as a fallback."
        )

    if hw_fail:
        out["note"] = hw_fail + (
            " " + out["note"] if out["note"] else ""
        )

    postprocessing_seconds = time.perf_counter() - t_post
    total_seconds = time.perf_counter() - t0

    # Backward-compatible timing field: all post-exact pipeline work,
    # not pure quantum execution time.
    qaoa_pipeline_seconds = (
        training_seconds
        + statevector_seconds
        + circuit_build_seconds
        + sampling_seconds
        + postprocessing_seconds
    )

    out.update(
        preprocessing_seconds=round(preprocessing_seconds, 6),
        training_seconds=round(training_seconds, 6),
        statevector_seconds=round(statevector_seconds, 6),
        circuit_build_seconds=round(circuit_build_seconds, 6),
        sampling_seconds=round(sampling_seconds, 6),
        postprocessing_seconds=round(postprocessing_seconds, 6),
        qaoa_seconds=round(qaoa_pipeline_seconds, 6),
        seconds=round(total_seconds, 6),
        total_seconds=round(total_seconds, 6),
        objective_selected=float(out["fval"]),
    )

    denom = abs(float(best_exact[0]))
    out["objective_gap_pct"] = (
        100.0 * (float(out["fval"]) - float(best_exact[0])) / denom
        if denom > 1e-12 else None
    )

    return out
