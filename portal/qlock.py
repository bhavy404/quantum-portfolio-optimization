"""Run all Qiskit work on ONE dedicated thread.

Qiskit's compiled core can crash the whole server (segmentation fault) when circuits are built or simulated from
several threads. The web server uses a pool of threads and the key bank tops itself up in the background, so
every piece of Qiskit work is sent to this single worker and waits its turn. Hardware QAOA jobs wait on IBM
outside the worker; hardware BB84 key making runs on it, so other quantum work queues behind it meanwhile.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qiskit")
_LOCAL = threading.local()


def run(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) on the Qiskit worker thread and return its result (or raise its error)."""
    if getattr(_LOCAL, "inside", False):  # already on the worker: just call
        return fn(*args, **kwargs)

    def wrapped():
        _LOCAL.inside = True
        try:
            return fn(*args, **kwargs)
        finally:
            _LOCAL.inside = False

    return _EXEC.submit(wrapped).result()
