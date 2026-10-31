"""Basket selection: the Markowitz problem as a QUBO, solved with QAOA and checked against the exact answer.

Objective for a basket x (x_i = 1 if stock i is held, exactly k stocks held, equal weights w = x/k):
    f(x) = (1 - q) * risk(w) - q * return(w)
Return and variance are rescaled to comparable size so the Safety <-> Growth slider q behaves evenly.
"Exactly k stocks" is enforced with a penalty term, giving an unconstrained binary problem (a QUBO).

QAOA engine
-----------
* The simulator computes the QAOA state exactly with NumPy (state vector of 2^n amplitudes), trains the
  circuit angles with COBYLA from several starting points, then "measures" the final state a fixed number
  of times, exactly as hardware would. Exact simulation makes 12-qubit runs take seconds instead of minutes.
* The hardware engine takes the trained angles, builds the same circuit in Qiskit and runs it on an IBM
  quantum processor.
* Two circuit types. "x" is the textbook QAOA (X mixer, equal superposition of all 2^n baskets), which wastes
  most of its weight on baskets with the wrong number of stocks. "xy" is the constraint-preserving version:
  it starts in an equal superposition of only the valid baskets (a Dicke state) and mixes with XY (partial
  swap) gates on a ring, which never change how many stocks are held. Every measured basket is valid.
* The result reports how likely the circuit made the best basket versus blind guessing. That number, not
  "it found the answer", is the honest measure of what the quantum step contributed.
"""
import itertools
import time

import numpy as np

from . import config, progress, qlock

SHOTS = 2048
RESTARTS = 6


# ----- the problem ---------------------------------------------------------------------------------
def normalise(mu: np.ndarray, sigma: np.ndarray):
    mu_scale = float(np.mean(np.abs(mu))) or 1.0
    var_scale = float(np.mean(np.diag(sigma))) or 1.0
    return mu / mu_scale, sigma / var_scale


def objective(x: np.ndarray, mu_s: np.ndarray, sig_s: np.ndarray, k: int, q: float) -> float:
    w = x / k
    return float((1 - q) * (w @ sig_s @ w) - q * (w @ mu_s))


def exact(mu_s, sig_s, k, q):
    """Every basket of k stocks, best first. Fast for up to ~16 stocks."""
    n = len(mu_s)
    scored = []
    for combo in itertools.combinations(range(n), k):
        x = np.zeros(n)
        x[list(combo)] = 1.0
        scored.append((objective(x, mu_s, sig_s, k, q), combo))
    scored.sort(key=lambda t: t[0])
    return scored


def _all_states(n):
    z = np.arange(2 ** n)
    return ((z[:, None] >> np.arange(n)) & 1).astype(float)  # bit i of state z is stock i


def _energies(mu_s, sig_s, k, q):
    """QUBO energy of every one of the 2^n bitstrings, plus the penalty weight used."""
    n = len(mu_s)
    X = _all_states(n)
    W = X / k
    f = (1 - q) * np.einsum("ij,jk,ik->i", W, sig_s, W) - q * (W @ mu_s)
    count = X.sum(axis=1)
    penalty = float(f.max() - f.min()) + 1e-6
    return f + penalty * (count - k) ** 2, count == k, penalty, X


def _qubo_program(mu_s, sig_s, k, q, penalty):
    return qlock.run(_qubo_program_unlocked, mu_s, sig_s, k, q, penalty)


def _qubo_program_unlocked(mu_s, sig_s, k, q, penalty):
    """The same QUBO as a Qiskit QuadraticProgram (used for the circuit and its Ising operator)."""
    from qiskit_optimization import QuadraticProgram

    n = len(mu_s)
    qp = QuadraticProgram("basket")
    for i in range(n):
        qp.binary_var(f"x{i}")
    lin = {f"x{i}": -q / k * float(mu_s[i]) - 2 * penalty * k for i in range(n)}
    quad = {(f"x{i}", f"x{j}"): (1 - q) / (k * k) * float(sig_s[i, j]) + penalty for i in range(n) for j in range(n)}
    qp.minimize(constant=penalty * k * k, linear=lin, quadratic=quad)
    return qp


