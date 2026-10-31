/* Browser Bob for QPortal live BB84. The key is held in this closure only. */
(function () {
  "use strict";
  const INFO = new TextEncoder().encode("qportal-bb84-live-v1");
  const encoder = new TextEncoder();
  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `BB84 request failed (${response.status})`);
    return data;
  }
  function randomBits(n) {
    const bytes = new Uint8Array(n);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, x => x & 1);
  }
  async function deriveKey(secretBits, sessionId) {
    if (secretBits.length < 128) throw new Error("The sifted key has fewer than 128 bits.");
    const raw = new Uint8Array(secretBits);
    const material = await crypto.subtle.importKey("raw", raw, "HKDF", false, ["deriveBits"]);
    const bits = await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt: encoder.encode(sessionId), info: INFO }, material, 256);
    return new Uint8Array(bits);
  }
  async function fingerprint(key) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", key));
    return Array.from(digest.slice(0, 10), b => b.toString(16).padStart(2, "0").toUpperCase()).join("").match(/.{1,4}/g).join("-");
  }
  async function proof(key, sessionId, email, mode) {
    const cryptoKey = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const msg = encoder.encode(`qportal-live-login-v1|${mode}|${sessionId}|${email.trim().toLowerCase()}`);
    const signed = new Uint8Array(await crypto.subtle.sign("HMAC", cryptoKey, msg));
    return Array.from(signed, b => b.toString(16).padStart(2, "0")).join("");
  }
  async function run(eveOn, account, onUpdate) {
    if (!globalThis.crypto || !crypto.getRandomValues || !crypto.subtle) {
      throw new Error("Live BB84 needs Web Crypto. Open QPortal over HTTPS (for example, a Cloudflare Tunnel) or localhost.");
    }
    const attempts = [];
    const groupId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, "0")).join("");
    for (let attemptNo = 1; attemptNo <= 3; attemptNo++) {
      const attackThisAttempt = !!eveOn && attemptNo === 1;
      const start = await post("/api/bb84/start", { eve_on: attackThisAttempt, attempt: attemptNo, account: account || "", group_id: groupId });
      const N = start.qubits, demoRows = start.demo_rows;
      // Bob commits to his bases BEFORE he learns anything; the simulated channel then returns his outcomes.
      const bobBases = randomBits(N);
      const sift = await post("/api/bb84/sift", { session_id: start.session_id, bob_bases: bobBases, account: account || "" });
      const bobBits = sift.bob_bits;
      if (!Array.isArray(bobBits) || bobBits.length !== N) throw new Error("The channel returned an invalid measurement result.");
      const kept = [];
      for (let i = 0; i < N; i++) if (bobBases[i] === sift.alice_bases[i]) kept.push(i);
      const sampleSet = new Set(sift.sample_positions);
      const sampleBits = sift.sample_positions.map(i => bobBits[i]);
      const result = await post("/api/bb84/sample", { session_id: start.session_id, bob_sample_bits: sampleBits, account: account || "" });
      const attempt = {
        number: attemptNo, qubits: N, eve: attackThisAttempt, verdict: result.verdict,
        sifted: result.sifted_bits, sample: result.sample_size, errors: result.errors,
        qber: result.error_rate, upper: result.upper_bound, limit: result.limit,
        secretBits: result.secret_bits, sessionId: start.session_id,
        trace: { aliceBits: sift.trace.alice_bits, aliceBases: sift.trace.alice_bases,
          eveBases: sift.trace.eve_bases, bobBases: bobBases.slice(0, 20), bobBits: bobBits.slice(0, 20),
          kept: kept.slice(0, 20).map(i => i),
          rows: Array.from({ length: demoRows }, (_, i) => ({ index: i + 1, aliceBit: sift.trace.alice_bits[i],
            aliceBasis: sift.trace.alice_bases[i], eveBasis: sift.trace.eve_bases.length ? sift.trace.eve_bases[i] : null,
            bobBasis: bobBases[i], bobBit: bobBits[i], kept: bobBases[i] === sift.alice_bases[i],
            mismatch: bobBases[i] === sift.alice_bases[i] && bobBits[i] !== sift.trace.alice_bits[i] })) }
      };
      attempts.push(attempt);
      if (onUpdate) onUpdate({ attempts: attempts.slice(), current: attempt, running: result.verdict !== "secure" });
      if (result.verdict === "secure") {
        const secretPositions = kept.filter(i => !sampleSet.has(i) && i >= demoRows);  // demo-table rows are never key bits
        const secretBits = secretPositions.map(i => bobBits[i]);
        const key = await deriveKey(secretBits, start.session_id);
        const fp = await fingerprint(key);
        if (fp !== result.fingerprint) throw new Error("Browser/server key fingerprints do not match. The exchange was discarded.");
        if (onUpdate) onUpdate({ attempts: attempts.slice(), current: attempt, running: false, ready: true, fingerprint: fp });
        return { session_id: start.session_id, key, fingerprint: fp, attempts,
          makeProof: (email, mode) => proof(key, start.session_id, email, mode) };
      }
      if (attemptNo === 3) break;
    }
    throw new Error("BB84 could not establish a clean key after three attempts. Please restart the exchange.");
  }
  globalThis.QPortalBB84Live = { run };
})();
