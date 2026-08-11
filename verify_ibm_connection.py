"""
verify_ibm_connection.py
========================
Milestone 1 — Quick credential and backend connectivity check.

This lighter script only tests:
  1. .env loading
  2. Credential detection (no values printed)
  3. IBM Quantum authentication
  4. Backend availability

It does NOT submit a job to hardware.
Use ibm_connectivity_test.py for the full Bell-state hardware test.

Usage:
  .venv/bin/python verify_ibm_connection.py
"""

import os
import sys
from typing import Optional
from dotenv import load_dotenv


def load_and_check_env() -> tuple:
    """Load .env and report which variables are present."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    print(f"[1/4] .env file: {'FOUND' if os.path.exists(env_path) else 'NOT FOUND (using system env)'}")
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)

    token = os.environ.get("IBM_QUANTUM_TOKEN", "").strip()
    instance = os.environ.get("IBM_QUANTUM_INSTANCE", "").strip()
    backend_name = os.environ.get("IBM_BACKEND_NAME", "").strip()

    print()
    print("[2/4] Credential Detection:")
    print(f"  IBM_QUANTUM_TOKEN    : {'✅ PRESENT' if token else '❌ MISSING'}")
    print(f"  IBM_QUANTUM_INSTANCE : {'✅ PRESENT' if instance else '⚠️  NOT SET (may use default)'}")
    print(f"  IBM_BACKEND_NAME     : {backend_name if backend_name else '⚠️  NOT SET (will auto-select)'}")

    if not token:
        print()
        print("  ❌ FATAL: IBM_QUANTUM_TOKEN is required.")
        print("     Set it in your .env file:  IBM_QUANTUM_TOKEN=<your-api-key>")
        sys.exit(1)

    return token, instance or None, backend_name or None


def check_authentication(token: str, instance: Optional[str]):
    """Test IBM Quantum authentication without submitting a job."""
    print()
    print("[3/4] IBM Quantum Authentication:")
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
        kwargs = dict(channel="ibm_quantum_platform", token=token)
        if instance:
            kwargs["instance"] = instance
        service = QiskitRuntimeService(**kwargs)
        print("  ✅ Authentication: SUCCESS")
        return service
    except Exception as exc:
        print(f"  ❌ Authentication FAILED: {exc}")
        print()
        print("  Common causes:")
        print("   - Invalid or expired IBM Quantum API token")
        print("   - Wrong IBM_QUANTUM_INSTANCE (CRN) value")
        print("   - Network/firewall/SSL issue (Python 3.9 + LibreSSL incompatible)")
        print("     → Fix: Use Python 3.10+ built with OpenSSL (brew install python@3.11)")
        sys.exit(1)


def check_backend(service, backend_name: Optional[str]):
    """Verify the configured backend is accessible."""
    print()
    print("[4/4] Backend Check:")
    try:
        if backend_name:
            backend = service.backend(backend_name)
            status = backend.status()
            print(f"  ✅ Backend '{backend.name}' reachable")
            print(f"     Status:     {status.status_msg}")
            print(f"     Qubits:     {backend.num_qubits}")
            print(f"     Operational:{status.operational}")
        else:
            backend = service.least_busy(simulator=False, operational=True)
            status = backend.status()
            print(f"  ✅ Least busy backend: '{backend.name}'")
            print(f"     Status:     {status.status_msg}")
            print(f"     Qubits:     {backend.num_qubits}")
            print(f"     Tip: set IBM_BACKEND_NAME={backend.name} in .env to pin this backend")
        return backend
    except Exception as exc:
        print(f"  ❌ Backend check FAILED: {exc}")
        sys.exit(1)


def main():
    print("=" * 60)
    print("  IBM Quantum Connection Verifier — Milestone 1")
    print("=" * 60)

    token, instance, backend_name = load_and_check_env()
    service = check_authentication(token, instance)
    backend = check_backend(service, backend_name)

    print()
    print("=" * 60)
    print("  ✅ All checks passed!")
    print(f"  Ready to run Bell-state test on: {backend.name}")
    print("  Next: run  .venv/bin/python ibm_connectivity_test.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
