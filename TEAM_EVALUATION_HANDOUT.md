# 🎓 Capstone Project Team Handout & Evaluation Defense Guide

> **Project Title:** Quantum Portfolio Optimization using QAOA on IBM Quantum Hardware  
> **Target Audience:** Team members preparing for capstone evaluation / viva defense  
> **Key Goal:** Understand the entire pipeline from scratch, speak confidently with zero quantum jargon confusion, and clearly justify why this project required substantial research and engineering time.

---

## 📌 Section 1: The "Explain Like I'm 5" Summary

### What Problem Are We Solving?
Imagine you have money to invest. You are looking at 5 major tech stocks: **Apple (AAPL), Microsoft (MSFT), Google (GOOGL), Amazon (AMZN), and Meta (META)**.

Your budget only allows you to buy **exactly 2 stocks**.

* If you pick only high-profit stocks, you take on massive risk of losing money.
* If you pick only safe stocks, you make very low returns.
* **The Goal:** Find the **sweet spot**—the pair of 2 stocks that gives you the **highest expected return** with the **lowest risk (variance)**.

### How Does a Normal Computer vs. a Quantum Computer Do It?
* **Classical Computer:** Checks combinations one by one or uses classical mathematical approximations. For 5 stocks ($10$ combinations), classical computers are fast. But for $500$ stocks with real-world trading constraints (taxes, round lots, sector limits), the number of combinations exceeds the number of atoms in the known universe ($2^{500}$).
* **Quantum Computer:** Instead of testing one combination at a time, we represent each stock as a **qubit** (quantum bit). Through **superposition** and **quantum interference**, the quantum circuit explores all combinations simultaneously and amplifies the probability of measuring the optimal portfolio.

---

## 🔄 Section 2: The 5 Big Steps of Our Project

```
[ Step 1: Financial Data ] ──► [ Step 2: Math / QUBO ] ──► [ Step 3: QAOA Quantum Circuit ]
                                                                     │
                                        ┌────────────────────────────┴───────────────────────────┐
                                        ▼                                                        ▼
                            [ Step 4A: Local Simulator ]                              [ Step 4B: Real IBM Hardware ]
                                        │                                                        │
                                        ▼                                                        ▼
                            [ Step 5: FastAPI Web API ]                              [ Verification & Benchmark ]
```

### Step 1: Fetching Real-World Market Data
* We download 1 full year of daily stock prices (2023–2024) from Yahoo Finance for AAPL, MSFT, GOOGL, AMZN, and META.
* We split the data into a **70% Training set** (to find the optimal portfolio) and a **30% Testing set** (to verify if our portfolio actually performed well in the future).
* We calculate two key metrics:
  1. **Expected Returns ($\mu$):** How much profit each stock averaged annually.
  2. **Covariance Matrix ($\Sigma$):** How stocks fluctuate together (if both drop at the same time, risk is high).

### Step 2: Turning Finance into Math (QUBO Formulation)
Quantum computers do not understand financial terms like "stocks" or "dividends". They understand **energy minimization**.
* We define a binary variable for each stock: $x_i = 1$ (stock selected) or $x_i = 0$ (stock not selected).
* We write the **Markowitz Cost Function**:
  $$\text{Cost} = (1 - q) \cdot \text{Risk} - q \cdot \text{Return}$$
  *(where $q = 0.5$ is the risk-tolerance factor)*.
* **The Budget Constraint:** We must pick *exactly* 2 stocks ($\sum x_i = 2$). Since quantum circuits prefer unconstrained equations, we add a **penalty term**:
  $$\text{Penalty} = P \cdot (x_0 + x_1 + x_2 + x_3 + x_4 - 2)^2$$
  If the circuit tries to pick 1 or 3 stocks, the penalty creates huge positive energy, forcing the system back to feasible 2-stock portfolios.
* This combined mathematical formula is called a **QUBO (Quadratic Unconstrained Binary Optimization)**.

### Step 3: Translating QUBO into Quantum Gates (QAOA)
* We map binary variables $x_i \in \{0, 1\}$ into quantum spin operators $Z_i \in \{+1, -1\}$. This creates our **Cost Hamiltonian ($H_C$)**.
* We use **QAOA (Quantum Approximate Optimization Algorithm)**:
  1. **Initialization:** Put all 5 qubits into an equal superposition state ($|+\rangle^{\otimes 5}$).
  2. **Cost Layer ($e^{-i \gamma H_C}$):** Applies quantum phase shifts proportional to financial costs using $RZ$ and $CX$ gates.
  3. **Mixer Layer ($e^{-i \beta H_M}$):** Applies $RX(2\beta)$ rotations to allow quantum tunneling between different portfolio configurations.
  4. **Measurement:** Measures the qubits thousands of times (shots) to produce a probability distribution of bitstrings (e.g., `00011` = AMZN + META).

