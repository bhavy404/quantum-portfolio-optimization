# 📘 QPortal: Complete Technical Architecture & Implementation Guide

> **Project Overview:** Secure Quantum Portfolio Optimization using QAOA with BB84 Quantum Key Distribution (QKD) on Real IBM Quantum Hardware.  
> **Target Audience:** Engineering Team & Collaborators  
> **Scope:** Pure technical documentation explaining the end-to-end architecture, physical-to-quantum mapping, codebase breakdown, and live hardware results.

---

## 📑 Table of Contents
1. [Executive Summary (In Plain English)](#1-executive-summary-in-plain-english)
2. [Physical Optical Rig vs. Real IBM Quantum QPU Implementation](#2-physical-optical-rig-vs-real-ibm-quantum-qpu-implementation)
3. [End-to-End System Flow & Architecture Diagram](#3-end-to-end-system-flow--architecture-diagram)
4. [Codebase Walkthrough: Where Which Part of Code Handles What](#4-codebase-walkthrough-where-which-part-of-code-handles-what)
5. [Live Hardware Verification Results (The Concrete Proof)](#5-live-hardware-verification-results-the-concrete-proof)
6. [How to Run and Test the System](#6-how-to-run-and-test-the-system)

---

## 1. Executive Summary (In Plain English)

QPortal bridges two core domains of quantum computing into a single unified application:

```
┌─────────────────────────────────────────┐       ┌─────────────────────────────────────────┐
│       1. THE BRAIN (OPTIMIZATION)       │       │        2. THE SHIELD (SECURITY)         │
│                                         │       │                                         │
│ • Problem: Markowitz Portfolio Theory   │       │ • Problem: Shor's algorithm will break  │
│   under cardinality & budget limits is  │  ──►  │   classical RSA/ECC encryption.         │
│   an NP-hard combinatorial problem.     │       │ • Solution: BB84 QKD generates a shared │
│ • Solution: QUBO formulation + QAOA on  │       │   quantum-safe 256-bit AES key to       │
│   superconducting quantum processors.   │       │   encrypt optimal trade allocations.    │
└─────────────────────────────────────────┘       └─────────────────────────────────────────┘
```

1. **The Optimization Engine (QAOA):**  
   Takes historical market data (e.g., Apple, Microsoft, Google, or Indian stocks like Reliance and TCS), calculates expected returns ($\mu$) and covariance risk ($\Sigma$), and converts the problem into an Ising Cost Hamiltonian. The Quantum Approximate Optimization Algorithm (QAOA) runs on quantum circuits to determine the exact asset allocation that maximizes the Sharpe ratio.
2. **The Security Engine (BB84 QKD):**  
   Transmitting sensitive financial portfolio weights over public classical channels introduces vulnerability to future quantum decryption. We implement the **BB84 Quantum Key Distribution protocol** directly on physical IBM Quantum QPUs. The protocol generates an unhackable symmetric key based on the fundamental laws of quantum physics (No-Cloning Theorem and wavefunction collapse), which is then used with **AES-256-GCM** to encrypt the portfolio payload.

---

## 2. Physical Optical Rig vs. Real IBM Quantum QPU Implementation

### 2.1 The Physical Setup & The Blocker
Our original plan included an educational optical hardware demonstrator:
* **Transmitter (Alice):** A 650nm HW-493 laser module pulsed by an Arduino UNO through polarizing filters ($0^\circ, 90^\circ, +45^\circ, -45^\circ$).
* **Receiver (Bob):** A BPW34 silicon PIN photodiode and an LM358 operational amplifier conditioning micro-volt photocurrent into 5V digital logic.
* **The Blocker:** In BB84, Bob must choose between two measurement bases at random without knowing Alice's choice. In optics, this requires a **50:50 non-polarizing beam splitter cube (~20×20mm)** to passively divide the beam into transmitted and reflected channels. Because that optical cube was pending procurement, true optical dual-basis sifting could not be completed physically.

### 2.2 The Quantum Synthesis (Mapping Optics to IBM QPUs)
In quantum physics, **a single photon's polarization angle is mathematically identical to the state of a single Qubit**. Instead of pulsing light across a table, we mapped every optical element to physical superconducting qubits running on IBM Quantum cloud hardware:

| Physical Optical Component | Optical Physical State | IBM Quantum QPU Implementation |
| :--- | :--- | :--- |
| **Laser Pulse** | 650nm single-photon carrier | **1 Physical Superconducting Qubit** ($q_i$) on the 156-qubit QPU |
| **0° / 90° Polarizers** | Rectilinear Basis ($+$): 0° = bit 0, 90° = bit 1 | Computational $Z$-basis: $|0\rangle$ (Identity) or $|1\rangle$ ($X$ gate) |
| **+45° / -45° Polarizers** | Diagonal Basis ($\times$): +45° = bit 0, -45° = bit 1 | Superposition $X$-basis: $|+\rangle$ ($H$ gate) or $|-\rangle$ ($X$ then $H$ gate) |
| **50:50 Beam Splitter Cube** | Random passive routing between 2 analyzer paths | Bob applies Hadamard basis rotation ($H$) prior to measurement if choosing basis $\times$ |
| **BPW34 Photodiode + LM358** | Photocurrent amplification & digital capture | Readout resonator measurement into classical bit register $c_i$ |
| **Eve (Eavesdropper)** | Unauthorized optical beam tap | Intermediate quantum measurement operator causing state collapse |

---

## 3. End-to-End System Flow & Architecture Diagram

```
+---------------------------------------------------------------------------------------------------+
|                                  ALICE (Quantum State Preparation)                                |
|  1. Generate random bits:  b_A in {0, 1}^N                                                        |
|  2. Generate random bases: a_A in {Z (+), X (x)}^N                                                |
|  3. Prepare Qubits:                                                                               |
|     - If Z basis: bit 0 -> |0>, bit 1 -> X|0> = |1>                                               |
|     - If X basis: bit 0 -> H|0> = |+>, bit 1 -> H(X|0>) = |->                                     |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
|                                     QUANTUM CHANNEL & ATTACK                                      |
|  • Qubits stream across the quantum channel.                                                      |
|  • [Optional Eve Interception]: Eve measures in random basis e_i.                                 |
|    - If e_i != a_A: Wavefunction collapses into Eve's basis.                                      |
|    - Induces ~25% error on sifted bits!                                                           |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
|                                      BOB (Physical IBM QPU)                                       |
|  1. Bob selects random measurement bases: a_B in {Z (+), X (x)}^N                                 |
|  2. If a_B == X: Apply Hadamard gate (qc.h) to rotate to computational basis.                     |
|  3. Measure qubits: qc.measure(q, c) on real hardware (ibm_fez, 156 Qubits) via SamplerV2.       |
|  4. Output bitstrings: b_B in {0, 1}^N                                                            |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
|                                      CLASSICAL SIFTING & QBER                                     |
|  1. Alice & Bob publicly announce bases a_A and a_B (over classical network).                     |
|  2. Discard all bits where a_A != a_B. Retain matching indices (~50% efficiency).                 |
|  3. Compare a 30% sample of sifted bits to calculate Quantum Bit Error Rate:                      |
|                        QBER = (mismatched sample bits) / (sample size)                            |
|  4. Security Gate:                                                                                |
|     - If QBER > 11%: EAVESDROPPING DETECTED -> ABORT KEY                                          |
|     - If QBER <= 11%: CHANNEL SECURE -> PROCEED                                                   |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
|                                PRIVACY AMPLIFICATION & KEY DERIVATION                             |
|  • Discard sample bits. Take remaining unrevealed sifted secret bits.                             |
|  • Hash bitstream through SHA-256 HKDF -> Produces a clean 256-bit Symmetric Key.                 |
+---------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+---------------------------------------------------------------------------------------------------+
|                                DYNAMIC PAYLOAD ENCRYPTION (AES-256-GCM)                           |
|  • QAOA Output: Optimal Assets (e.g. AMZN + META or RELIANCE + TCS), Sharpe = 3.18, Return = 110% |
|  • Encrypt JSON payload using derived 256-bit quantum key with random 96-bit nonce.               |
|  • Transmit ciphertext over wire -> Client decrypts and verifies 100% integrity.                 |
+---------------------------------------------------------------------------------------------------+
```

---

## 4. Codebase Walkthrough: Where Which Part of Code Handles What

The codebase is modular and cleanly separated into quantum cryptography, optimization, API services, and user interfaces:

```
Capstone_Project/
├── qkd_bb84.py               <-- Quantum Key Distribution Engine (IBM QPU + Crypto)
├── dashboard_api.py          <-- FastAPI REST Microservice (Backend)
├── dashboard.html            <-- Interactive Single-Page Web Dashboard (UI)
├── test_bb84_hardware.py     <-- Direct Standalone Hardware Verification Script
├── quantum/
│   └── portfolio_qaoa_ibm.py <-- Deep-dive QAOA hardware research script
├── .env                      <-- IBM Quantum Token & Hardware Config (Protected)
└── requirements.txt          <-- Dependencies (Qiskit 2.0+, FastAPI, Cryptography)
```

---

### Detailed File Responsibilities

### 1. `qkd_bb84.py` — The Quantum Cryptography Engine
* **`class BB84Session`**:
  * Initializes Alice's classical bits and bases using cryptographically secure random generators (`secrets.randbelow(2)`).
  * Generates Bob's random bases and Eve's random attack bases.
* **`build_circuit()`**:
  * Synthesizes the Qiskit quantum circuit (`QuantumCircuit(qr, cr)`).
  * Applies Pauli-$X$ and Hadamard ($H$) gates according to Alice's bases.
  * Models Eve's intercept-and-resend attack: if Eve measures in a conjugate basis, the state undergoes projective collapse into Eve's basis.
  * Applies Bob's basis rotations ($H$ gate if $X$ basis) and appends measurements.
* **`execute_on_ibm_qpu()`**:
  * Authenticates with `QiskitRuntimeService` using credentials from `.env`.
  * Pins the target operational physical backend (defaults to `ibm_fez` or `ibm_marrakesh`).
  * Compiles logical circuits to native Instruction Set Architecture (ISA) basis gates (`cz`, `rz`, `sx`, `x`) via `generate_preset_pass_manager(optimization_level=1, backend=backend)`.
  * Dispatches jobs through `SamplerV2(mode=backend)` and extracts measurement bitstrings accounting for Little-Endian qubit ordering (`reversed_shot[i]`).
* **`execute_local_simulation()`**:
  * Uses Qiskit `AerSimulator` for instant sub-millisecond local execution (useful for rapid UI testing).
* **`_sift_and_estimate_qber()`**:
  * Sifts bits where Alice and Bob used identical bases.
  * Computes the Quantum Bit Error Rate ($\text{QBER}$) over a test sample.
  * Enforces the theoretical $11\%$ threshold: if $\text{QBER} > 0.11$, triggers `ABORTED_EAVESDROPPING_DETECTED`; otherwise sets `SECURE_KEY_ESTABLISHED`.
  * Derives a 256-bit symmetric encryption key using SHA-256 HKDF.
* **`encrypt_portfolio_payload()` & `decrypt_portfolio_payload()`**:
  * Performs AES-256-GCM authenticated payload encryption/decryption using 12-byte random nonces.

---

### 2. `dashboard_api.py` — The REST API Microservice
Built with FastAPI, CORS middleware, and Pydantic validation:
* **`GET /` & `GET /dashboard`**: Serves `dashboard.html` directly in the browser.
* **`POST /optimize`**: Accepts asset tickers, date ranges, risk factor $q$, and budget $K$. Downloads financial data via `yfinance`, compiles the Markowitz QUBO, and solves it via QAOA.
* **`POST /qkd/generate-key`**: Triggers the BB84 protocol on real IBM hardware or simulator, returns the Alice/Bob basis table, sifting count, QBER, IBM Job ID, and derived key.
* **`POST /qkd/encrypt-portfolio`**: Takes a QAOA portfolio result and encrypts it with the active quantum key.
* **`POST /qkd/decrypt-payload`**: Decrypts the ciphertext and verifies authentication tags.
* **`GET /qkd/status`**: Returns real-time session metadata and QPU connection health.

---

### 3. `dashboard.html` — The Live Web Interface
A dark-mode glassmorphic single-page application:
* **Panel 1: BB84 QKD Engine:**
  * Controls for qubit count ($N = 16, 24, 32$), target QPU selection, and an **Eve Interception Toggle**.
  * Displays the step-by-step photon sifting table (green rows indicate matching bases), live QBER error meter, IBM Job ID, and derived 256-bit key.
* **Panel 2: QAOA Portfolio Optimization:**
  * Ticker inputs with one-click presets for **US Tech Giants** (`AAPL, MSFT, GOOGL, AMZN, META`) and **Indian Nifty Bluechips** (`RELIANCE.NS, TCS.NS, INFY.NS, HDFCBANK.NS, ICICIBC.NS`).
  * Risk tolerance slider $q$, budget $K$, financial metrics cards, and interactive Chart.js capital allocation bar charts.
* **Panel 3: Quantum Payload Security:**
  * Displays the original JSON portfolio allocation, the AES-256-GCM wire ciphertext with nonce, and verified client-side decryption.

---

### 4. `test_bb84_hardware.py` — Standalone Hardware Verification
A clean terminal script that your teammates can run anytime to test direct QPU connectivity, submit a 16-qubit BB84 job, verify basis sifting, and test roundtrip payload encryption without starting the web server.

---

## 5. Live Hardware Verification Results (The Concrete Proof)

The BB84 quantum circuit was submitted to and executed on a physical IBM Quantum processor. The hardware execution returned authentic quantum measurement bitstrings:

| Parameter | Measured Hardware Value |
| :--- | :--- |
| **Physical Quantum Processor** | **`ibm_fez`** (156 Physical Superconducting Qubits) |
| **IBM Quantum Job ID** | **`dags3ab9k43c73adqtk0`** |
| **Execution Primitive** | Qiskit Runtime V2 (`SamplerV2`) with ISA Pass Manager |
| **Transmitted Qubits ($N$)** | 16 Qubits |
| **Sifted Bits Retained** | **10 / 16** (62.5% Basis Matching Efficiency) |
| **Physical QBER** | **0.0%** (Well below the 11% eavesdropping threshold) |
| **Security Status** | **`SECURE_KEY_ESTABLISHED`** |
| **Derived 256-Bit Quantum Key** | `815f6415f5e574c05c7bb4086e7d00597e833c0ab943516aa324c73491659b3f` |
| **Payload Encryption Verification** | Successfully encrypted QAOA portfolio; decrypted with 100% data integrity |

### Eve Interception Verification:
When the Eve intercept-and-resend attack was simulated, Eve's measurements in incompatible bases collapsed the quantum wavefunctions, driving the QBER to **20% – 29%**, immediately triggering:
$$\text{Status: } \mathbf{ABORTED\_EAVESDROPPING\_DETECTED}$$
This experimentally proves that unauthorized interception cannot occur unnoticed under the laws of quantum mechanics.

---

## 6. How to Run and Test the System

### Step 1: Start the Backend Service
From the project root directory in your terminal:
```bash
.venv/bin/python dashboard_api.py
```
*(The server will initialize on `http://127.0.0.1:8000` with IBM Quantum credentials loaded from `.env`)*.

### Step 2: Open the Dashboard
Open your browser and navigate to:
* **Interactive Dashboard:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Interactive Swagger API Docs:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Step 3: Run Direct Terminal Verification (Optional)
To verify hardware execution directly from the command line without the web browser:
```bash
.venv/bin/python test_bb84_hardware.py
```

---
*Document prepared for the Capstone Project Engineering Team.*
