"""What a running analysis is doing right now, in plain words, so the waiting page can show it."""
import threading

_LOCAL = threading.local()
_STAGE = {}
_LOCK = threading.Lock()


def begin(run_id):
    _LOCAL.run_id = run_id
    say("Starting")


def say(text):
    rid = getattr(_LOCAL, "run_id", None)
    if rid is not None:
        with _LOCK:
            _STAGE[rid] = text


def get(run_id):
    with _LOCK:
        return _STAGE.get(run_id)


def end(run_id):
    with _LOCK:
        _STAGE.pop(run_id, None)
    _LOCAL.run_id = None
