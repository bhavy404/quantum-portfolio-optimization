# BB84 on the login page — live browser/server mode

## Mode switch and compatibility

`QPORTAL_LOGIN_MODE=classic|live` selects the login flow. The default is **classic**; leaving the variable unset preserves the existing QPortal login. Set `QPORTAL_LOGIN_MODE=live` to enable the browser/server exchange. The old `/api/auth/login` and `/api/auth/register` endpoints remain in place and branch by mode. IBM hardware is not called by live login. The separate saved-result key bank remains configured by `QPORTAL_BANK_TARGET` / the existing key-bank settings.

## Honest protocol description

Qubits are simulated in this demo. There is no quantum channel to the browser and no claim that the browser or a participant has a physical quantum device. Alice (the portal) creates the random qubits and bases and keeps them, together with the simulated channel, on the server. Bob (the browser) chooses his measurement bases first and the channel returns his outcomes, so the qubit states, Alice's bases and Alice's bits never reach the wire before the public discussion. Eve (the server-side intercept-and-resend step) can intercept and resend the states. Error correction is idealised: both sides use Bob's measured bits for the key. The BB84 sifting, sample test, eavesdropper detection, key derivation and HMAC proof are implemented cryptographically. The derived key is never sent to the server or stored in browser storage.

## Live flow

1. On opening the login/sign-up page, the browser calls `POST /api/bb84/start` before the user submits credentials. Alice creates 1,600 random bits and bases and keeps them on the server. The response carries only a one-time session ID and the settings, and no states, bases or bits. The page holds the exchange result and eventual key in memory only.
2. Bob chooses 1,600 bases with `crypto.getRandomValues` and sends them to `POST /api/bb84/sift` before he learns anything. The channel returns Bob's measured bits (his own outcomes; a mismatched basis gives a random bit). The server also returns Alice's bases and a random 30% sample of the kept positions. The first 20 positions are shown in the demo table and are never used for the key.
3. Bob sends only the measured bits at the disclosed sample positions to `POST /api/bb84/sample`. Alice computes the QBER and the one-sided 95% Clopper–Pearson upper bound using `keybank._upper95`. The secure verdict requires an upper bound at or below 11%.
4. If the verdict is secure, Bob derives a 256-bit key with Web Crypto HKDF-SHA256. Alice independently derives the same key from her simulated channel record. Both use the session ID as HKDF salt and `qportal-bb84-live-v1` as info. The browser and server compare fingerprints; the key itself is never sent. The browser computes an HMAC-SHA256 proof bound to session ID, normalized email and login/sign-up action.
5. The login/sign-up request includes `session_id` and `proof`. The server accepts only a clean, unexpired, unused session with a valid constant-time HMAC proof. A missing proof, wrong proof, proof from another session, or reused session ID is rejected. This proof is checked only at login/sign-up; it is not used to sign every API request.
6. When the visible **Eve is on the line** switch is enabled, Eve intercepts every qubit on the first attempt, measures in a random basis and resends a state. The sample is expected to show about 25% error. The attempt is marked red and its key is discarded. The browser automatically retries with Eve off; the clean attempt is shown green and produces the fingerprint. Up to three attempts are allowed.

### Endpoints

- `POST /api/bb84/start` — create Alice's 1,600 simulated qubits on the server (nothing about the states is returned); optional Eve injection and attempt group ID.
- `POST /api/bb84/sift` — receive Bob's bases, return Bob's measured bits, Alice's bases and the sample positions.
- `POST /api/bb84/sample` — compare the disclosed sample, compute QBER / upper bound, record the verdict and derive the server-side key only for a secure clean exchange.
- `POST /api/auth/login` and `/api/auth/register` — require the one-time browser HMAC proof in live mode.
- `GET /api/bb84/audit.csv` — authenticated per-account export of that account's audit rows.

BB84 sessions are held in server memory for this single-process demo and expire after 10 minutes. Run one Uvicorn worker so the start/sift/sample requests reach the same process. The audit table persists the metrics, not Alice/Bob measurement strings or key material.

