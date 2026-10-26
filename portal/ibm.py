"""Choose an IBM quantum computer that can take jobs right now.

The preferred machine (IBM_BACKEND_NAME in .env) is used when it is running. If it is in maintenance, paused or
offline, the least busy machine that is running and big enough is used instead, so one machine going down does not
stop the portal.
"""
import time

from . import config, progress


def _ready(b):
    try:
        st = b.status()
        return bool(st.operational) and not getattr(st, "status_msg", "") == "internal"
    except Exception:
        return False


def connect():
    from qiskit_ibm_runtime import QiskitRuntimeService

    if not config.IBM_TOKEN:
        raise RuntimeError("IBM Quantum is not configured on this server.")
    kwargs = {"channel": "ibm_quantum_platform", "token": config.IBM_TOKEN}
    if config.IBM_INSTANCE:
        kwargs["instance"] = config.IBM_INSTANCE
    return QiskitRuntimeService(**kwargs)


def pick_backend(service, min_qubits=1):
    """Return (backend, note). note is "" when the preferred machine was used, else a plain-language reason."""
    preferred = config.IBM_BACKEND
    try:
        b = service.backend(preferred)
        if _ready(b) and b.num_qubits >= min_qubits:
            queue = _pending(b)
            if queue is not None and queue > 25:  # a long line: look for a much shorter one
                try:
                    alt = service.least_busy(operational=True, simulator=False, min_num_qubits=max(min_qubits, 1))
                    aq = _pending(alt)
                    if alt.name != b.name and aq is not None and aq + 15 < queue:
                        return alt, f"{preferred} had {queue} jobs waiting; used {alt.name} ({aq} waiting)"
                except Exception:
                    pass
            return b, ""
        why = f"{preferred} is not accepting jobs right now"
    except Exception as ex:
        why = f"{preferred} could not be used ({type(ex).__name__})"
    try:
        b = service.least_busy(operational=True, simulator=False, min_num_qubits=max(min_qubits, 1))
    except Exception as ex:
        raise RuntimeError(f"{why}, and no other IBM computer is available right now.") from ex
    return b, f"{why}; used {b.name} instead"


def _pending(b):
    try:
        return int(b.status().pending_jobs)
    except Exception:
        return None


class IBMTimeout(RuntimeError):
    pass


def wait(job, what="IBM job"):
    """Wait for an IBM job, telling the page its state, and give up after IBM_WAIT_SECONDS."""
    t0 = time.time()
    limit = config.IBM_WAIT_SECONDS
    while True:
        try:
            st = job.status()
            st = getattr(st, "name", str(st))
        except Exception:
            st = "QUEUED"
        if st in ("DONE", "COMPLETED"):
            return job.result()
        if st in ("ERROR", "CANCELLED", "FAILED"):
            raise RuntimeError(f"{what} ended with status {st}.")
        waited = int(time.time() - t0)
        where = "running on the quantum processor" if st == "RUNNING" else "waiting in IBM's queue"
        progress.say(f"{what}: {where} ({waited}s of up to {limit}s). Job {job.job_id()}")
        if waited >= limit:
            try:
                job.cancel()
            except Exception:
                pass
            raise IBMTimeout(f"IBM job {job.job_id()} did not finish within {limit} seconds")
        time.sleep(3)