# ----- QAOA, exact simulation ----------------------------------------------------------------------
def _apply_mixer(psi, beta, n):
    c, s = np.cos(beta), -1j * np.sin(beta)
    psi = psi.reshape((2,) * n)
    for ax in range(n):
        a = np.take(psi, 0, axis=ax)
        b = np.take(psi, 1, axis=ax)
        psi = np.stack((c * a + s * b, s * a + c * b), axis=ax)
    return psi.reshape(-1)


def ring_edges(n):
    """Edges of the XY mixer ring, in application order (even edges first so hardware can run them in parallel)."""
    if n == 2:
        return [(0, 1)]
    return [(i, i + 1) for i in range(0, n - 1, 2)] + [(i, (i + 1) % n) for i in range(1, n, 2)]


_XY_CACHE = {}


def _xy_index(n):
    if n not in _XY_CACHE:
        z = np.arange(2 ** n)
        _XY_CACHE[n] = [(z[((z >> i) & 1) != ((z >> j) & 1)], (1 << i) | (1 << j)) for i, j in ring_edges(n)]
    return _XY_CACHE[n]


def _apply_xy_mixer(psi, beta, n):
    """exp(-i*beta*(XX+YY)/2) on every ring edge. Moves amplitude between 01 and 10 only, so the number of
    ones (stocks held) never changes."""
    c, s = np.cos(beta), -1j * np.sin(beta)
    for d, mask in _xy_index(n):
        new = psi.copy()
        new[d] = c * psi[d] + s * psi[d ^ mask]
        psi = new
    return psi


def _dicke_vector(n, k):
    z = np.arange(2 ** n)
    ones = np.array([bin(v).count("1") for v in z])
    v = (ones == k).astype(complex)
    return v / np.linalg.norm(v)


def _state(params, e_norm, n, reps, mixer="x", k=None):
    gammas, betas = params[:reps], params[reps:]
    if mixer == "xy":
        psi = _dicke_vector(n, k)
        for g, b in zip(gammas, betas):
            psi = psi * np.exp(-1j * g * e_norm)
            psi = _apply_xy_mixer(psi, b, n)
        return psi
    psi = np.full(2 ** n, 1 / np.sqrt(2 ** n), dtype=complex)
    for g, b in zip(gammas, betas):
        psi = psi * np.exp(-1j * g * e_norm)
        psi = _apply_mixer(psi, b, n)
    return psi


def _train(e_norm, n, reps, maxiter, seed=7, mixer="x", k=None):
    from scipy.optimize import minimize

    rng = np.random.default_rng(seed)

    calls = [0]

    def cost(p):
        calls[0] += 1
        pr = np.abs(_state(p, e_norm, n, reps, mixer, k)) ** 2
        return float(pr @ e_norm)

    best = None
    for _ in range(RESTARTS):
        x0 = np.concatenate([rng.uniform(0, 2 * np.pi, reps), rng.uniform(0, np.pi, reps)])
        res = minimize(cost, x0, method="COBYLA", options={"maxiter": maxiter, "rhobeg": 0.5})
        if best is None or res.fun < best.fun:
            best = res
    return best.x, float(best.fun), calls[0]


def dicke_circuit(n, k):
    """Equal superposition of all n-bit strings with exactly k ones (Baertschi-Eidenbenz construction,
    checked against the exact state for every n <= 12)."""
    from math import acos, sqrt

    from qiskit import QuantumCircuit

    qc = QuantumCircuit(n)
    for i in range(n - k, n):
        qc.x(i)

    def block(m, kk):  # acts on the first m qubits; bottom qubit = m-1
        bot = m - 1
        qc.cx(bot - 1, bot)
        qc.cry(2 * acos(sqrt(1 / m)), bot, bot - 1)
        qc.cx(bot - 1, bot)
        for l in range(2, kk + 1):
            a, mid = bot - l, bot - l + 1
            th = 2 * acos(sqrt(l / m))
            qc.cx(a, bot)
            qc.ry(th / 2, a)
            qc.ccx(bot, mid, a)
            qc.ry(-th / 2, a)
            qc.ccx(bot, mid, a)
            qc.cx(a, bot)

    for m in range(n, k, -1):
        block(m, k)
    for m in range(k, 1, -1):
        block(m, m - 1)
    return qc


