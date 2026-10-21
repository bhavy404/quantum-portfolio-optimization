"""Choose an IBM quantum computer that can take jobs right now.

The preferred machine (IBM_BACKEND_NAME in .env) is used when it is running. If it is in maintenance, paused or
offline, the least busy machine that is running and big enough is used instead, so one machine going down does not
stop the portal.
"""
from . import config


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
            return b, ""
        why = f"{preferred} is not accepting jobs right now"
    except Exception as ex:
        why = f"{preferred} could not be used ({type(ex).__name__})"
    try:
        b = service.least_busy(operational=True, simulator=False, min_num_qubits=max(min_qubits, 1))
    except Exception as ex:
        raise RuntimeError(f"{why}, and no other IBM computer is available right now.") from ex
    return b, f"{why}; used {b.name} instead"
