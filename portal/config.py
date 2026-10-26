"""Runtime configuration for the QPortal web app (all values come from environment variables)."""
import os

try:  # read settings (IBM token etc.) from the repository's .env file
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("QPORTAL_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("QPORTAL_DB", DATA_DIR / "qportal.db"))

# True only for automated tests: generates fake prices so the app can run without internet.
# The UI shows a red banner whenever this is on. Never enable it for real users.
SYNTHETIC = os.getenv("QPORTAL_SYNTHETIC", "0") == "1"

# Where BB84 key-generation circuits run: "simulator" (default) or "hardware" (IBM QPU).
KEY_TARGET = os.getenv("QPORTAL_KEY_TARGET", "simulator").lower()
IBM_TOKEN = os.getenv("IBM_QUANTUM_TOKEN", "").strip()
IBM_BACKEND = os.getenv("IBM_BACKEND_NAME", "ibm_fez").strip() or "ibm_fez"
IBM_INSTANCE = os.getenv("IBM_QUANTUM_INSTANCE", "").strip()
# How long to wait for one IBM job (queue + run) before giving up and using the simulator instead.
IBM_WAIT_SECONDS = int(os.getenv("IBM_WAIT_SECONDS", "240"))

HISTORY_YEARS = int(os.getenv("QPORTAL_HISTORY_YEARS", "3"))
PRICE_MAX_AGE_HOURS = int(os.getenv("QPORTAL_PRICE_MAX_AGE_HOURS", "4"))
MAX_SHORTLIST = 12   # statevector QAOA stays interactive up to ~12 qubits
MIN_SHORTLIST = 3
BANK_TARGET_KEYS = int(os.getenv("QPORTAL_BANK_KEYS", "3"))
KEY_MIN_SECRET_BITS = 256
ALLOWED_ORIGINS = [o for o in os.getenv("QPORTAL_ORIGINS", "").split(",") if o]


def _load_secret() -> bytes:
    """Server secret used to sign login tokens and to harden keys. Set QPORTAL_SECRET in production."""
    env = os.getenv("QPORTAL_SECRET")
    if env:
        return env.encode()
    path = DATA_DIR / "server_secret.key"
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    value = secrets.token_bytes(32)
    path.write_text(value.hex())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


SERVER_SECRET = _load_secret()