def _qiskit_circuit(qp, params, reps, e_range, mixer="x", k=None):
    """The trained QAOA circuit in Qiskit (angles mapped to Qiskit's convention)."""
    gammas, betas = params[:reps], params[reps:]
    if mixer == "xy":
        from qiskit import QuantumCircuit
        from qiskit.circuit.library import PauliEvolutionGate, XXPlusYYGate

        op, _ = qp.to_ising()
        n = op.num_qubits
        circ = QuantumCircuit(n)
        circ.compose(dicke_circuit(n, k), inplace=True)
        for g, b in zip(gammas, betas):
            circ.append(PauliEvolutionGate(op, time=float(g / e_range)), range(n))
            for i, j in ring_edges(n):
                circ.append(XXPlusYYGate(2 * float(b)), [i, j])
        return circ
    from qiskit.circuit.library import QAOAAnsatz

    op, _ = qp.to_ising()
    circ = QAOAAnsatz(op, reps=reps)
    values = {}
    for p in circ.parameters:
        idx = int(p.name[p.name.index("[") + 1:-1])
        values[p] = float(gammas[idx] / e_range) if p.name[0] == "γ" else float(betas[idx])
    return circ.assign_parameters(values)


def _depth(qp, params, reps, e_range, mixer="x", k=None):
    return qlock.run(_depth_unlocked, qp, params, reps, e_range, mixer, k)


def _depth_unlocked(qp, params, reps, e_range, mixer="x", k=None):
    try:
        from qiskit import transpile

        circ = _qiskit_circuit(qp, params, reps, e_range, mixer, k).decompose(reps=3)
        t = transpile(circ, basis_gates=["cx", "rz", "sx", "x"], optimization_level=1)
        return int(t.depth()), int(t.count_ops().get("cx", 0))
    except Exception:
        return None, None


def _run_hardware(qp, params, reps, e_range, n, shots=SHOTS, mixer="x", k=None):
    from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
    from qiskit_ibm_runtime import SamplerV2

    from . import ibm

    service = ibm.connect()
    backend, backend_note = ibm.pick_backend(service, n)
    def build():
        circ = _qiskit_circuit(qp, params, reps, e_range, mixer, k)
        circ.measure_all()
        return generate_preset_pass_manager(optimization_level=1, backend=backend).run(circ)

    isa = qlock.run(build)  # building and compiling happen on the Qiskit thread; waiting on IBM does not
    job = SamplerV2(mode=backend).run([isa], shots=shots)
    progress.say(f"Sent the circuit to {backend.name}")
    counts = ibm.wait(job, "QAOA on IBM")[0].data.meas.get_counts()
    shown = backend.name + (f" ({backend_note})" if backend_note else "")
    return counts, shown, job.job_id(), int(isa.depth())


