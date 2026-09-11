"""
test_bb84_hardware.py
=====================
Verification script to submit a live BB84 key distribution job to a real IBM Quantum QPU.
"""

import sys
from qkd_bb84 import BB84Session, encrypt_portfolio_payload, decrypt_portfolio_payload

print("=" * 66)
print("  QPortal — Live BB84 on Real IBM Quantum QPU")
print("=" * 66)

# 1. Initialize a 16-qubit BB84 session on real IBM QPU (ibm_fez has 0 queue, or auto-selected)
session = BB84Session(num_qubits=16, enable_eve=False, backend_name="ibm_fez")
print(f"[*] Initialized 16-qubit BB84 protocol")
print(f"[*] Alice's random bits : {session.alice_bits}")
print(f"[*] Alice's bases       : {session.alice_bases}")
print(f"[*] Bob's bases         : {session.bob_bases}")

print(f"\n[*] Submitting BB84 quantum circuit to real IBM QPU ({session.backend_name})...")
try:
    summary = session.execute_on_ibm_qpu()
except Exception as e:
    print(f"[!] Hardware execution error: {e}")
    sys.exit(1)

print("\n" + "=" * 66)
print("  HARDWARE EXECUTION RESULTS")
print("=" * 66)
print(f"  Target QPU           : {summary['qpu_name']}")
print(f"  IBM Job ID           : {summary['job_id']}")
print(f"  Security Status      : {summary['security_status']}")
print(f"  Sifted Bits Count    : {summary['sifted_bits_count']} / {summary['num_qubits']} ({summary['sifted_efficiency']})")
print(f"  Physical QBER        : {summary['qber_percentage']}")
print(f"  Derived 256-bit Key  : {summary['derived_key_hex']}")
print("=" * 66)

# 2. Test encryption of sample portfolio optimization payload
if session.derived_key_bytes:
    sample_portfolio = {
        "status": "success",
        "selected_stocks": ["AMZN", "META"],
        "annualized_return": 1.103,
        "sharpe_ratio": 3.18,
        "budget": 2,
    }
    encrypted = encrypt_portfolio_payload(sample_portfolio, session.derived_key_bytes)
    print("\n[*] AES-256-GCM Payload Encryption using Real QPU Quantum Key:")
    print(f"  Nonce (hex)      : {encrypted['nonce_hex']}")
    print(f"  Ciphertext (hex) : {encrypted['ciphertext_hex']}")

    decrypted = decrypt_portfolio_payload(encrypted, session.derived_key_bytes)
    print("\n[*] Client-Side Decryption Verification:")
    print(f"  Decrypted payload: {decrypted}")
    assert decrypted["selected_stocks"] == ["AMZN", "META"]
    print("  ✅ Decryption matched original payload perfectly!")