### Step 4: Running on Real IBM Quantum Hardware (127-Qubit QPUs)
* We connected our code to IBM Quantum cloud platforms (`ibm_marrakesh` and `ibm_kyoto`).
* Because physical quantum processors have noisy connections and limited gate sets, we **transpiled** our circuit to the hardware's native **Instruction Set Architecture (ISA)** using Qiskit 1.0 Preset Pass Managers.
* We benchmarked real hardware output against exact classical mathematical calculations.

### Step 5: Building the Production Web API
* We wrapped the entire pipeline in a **FastAPI backend** (`dashboard_api.py`).
* Web dashboards can make POST requests specifying custom tickers, date ranges, and budget constraints, receiving instant quantum portfolio calculations, train/test returns, and Sharpe ratios.

---

## ⏳ Section 3: Why Did This Project Take So Much Time? (Justifying Workload to Teachers)

If the evaluators ask: *"Why did this project take an entire semester?"* — here are the 5 major technical challenges your team solved:

| Challenge Area | What Made It Difficult & Time-Consuming | What We Implemented |
| :--- | :--- | :--- |
| **1. Complex Mathematical Mapping** | Converting Markowitz Mean-Variance equations with equality constraints into an unconstrained Ising spin Hamiltonian requires multi-variable penalty algebra and Pauli operator expansions. | Automated converter via Qiskit Optimization and custom penalty formulations. |
| **2. Qiskit 1.0+ Architecture Migration** | IBM released Qiskit 1.0 with breaking architectural changes. Legacy tutorials use retired classes (`qiskit.execute`, `AerSimulator`, V1 Samplers). Modern IBM QPUs strictly require `Qiskit Runtime V2`, PUB (Primitive Unified Blocs), and pre-transpiled ISA circuits. | Complete migration to Qiskit 1.0 Preset Pass Managers and `SamplerV2`. |
| **3. Hardware Noise & Calibration on Real QPUs** | Real QPUs suffer from quantum decoherence ($T_1/T_2$ relaxation times), gate infidelities, and readout errors. Running algorithms naively yields noisy, invalid states. | Shot allocation strategies (256 shots for search, 1024+ for final evaluation), bitstring endianness verification, and feasible probability mass filtering. |
| **4. Cloud Queue & Latency Management** | IBM Quantum hardware has shared global queues with wait times ranging from 10 minutes to several hours per job. | Built a dual-track architecture: local `StatevectorSampler` for rapid testing/API serving, and dedicated asynchronous scripts for real QPU experiments. |
| **5. Production API & Software Engineering** | Integrating quantum libraries into a real-time web server required solving CORS issues, asynchronous data fetching, and strict Pydantic input validation to prevent invalid QUBO matrix dimensions. | Production-ready FastAPI service with batch `yfinance` data extraction, credential isolation via `.env`, and health checks. |

---

## 👥 Section 4: Team Role Breakdown (Who Explains What)

Assign these areas to team members so everyone demonstrates deep mastery:

### 👤 Speaker 1: Problem Statement & Financial Math (QUBO)
* **What to explain:**
  * Why portfolio optimization is NP-hard for large asset universes.
  * The Markowitz trade-off between risk ($\Sigma$) and return ($\mu$).
  * How the equality constraint ($\sum x_i = K$) is turned into a penalty term to form a QUBO.
* **Key phrase to use:** *"We translated a quadratic constrained financial problem into an Unconstrained Binary Optimization format suitable for quantum Hamiltonian mapping."*

### 👤 Speaker 2: Quantum Algorithm & QAOA Mechanics
* **What to explain:**
  * How 5 stocks map to 5 qubits in a 32-dimensional Hilbert space ($2^5$).
  * How QAOA uses alternating Cost ($e^{-i \gamma H_C}$) and Mixer ($e^{-i \beta H_M}$) unitaries.
  * How quantum interference boosts the probability of measuring the lowest-energy state.
* **Key phrase to use:** *"QAOA acts as a parameterized quantum circuit where classical optimizers like COBYLA adjust the angles $\gamma$ and $\beta$ to steer the quantum state toward optimal portfolios."*