# ----- public entry point --------------------------------------------------------------------------
def solve(mu, sigma, k, q, engine="qaoa_sim", reps=2, maxiter=100, mixer="xy", selection="most_frequent"):
    """selection: 'most_frequent' = answer is the valid basket the circuit measured most often (pure QAOA output).
    'best_of_shots' = score every sampled valid basket classically and keep the best (near-classical search)."""
    mu_s, sig_s = normalise(mu, sigma)
    n = len(mu_s)
    t0 = time.time()
    exact_t0 = time.time()
    ranked = exact(mu_s, sig_s, k, q)
    exact_seconds = round(time.time() - exact_t0, 4)
    best_exact = ranked[0]
    rank_of = {combo: r + 1 for r, (_, combo) in enumerate(ranked)}
    out = {
        "engine": engine, "qubits": n, "reps": reps, "maxiter": maxiter, "mixer": mixer,
        "exact": {"idx": list(best_exact[1]), "fval": best_exact[0]},
        "feasible_baskets": len(ranked), "backend": None, "job_id": None, "circuit_depth": None,
        "exact_seconds": exact_seconds, "objective_exact": float(best_exact[0]),
        "cx_gates": None, "samples": [], "note": "", "shots": 0,
        "p_best": None, "p_best_blind": 1.0 / len(ranked), "train_evals": 0,
    }
    if engine == "classical":
        out.update(idx=list(best_exact[1]), fval=best_exact[0], rank=1, matches_exact=True, feasible_mass=1.0,
                   label="Classical exact search", seconds=round(time.time() - t0, 4), qaoa_seconds=0.0)
        return out

    E, feasible, penalty, X = _energies(mu_s, sig_s, k, q)
    if mixer == "xy":
        # The state never leaves the valid baskets, where the penalty is a constant. Scale by the valid range only.
        e_range = float(E[feasible].max() - E[feasible].min()) or 1.0
        e_norm = (E - E[feasible].min()) / e_range
    else:
        e_range = float(E.max() - E.min()) or 1.0
        e_norm = (E - E.min()) / e_range
    progress.say(f"Training the quantum circuit on a simulator ({n} qubits)")
    params, _, evals = _train(e_norm, n, reps, maxiter, mixer=mixer, k=k)
    out["train_evals"] = evals
    probs = np.abs(_state(params, e_norm, n, reps, mixer, k)) ** 2
    qp = _qubo_program(mu_s, sig_s, k, q, penalty)
    out["circuit_depth"], out["cx_gates"] = _depth(qp, params, reps, e_range, mixer, k)
    out["shots"] = SHOTS

    hw_fail = None
    if engine == "qaoa_hw":
        try:
            progress.say("Connecting to IBM Quantum")
            counts, name, job_id, hw_depth = _run_hardware(qp, params, reps, e_range, n, mixer=mixer, k=k)
        except Exception as ex:
            hw_fail = f"IBM hardware could not finish this run ({ex}); the simulator result is shown instead."
            print("[ibm]", hw_fail)
            engine = "qaoa_sim"
            out["engine"] = "qaoa_sim"
    if engine == "qaoa_hw":
        total = sum(counts.values())
        measured = {}
        for bits, c in counts.items():
            z = sum(int(b) << i for i, b in enumerate(bits[::-1][:n]))  # rightmost character = qubit 0
            measured[z] = measured.get(z, 0) + c
        out.update(label="QAOA (IBM quantum processor)", backend=name, job_id=job_id, circuit_depth=hw_depth)
        out["note"] = "Circuit angles were trained on a simulator, then the circuit was run on the IBM processor."
        shot_counts = measured
    else:
        rng = np.random.default_rng(11)
        draws = rng.choice(len(probs), size=SHOTS, p=probs / probs.sum())
        shot_counts = dict(zip(*np.unique(draws, return_counts=True)))
        out.update(label="QAOA (quantum circuit simulator)", backend="Exact state-vector simulation")
        total = SHOTS

    rows = []
    for z, c in shot_counts.items():
        z = int(z)
        idx = tuple(int(i) for i in np.flatnonzero(X[z]))
        ok = bool(feasible[z])
        rows.append({"idx": list(idx), "prob": c / total, "feasible": ok,
                     "fval": objective(X[z], mu_s, sig_s, k, q) if ok else None, "rank": rank_of.get(idx)})
    feas = [r for r in rows if r["feasible"]]
    out["feasible_mass"] = float(sum(r["prob"] for r in feas))
    best_key = out["exact"]["idx"]
    out["p_best"] = float(next((r["prob"] for r in rows if r["idx"] == best_key), 0.0))
    if engine == "qaoa_sim":
        out["p_best"] = float(probs[sum(1 << i for i in best_key)])  # exact probability, no sampling noise
    rows.sort(key=lambda r: -r["prob"])
    out["samples"] = rows[:12]
    if feas:
        if selection == "best_of_shots":
            pick = min(feas, key=lambda r: (r["fval"], -r["prob"]))
        else:
            pick = max(feas, key=lambda r: (r["prob"], -r["fval"]))
        out.update(idx=pick["idx"], fval=pick["fval"], rank=pick["rank"], matches_exact=pick["idx"] == best_key)
    else:
        out.update(idx=list(best_exact[1]), fval=best_exact[0], rank=1, matches_exact=True)
        out["note"] = "The circuit produced no valid basket this time, so the exact answer is shown."
    if hw_fail:
        out["note"] = hw_fail + (" " + out["note"] if out["note"] else "")
    out["seconds"] = round(time.time() - t0, 4)
    out["qaoa_seconds"] = round(max(0.0, out["seconds"] - exact_seconds), 4)
    out["objective_selected"] = float(out["fval"])
    # Lower objective is better. The percentage gap is descriptive only when the optimum is positive.
    denom = abs(float(best_exact[0]))
    out["objective_gap_pct"] = (100.0 * (float(out["fval"]) - float(best_exact[0])) / denom) if denom > 1e-12 else None
    return out
