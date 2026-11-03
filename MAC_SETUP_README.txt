QPortal Mac setup

1. Extract this ZIP into Downloads.
2. Open Terminal and run:
   cd ~/Downloads/quantum-portfolio-optimization-MAC-fixed
   chmod +x RUN_QPORTAL_MAC.command
   ./RUN_QPORTAL_MAC.command
3. Open http://127.0.0.1:8000

What the script does (nothing is installed outside this folder):
- Needs Python 3.12 (install with: brew install python@3.12).
- Deletes and recreates ONLY this project's own .venv folder, then installs the pinned
  packages from portal/requirements.txt into it and runs "pip check".
- Checks the NumPy aliases and imports, then starts the FastAPI server on 127.0.0.1:8000.

Downloads the script makes (so you know what touches the internet):
- Python packages from PyPI (pip) - the normal install step.
- ONLY IF the standard venv cannot provide pip (a broken ensurepip), it downloads pip's official
  installer, get-pip.py, from https://bootstrap.pypa.io and runs it inside .venv. This is a common
  workaround, but it does run a downloaded script, so read it first if that bothers you:
  https://bootstrap.pypa.io/get-pip.py . On a healthy Python 3.12 this fallback is not used.
- The app itself fetches market prices (Yahoo Finance), the NSE stock list and mutual-fund data at runtime.

Pinned versions: numpy 2.5.4, pandas 3.1.0, scipy 1.18.1, qiskit 2.4.1, qiskit-aer 0.18.2,
qiskit-ibm-runtime 0.51.0, qiskit-optimization 0.7.0, qiskit-algorithms 0.4.0. All exist on PyPI and
install together on Python 3.12 with "pip check" passing. The root requirements.txt reuses the same set
(plus matplotlib and reportlab, which only generate_pdf_report.py needs).

Sign-up creates the account only. After "Account created" you log in on the same screen.
The eavesdropper demo is its own page (link under the sign-in form, or Security > Eavesdropping test).

IBM Quantum credentials are optional. Never paste your IBM token into public chats.