## Sample size and false-reject benchmark

The standard 30% sample is used. With only 800 qubits (about 400 sifted bits, a 120-bit sample) and 4.5% channel noise, **47 of 200** clean exchanges were falsely rejected (23.5%). Raising the sample to 65% fixed that but publicly revealed most of the sifted bits. The fix now is more qubits instead: 1,600 qubits give about 800 sifted bits, a 30% sample of about 240 bits, and about 560 secret bits. The new false-reject rate is measured with `tests/test_bb84_live.py` (see the results line below).

Recorded benchmark on 31 October 2026 (1,600 qubits, 30% sample, 4.5% channel noise, simulated channel, no IBM hardware):

| Check | Result |
|---|---:|
| Clean runs | 2,000 |
| Clean false rejects | 35 / 2,000 (1.75%) |
| Average clean QBER | 4.50% |
| Average sifted bits | 801.1 |
| Average sample | 240.3 bits |
| Average secret bits (minimum) | 553.7 (499) |
| Eve runs | 500 |
| Eve detected | 500 / 500 |
| Average Eve QBER (minimum) | 27.14% (19.5%) |

The false-reject rate stays under the 5% ceiling, and a rejected clean attempt is retried automatically (up to three attempts).

## Run locally

Classic remains the default:

```bash
uvicorn portal.main:app --reload --port 8000
```

Enable live mode:

```bash
QPORTAL_LOGIN_MODE=live uvicorn portal.main:app --host 127.0.0.1 --port 8000
```

Install the project requirements first (`pip install -r portal/requirements.txt`). Web Crypto's `crypto.subtle` is restricted by browsers to secure contexts. `localhost` works for local testing; classmates opening the site over a LAN IP should use HTTPS (for example, a Cloudflare quick tunnel) rather than plain `http://192.168...` if their browser does not expose Web Crypto there.

For same-Wi-Fi hosting, the server can listen on all interfaces:

```bash
QPORTAL_LOGIN_MODE=live uvicorn portal.main:app --host 0.0.0.0 --port 8000
```

Use an HTTPS tunnel for classmates if the browser's secure-context check blocks Web Crypto. Do not expose a development database or real credentials publicly.

## 10–15 account test

Run against the live-mode server:

```bash
python scripts/load_test_accounts.py --base-url https://YOUR-HTTPS-URL --accounts 12
```

By default the script creates 12 generated `example.test` accounts and uses generated throwaway passwords. To test with actual classmates, create a local CSV with `email,password,name` columns containing their email addresses and throwaway demo passwords, then run:

```bash
python scripts/load_test_accounts.py --base-url https://YOUR-HTTPS-URL --accounts-file classmates.csv
```

Keep `classmates.csv` private and delete it after the demo. The script does not write passwords, HMAC proofs, keys or measurement bits to its output. Each account is registered and then logs in three times; the first login turns Eve on and observes the red attempt / clean retry. The first three accounts also run missing-proof, wrong-proof, wrong-session and replay checks. The CSV is exported from the persisted `bb84_live_audit` table; the Markdown summary reports accounts, logins, Eve runs/detections, clean false rejects and median login time.

## Audit fields

`bb84_live_audit` records account, timestamp, mode, qubit count, sifted bits, sample size, error rate, 95% upper bound, Eve on/off, verdict, attempt number and latency. It deliberately excludes raw BB84 measurement strings, keys, proofs and passwords. The authenticated CSV endpoint is scoped to the signed-in account; the load-test script gathers each participant's rows to create the combined test CSV.

## Tests

```bash
QPORTAL_DATA_DIR="$(mktemp -d)" python -m pytest -q tests/test_bb84_live.py
QPORTAL_DATA_DIR="$(mktemp -d)" python -m pytest -q tests/test_login_bb84.py tests/test_bb84_security_hardening.py
```

The live tests cover clean key agreement and at least 128 secret bits, 100/100 Eve detection, the 200-run clean false-rejection ceiling, detected-attempt discard followed by clean retry, missing/wrong/cross-session proofs, session replay, API login proof enforcement, and audit fields.
