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

def solve(mu, sigma, k, q, engine="qaoa_sim", reps=2, maxiter=100, mixer="xy"):
    """Solve cardinality-constrained portfolio selection with QAOA or exact search.

    Timing fields separate exact enumeration from the QAOA pipeline.
    Legacy output fields are retained for application compatibility.
    """
    t0 = time.perf_counter()

    # Input normalization.
    t_pre = time.perf_counter()
    mu_s, sig_s = normalise(mu, sigma)
    n = len(mu_s)
    preprocessing_seconds = time.perf_counter() - t_pre

    # Exact classical reference solution.
    t_exact = time.perf_counter()
    ranked = exact(mu_s, sig_s, k, q)
    exact_seconds = time.perf_counter() - t_exact

    if not ranked:
        raise ValueError("No feasible portfolio baskets exist for the supplied inputs.")

    best_exact = ranked[0]
    best_key = tuple(best_exact[1])
    rank_of = {basket: r + 1 for r, (_, basket) in enumerate(ranked)}

    out = {
        "engine": engine,
        "qubits": n,
        "reps": reps,
        "maxiter": maxiter,
        "mixer": mixer,
        "exact": {"idx": list(best_exact[1]), "fval": best_exact[0]},
        "feasible_baskets": len(ranked),
        "backend": None,
        "job_id": None,
        "circuit_depth": None,
        "cx_gates": None,
        "samples": [],
        "note": "",
        "shots": 0,
        "p_best": None,
        "p_best_blind": 1.0 / len(ranked),
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

    # Exact-only mode.
    if engine == "classical":
        total_seconds = time.perf_counter() - t0
        out.update(
            idx=list(best_exact[1]),
            fval=float(best_exact[0]),
            rank=1,
            matches_exact=True,
            feasible_mass=1.0,
            label="Classical exact search",
            seconds=round(total_seconds, 6),
            total_seconds=round(total_seconds, 6),
            qaoa_seconds=0.0,
            objective_selected=float(best_exact[0]),
            objective_gap_pct=0.0,
            engine="classical",
            p_best_source="not_applicable_classical",
        )
        return out

    # Build the objective and feasible-state mask.
    t_pre_qaoa = time.perf_counter()
    E, feasible, penalty, X = _energies(mu_s, sig_s, k, q)

    if mixer == "xy":
        # The XY mixer preserves cardinality, so normalize over feasible energies.
        e_range = float(E[feasible].max() - E[feasible].min()) or 1.0
        e_norm = (E - E[feasible].min()) / e_range
    else:
        e_range = float(E.max() - E.min()) or 1.0
        e_norm = (E - E.min()) / e_range

    preprocessing_seconds += time.perf_counter() - t_pre_qaoa

    # Train QAOA parameters.
    progress.say(f"Training the quantum circuit on a simulator ({n} qubits)")
    t_train = time.perf_counter()
    params, _, evals = _train(
        e_norm, n, reps, maxiter, mixer=mixer, k=k
    )
    training_seconds = time.perf_counter() - t_train
    out["train_evals"] = int(evals)

    # Calculate ideal statevector probabilities.
    t_state = time.perf_counter()
    probs = np.abs(_state(params, e_norm, n, reps, mixer, k)) ** 2
    prob_sum = float(probs.sum())

    if prob_sum <= 0.0:
        raise RuntimeError("The QAOA statevector has zero total probability.")

    probs = probs / prob_sum
    best_state = sum(1 << i for i in best_key)
    p_best_statevector = float(probs[best_state])
    statevector_feasible_mass = float(probs[feasible].sum())
    statevector_seconds = time.perf_counter() - t_state

    # Construct the QUBO and compile the circuit for resource estimates.
    t_circuit = time.perf_counter()
    qp = _qubo_program(mu_s, sig_s, k, q, penalty)
    circuit_depth, cx_gates = _depth(
        qp, params, reps, e_range, mixer, k
    )
    circuit_build_seconds = time.perf_counter() - t_circuit

    out.update(
        circuit_depth=circuit_depth,
        cx_gates=cx_gates,
        shots=SHOTS,
        p_best_statevector=p_best_statevector,
        statevector_feasible_mass=statevector_feasible_mass,
    )

    # Execute on IBM hardware if requested; otherwise sample the simulator.
    hw_fail = None
    sampling_engine = engine
    shot_counts = None
    total = 0

    if engine == "qaoa_hw":
        t_sampling = time.perf_counter()
        try:
            progress.say("Connecting to IBM Quantum")
            counts, name, job_id, hw_depth = _run_hardware(
                qp, params, reps, e_range, n,
                mixer=mixer, k=k
            )

            measured = {}
            for bits, count in counts.items():
                z = sum(
                    int(bit) << i
                    for i, bit in enumerate(bits[::-1][:n])
                )
                measured[z] = measured.get(z, 0) + count

            shot_counts = measured
            total = sum(measured.values())

            if total <= 0:
                raise RuntimeError("IBM hardware returned no measurement shots.")

            out.update(
                label="QAOA (IBM quantum processor)",
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
            rng = np.random.default_rng(11)
            draws = rng.choice(len(probs), size=SHOTS, p=probs)
            unique, counts = np.unique(draws, return_counts=True)
            shot_counts = dict(zip(unique.tolist(), counts.tolist()))
            total = SHOTS
            out.update(
                label="QAOA (quantum circuit simulator)",
                backend="Exact state-vector simulation",
            )

        sampling_seconds = time.perf_counter() - t_sampling

    else:
        t_sampling = time.perf_counter()
        rng = np.random.default_rng(11)
        draws = rng.choice(len(probs), size=SHOTS, p=probs)
        unique, counts = np.unique(draws, return_counts=True)
        shot_counts = dict(zip(unique.tolist(), counts.tolist()))
        total = SHOTS
        sampling_seconds = time.perf_counter() - t_sampling

        out.update(
            label="QAOA (quantum circuit simulator)",
            backend="Exact state-vector simulation",
        )

    if total <= 0:
        raise RuntimeError("No measurement shots were available for postprocessing.")

    # Convert bitstrings into sampled portfolio rows.
    t_post = time.perf_counter()
    rows = []
    empirical_counts = {}

    for z, count in shot_counts.items():
        z = int(z)
        idx = tuple(int(i) for i in np.flatnonzero(X[z]))
        empirical_counts[idx] = empirical_counts.get(idx, 0) + int(count)

        ok = bool(feasible[z])
        rows.append({
            "idx": list(idx),
            "prob": int(count) / total,
            "feasible": ok,
            "fval": float(objective(X[z], mu_s, sig_s, k, q)) if ok else None,
            "rank": rank_of.get(idx),
        })

    feasible_rows = [row for row in rows if row["feasible"]]
    feasible_shots = sum(
        int(count)
        for z, count in shot_counts.items()
        if feasible[int(z)]
    )

    sampled_feasible_mass = float(feasible_shots / total)
    p_best_empirical = float(empirical_counts.get(best_key, 0) / total)

    out.update(
        feasible_mass=sampled_feasible_mass,
        qaoa_sample_found_feasible=bool(feasible_rows),
        p_best_empirical=p_best_empirical,
    )

    # Preserve the legacy p_best field and explicitly identify its meaning.
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
        # Explicitly identify the exact classical fallback.
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

    # This is the post-exact pipeline time, not pure quantum execution time.
    qaoa_pipeline_seconds = max(0.0, total_seconds - exact_seconds)

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

    # Lower objective is better. The percentage gap is descriptive.
    denom = abs(float(best_exact[0]))
    out["objective_gap_pct"] = (
        100.0 * (float(out["fval"]) - float(best_exact[0])) / denom
        if denom > 1e-12 else None
    )

    return out
