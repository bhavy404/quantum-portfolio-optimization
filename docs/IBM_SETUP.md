# Run the QPortal dashboard on real IBM quantum hardware

You need an IBM Quantum account with access to a quantum computer (the free Open plan works, our mentor's access works too). Without it the dashboard still runs fully on the built-in simulator. Only the "IBM quantum processor" option and the hardware BB84 key need these steps.

## 1. Get your IBM details
1. Sign in at https://quantum.cloud.ibm.com
2. Copy your **API key** (top right, "API key"). Treat it like a password.
3. Open "Instances" and copy the **instance name or CRN** if you have more than one (optional).

## 2. Set up the project (first time only)
```
git clone https://github.com/bhavy404/quantum-portfolio-optimization.git
cd quantum-portfolio-optimization
git checkout portal-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r portal/requirements.txt
```

## 3. Put your details in a private `.env` file
```
cp .env.template .env
```
Open `.env` in any editor and fill in:
```
IBM_QUANTUM_TOKEN=your_api_key_here
IBM_QUANTUM_INSTANCE=your_instance_or_crn_here   # leave empty if you only have one
IBM_BACKEND_NAME=ibm_fez                          # or any machine you can see; the portal switches by itself if it is in maintenance or has a long queue
```
Rules:
- `.env` is private and already ignored by git. **Never commit it, never paste the key in a chat or screenshot.**
- Every person uses their own key. Do not share one key.
- Keep the file in the repo folder (the one that contains `portal/`), not in a parent folder.

## 4. Check the connection (about 1 to 3 minutes)
```
python check_ibm.py
```
It prints the machine it picked, the job state while it waits, and the measured counts. If it ends with the counts, IBM works.

## 5. Start the dashboard
```
uvicorn portal.main:app --port 8000
```
Open http://localhost:8000. In the analysis settings choose **IBM quantum processor** as the engine. The waiting page shows live stages such as "Sent the circuit to ibm_fez".

## What to expect
- Free-plan quota is small (about 10 minutes of quantum time per month). One analysis uses a few seconds. Do not spam runs.
- If IBM takes more than 240 seconds the portal cancels and finishes on the simulator, and tells you so on the result. Change the limit with `IBM_WAIT_SECONDS` in `.env`.
- The "How this result is protected" box shows the real machine, job ID and the BB84 error bound.
- Stop the server with Ctrl+C. Restart it after editing `.env`.

## Troubleshooting
| Problem | Fix |
|---|---|
| IBM option greyed out or "not available on this server" | The token is missing or `.env` is in the wrong folder. Restart the server after fixing. |
| `check_ibm.py` says invalid token or instance | Copy the API key again. Clear `IBM_QUANTUM_INSTANCE` if unsure. |
| Machine in maintenance or huge queue | Change `IBM_BACKEND_NAME` to another machine, or just leave it. The portal picks a running one. |
| Run finished "on the simulator" | IBM was too slow or failed. Read the note on the result page. Try again later. |
| `zsh: parse error near ')'` | You pasted the prompt, for example `(.venv)`, with the command. Type only the command. |