### 👤 Speaker 3: Real IBM Quantum Hardware & Transpilation
* **What to explain:**
  * The Bell-state hardware verification test ([`ibm_connectivity_test.py`](file:///Users/priyanshuparashar/Documents/Capstone_Project/ibm_connectivity_test.py)).
  * Why transpilation to the QPU's Instruction Set Architecture (ISA) is required.
  * How we accounted for Little-Endian qubit ordering and hardware noise.
* **Key phrase to use:** *"We transpiled our logical circuits into native physical gates for IBM 127-qubit QPUs, verifying our results against exact classical baselines."*

### 👤 Speaker 4: Software Engineering, FastAPI & Results
* **What to explain:**
  * The FastAPI service architecture ([`dashboard_api.py`](file:///Users/priyanshuparashar/Documents/Capstone_Project/dashboard_api.py)).
  * Input validations (Pydantic), CORS middleware, and credential security.
  * The final financial results: Selected `['AMZN', 'META']` with a Train Sharpe Ratio of **3.18** and Test Return of **37.09%**.
* **Key phrase to use:** *"We built a production REST API that connects live financial data streams to our quantum optimization engine with zero credential exposure."*

---

## ❓ Section 5: Top 6 Evaluator Questions & Exact Answers

### Q1: "Did you achieve Quantum Advantage?"
> **Winning Answer:**  
> *"No, and that was not our claim. For a 5-stock portfolio ($10$ combinations), classical computers solve this in milliseconds. Current Noisy Intermediate-Scale Quantum (NISQ) devices have noise and gate errors. Our project proves the **feasibility and implementation pipeline** of QAOA on real hardware so that as fault-tolerant quantum hardware scales to hundreds of qubits, this exact pipeline can solve portfolios that are mathematically intractable classically."*

### Q2: "What does QAOA actually do inside the circuit?"
> **Winning Answer:**  
> *"QAOA is a hybrid quantum-classical algorithm. The quantum computer creates an equal superposition of all possible portfolios. It applies phase rotations based on the cost function (Cost Hamiltonian) and mixing rotations (Mixer Hamiltonian). Through quantum interference, bad portfolios destructively interfere and cancel out, while optimal portfolios constructively interfere. A classical optimizer iteratively updates the rotation angles $(\gamma, \beta)$ to maximize the probability of measuring the optimal solution."*

### Q3: "Why did you need both a local simulator and real IBM hardware?"
> **Winning Answer:**  
> *"They serve two different purposes. The real IBM hardware (`portfolio_qaoa_ibm.py`) allowed us to test physical QPU execution, transpilation to hardware topology, and noise behavior. The local simulator (`StatevectorSampler`) runs in milliseconds without cloud queue latency, making it the ideal engine to power our real-time FastAPI dashboard backend."*

### Q4: "How did you handle the constraint of selecting exactly 2 stocks?"
> **Winning Answer:**  
> *"Quantum Hamiltonians cannot handle hard Boolean constraints natively. We converted the constraint $\sum x_i = 2$ into a quadratic penalty term $P \cdot (\sum x_i - 2)^2$ and added it to the objective function. Any state with more or fewer than 2 stocks receives an energy penalty, ensuring the lowest energy state corresponds to a valid 2-stock portfolio."*

### Q5: "What is the Sharpe ratio and why did you calculate it?"
> **Winning Answer:**  
> *"The Sharpe Ratio measures risk-adjusted return ($\frac{\text{Return} - R_f}{\text{Volatility}}$). A high return with huge volatility is dangerous. Our QAOA selected Amazon and Meta, which produced an annualized train return of 110% at a variance of 0.119, giving a high Sharpe ratio of 3.18, proving the algorithm successfully balanced both objectives."*

### Q6: "How did you protect your API keys and IBM credentials?"
> **Winning Answer:**  
> *"We implemented strict environment isolation using `.env` files that are gitignored. In our codebase, all token variables are masked (`_mask()`) during logging and authentication, ensuring zero plain-text credential leakage in server logs, source control, or API responses."*

---

## 📖 Section 6: Quantum Jargon Cheat Sheet

* **Qubit:** The basic unit of quantum information. Unlike a classical bit (0 or 1), it can exist in a superposition of both states.
* **Superposition:** A quantum phenomenon where a qubit exists in a linear combination of $|0\rangle$ and $|1\rangle$ simultaneously.
* **Entanglement:** A quantum linkage where the state of one qubit instantaneously determines the state of another (e.g. Bell state).
* **Hamiltonian ($H$):** An operator representing the total energy of a physical quantum system. In optimization, ground state (lowest energy) = optimal solution.
* **QUBO:** Quadratic Unconstrained Binary Optimization—a mathematical formulation suitable for quantum spin systems.
* **Transpilation / ISA:** Compiling high-level logical quantum gates (like H and CX) into the specific physical hardware gate set and connectivity layout of an IBM QPU.
* **Shots:** The number of times a quantum circuit is executed and measured to build a reliable probability histogram (e.g., 1024 shots).
* **COBYLA:** Constrained Optimization BY Linear Approximation—the classical gradient-free optimizer that tunes QAOA parameter angles.
