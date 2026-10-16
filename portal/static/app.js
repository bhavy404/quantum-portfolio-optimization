/* QPortal front end – plain JavaScript, no build step. All text from the server goes through textContent. */
(function () {
  "use strict";

  // ---------- helpers ----------
  const $app = document.getElementById("app");
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
  const pct = (v, d = 1) => (v * 100).toFixed(d) + "%";
  const spct = (v, d = 1) => (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%";
  const signClass = (v) => (v >= 0 ? "gain" : "loss");
  const short = (s) => s.replace(".NS", "");

  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v;
      else if (k in el && !k.includes("-") && k !== "list") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: keep going */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  const S = {
    token: store.get("qp_token", null), user: store.get("qp_user", null), health: null,
    rows: [], extra: {}, asOf: null,
    shortlist: store.get("qp_short", []),
    answers: store.get("qp_answers", {}), q: store.get("qp_q", 0.5), k: store.get("qp_k", null),
    amount: store.get("qp_amount", 100000), engine: "qaoa_sim",
    sort: { key: "ratio", dir: -1 }, filter: { text: "", sector: "", risk: "" },
  };
  let charts = [];
  let timer = null;

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json" };
    if (S.token) headers.Authorization = "Bearer " + S.token;
    const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (res.status === 401 && S.token) { logout(); throw new Error(data && data.detail || "Please log in again."); }
    if (!res.ok) {
      let msg = data && data.detail;
      if (Array.isArray(msg)) msg = msg.map((m) => m.msg.replace(/^Value error, /, "")).join(" ");
      throw new Error(msg || "Something went wrong. Please try again.");
    }
    return data;
  }

  function logout() {
    S.token = null; S.user = null; store.del("qp_token"); store.del("qp_user");
    location.hash = "#/login"; route();
  }

  function killCharts() { charts.forEach((c) => c.destroy()); charts = []; if (timer) { clearTimeout(timer); timer = null; } }

  function setupChartDefaults() {
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = css("--ink-2");
    Chart.defaults.borderColor = css("--line");
  }

  // ---------- shell ----------
  const STEPS = ["Choose stocks", "Set your comfort", "See the result"];

  function shell(content, active) {
    const nav = S.token ? h("nav", { class: "nav", "aria-label": "Main" },
      h("a", { href: "#/", "aria-current": active === "flow" ? "page" : null }, "Pick stocks"),
      h("a", { href: "#/history", "aria-current": active === "history" ? "page" : null }, "My results"),
      h("a", { href: "#/security", "aria-current": active === "security" ? "page" : null }, "Security")) : h("div", { class: "nav" });
    const who = S.token ? h("div", { class: "who" }, h("span", null, S.user && (S.user.name || S.user.email)),
      h("button", { class: "btn alt small", onclick: logout }, "Log out")) : null;
    $app.replaceChildren(
      S.health && S.health.synthetic ? h("div", { class: "banner", role: "alert" }, "TEST DATA – these prices are made up and only used for testing.") : null,
      h("header", { class: "topbar" }, h("div", { class: "wrap" }, h("a", { class: "brand", href: "#/" }, h("i"), "QPortal"), nav, who)),
      h("main", { class: "wrap", id: "main" }, content));
    window.scrollTo(0, 0);
  }

  function stepper(n) {
    return h("ol", { class: "steps", "aria-label": "Steps" }, STEPS.map((t, i) =>
      h("li", { class: i + 1 === n ? "on" : i + 1 < n ? "done" : "", "aria-current": i + 1 === n ? "step" : null }, h("b", null, i + 1 < n ? "✓" : i + 1), t)));
  }

  // ---------- login ----------
  function viewLogin() {
    let mode = "login";
    const err = h("p", { class: "error", role: "alert" });
    const form = h("form", { novalidate: true });
    const tabs = h("div", { class: "tabs", role: "tablist" });
    function draw() {
      tabs.replaceChildren(
        h("button", { type: "button", role: "tab", "aria-selected": String(mode === "login"), onclick: () => { mode = "login"; draw(); } }, "Log in"),
        h("button", { type: "button", role: "tab", "aria-selected": String(mode === "register"), onclick: () => { mode = "register"; draw(); } }, "Create account"));
      form.replaceChildren(
        mode === "register" ? h("label", { class: "field" }, h("span", null, "Your name"), h("input", { type: "text", name: "name", autocomplete: "name" })) : null,
        h("label", { class: "field" }, h("span", null, "Email"), h("input", { type: "email", name: "email", autocomplete: "email", required: true })),
        h("label", { class: "field" }, h("span", null, "Password"), h("input", { type: "password", name: "password", autocomplete: mode === "login" ? "current-password" : "new-password", required: true, minLength: 8 }),
          mode === "register" ? h("small", { class: "muted" }, "At least 8 characters.") : null),
        err,
        h("button", { class: "btn", type: "submit", style: "width:100%" }, mode === "login" ? "Log in" : "Create account"));
      err.textContent = "";
    }
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form); err.textContent = "";
      const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
      try {
        const out = await api("/api/auth/" + mode, { method: "POST", body: { email: fd.get("email"), password: fd.get("password"), name: fd.get("name") || "" } });
        S.token = out.token; S.user = out.user; store.set("qp_token", out.token); store.set("qp_user", out.user);
        location.hash = "#/"; route();
      } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
    });
    draw();
    shell(h("div", { class: "login" },
      h("div", { class: "pitch" },
        h("h1", null, "Pick stocks with less guesswork"),
        h("p", null, "Choose the Indian companies you are considering, tell us how much ups and downs you can live with, and get a suggested mix with plain charts."),
        h("ul", null,
          h("li", null, "See every stock's growth and risk on one map."),
          h("li", null, "A quantum-computing method picks the mix, and an exact check confirms it."),
          h("li", null, "Your saved results are encrypted with a key built from quantum circuits."))),
      h("div", { class: "panel" }, tabs, form)));
  }

  // ---------- data ----------
  async function loadRows() {
    if (S.rows.length) return;
    const out = await api("/api/screener");
    S.rows = out.rows; S.asOf = out.as_of;
  }
  const allRows = () => S.rows.concat(Object.values(S.extra));
  const rowOf = (sym) => allRows().find((r) => r.symbol === sym);
  const saveShort = () => store.set("qp_short", S.shortlist);

  // ---------- choose stocks ----------
  const COLS = [
    ["annual_return", "Growth per year", (r) => h("span", { class: signClass(r.annual_return) }, spct(r.annual_return, 0))],
    ["return_1y", "Last 12 months", (r) => r.return_1y == null ? "–" : h("span", { class: signClass(r.return_1y) }, spct(r.return_1y, 0))],
    ["volatility", "Typical ups and downs", (r) => pct(r.volatility, 0)],
    ["max_drawdown", "Worst fall", (r) => h("span", { class: "loss" }, pct(r.max_drawdown, 0))],
    ["ratio", "Growth for the risk", (r) => r.ratio.toFixed(2)],
  ];

  async function viewChoose() {
    shell(h("p", { class: "boot" }, "Loading stocks…"), "flow");
    try { await loadRows(); } catch (ex) { shell(h("div", { class: "panel" }, h("h2", null, "Stocks are not ready yet"), h("p", { class: "error" }, ex.message), h("button", { class: "btn", onclick: route }, "Try again")), "flow"); return; }
    const max = S.health.max_shortlist, min = S.health.min_shortlist;
    const sectors = [...new Set(allRows().map((r) => r.sector))].sort();
    const tbody = h("tbody"), moreBox = h("div"), msg = h("p", { class: "error", role: "alert" });
    const tray = h("div", { class: "tray noprint" });
    const mapCanvas = h("canvas", { role: "img", "aria-label": "Map of stocks: growth per year against typical ups and downs. Use the table below to choose stocks without the map." });

    const toggle = (sym) => {
      const i = S.shortlist.indexOf(sym);
      if (i >= 0) S.shortlist.splice(i, 1);
      else if (S.shortlist.length >= max) { msg.textContent = `You can compare up to ${max} stocks. Remove one first.`; return; }
      else S.shortlist.push(sym);
      msg.textContent = ""; saveShort(); refresh();
    };

    // map
    const pts = (rows) => rows.map((r) => ({ x: r.volatility * 100, y: r.annual_return * 100, sym: r.symbol, name: r.name }));
    let chart;
    const makeChart = () => { chart = new Chart(mapCanvas, {
      type: "scatter",
      data: { datasets: [
        { label: "Other stocks", data: [], backgroundColor: css("--dot"), pointRadius: 5, pointHoverRadius: 8 },
        { label: "On your shortlist", data: [], backgroundColor: css("--blue"), borderColor: css("--surface"), borderWidth: 2, pointRadius: 8, pointHoverRadius: 10 }] },
      options: {
        maintainAspectRatio: false, animation: false,
        scales: {
          x: { title: { display: true, text: "Typical ups and downs, % a year (further left is steadier)" }, ticks: { callback: (v) => v + "%" } },
          y: { title: { display: true, text: "Growth per year" }, ticks: { callback: (v) => v + "%" } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          title: (it) => it[0].raw.name,
          label: (it) => [`Growth per year: ${it.raw.y.toFixed(0)}%`, `Typical ups and downs: ${it.raw.x.toFixed(0)}%`, "Click to add or remove"] } } },
        onClick: (e, els) => { if (els.length) toggle(els[0].element.$context.raw.sym); },
        onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; } } });
    charts.push(chart); };

    // table
    const visible = () => {
      const f = S.filter, t = f.text.trim().toLowerCase();
      let rows = allRows().filter((r) => (!t || r.symbol.toLowerCase().includes(t) || r.name.toLowerCase().includes(t)) && (!f.sector || r.sector === f.sector) && (!f.risk || r.risk_level === f.risk));
      const k = S.sort.key, d = S.sort.dir;
      rows.sort((a, b) => k === "name" ? d * -a.name.localeCompare(b.name) : d * ((a[k] ?? -9) - (b[k] ?? -9)));
      return rows;
    };
    const head = h("tr");
    function sortBtn(k, label) {
      return h("button", { type: "button", onclick: () => { S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : -1 }; refresh(); } }, label + (S.sort.key === k ? (S.sort.dir > 0 ? " ▲" : " ▼") : ""));
    }
    function drawHead() {
      head.replaceChildren(h("th", { scope: "col" }, sortBtn("name", "Stock")), h("th", { scope: "col" }, "Price"),
        ...COLS.map(([k, label]) => h("th", { scope: "col", "aria-sort": S.sort.key === k ? (S.sort.dir > 0 ? "ascending" : "descending") : "none" }, sortBtn(k, label))),
        h("th", { scope: "col" }, "Risk"), h("th", { scope: "col" }, h("span", { class: "sr" }, "Choose")));
    }
    function drawTable() {
      const rows = visible();
      tbody.replaceChildren(...rows.map((r) => {
        const on = S.shortlist.includes(r.symbol);
        return h("tr", { class: on ? "sel" : "" },
          h("td", { class: "nm" }, h("b", null, r.name), h("small", null, `${short(r.symbol)} · ${r.sector}`)),
          h("td", { "data-l": "Price", class: "num" }, inr(r.price)),
          COLS.map(([k, label, fmt]) => h("td", { "data-l": label, class: "num" }, fmt(r))),
          h("td", { "data-l": "Risk" }, h("span", { class: "tag " + r.risk_level }, r.risk_level)),
          h("td", null, h("button", { class: on ? "btn alt small" : "btn small", "aria-pressed": String(on), onclick: () => toggle(r.symbol) }, on ? "Remove" : "Add")));
      }));
      if (!rows.length) tbody.replaceChildren(h("tr", null, h("td", { colSpan: 9 }, "No stocks match. Clear a filter or search for another name.")));
    }
    function drawTray() {
      const n = S.shortlist.length;
      tray.replaceChildren(h("div", { class: "wrap" },
        h("div", { class: "chips", "aria-label": "Your shortlist" }, n ? S.shortlist.map((s) => h("span", { class: "chip" }, (rowOf(s) || {}).name || short(s), h("button", { "aria-label": "Remove " + short(s), onclick: () => toggle(s) }, "×"))) : h("span", { class: "muted" }, `Add at least ${min} stocks to compare.`)),
        h("strong", { class: "num" }, `${n} of ${max}`),
        h("button", { class: "btn", disabled: n < min, onclick: () => { location.hash = "#/comfort"; } }, "Continue")));
    }
    function refresh() {
      const on = new Set(S.shortlist), all = allRows();
      chart.data.datasets[0].data = pts(all.filter((r) => !on.has(r.symbol)));
      chart.data.datasets[1].data = pts(all.filter((r) => on.has(r.symbol)));
      chart.update("none");
      drawHead(); drawTable(); drawTray();
    }

    // search of all listed companies
    let searchTimer = null;
    const onSearch = (e) => {
      S.filter.text = e.target.value; drawTable();
      clearTimeout(searchTimer);
      const t = S.filter.text.trim();
      if (t.length < 2) { moreBox.replaceChildren(); return; }
      searchTimer = setTimeout(async () => {
        try {
          const found = (await api("/api/stocks/search?q=" + encodeURIComponent(t))).filter((x) => !rowOf(x.symbol));
          moreBox.replaceChildren(found.length ? h("div", { class: "note" }, h("strong", null, "More listed companies"),
            h("p", { class: "muted" }, "We download prices when you add one, which can take a few seconds."),
            found.slice(0, 8).map((x) => h("div", { class: "rowline" }, h("span", null, `${x.name} (${short(x.symbol)})`),
              h("button", { class: "btn small", onclick: async (ev) => {
                ev.target.disabled = true; ev.target.textContent = "Loading…";
                try {
                  const d = await api("/api/stocks/" + encodeURIComponent(x.symbol));
                  const m = d.stock, vols = S.rows.map((r) => r.volatility).sort((a, b) => a - b);
                  const lo = vols[Math.floor(vols.length / 3)], hi = vols[Math.floor(2 * vols.length / 3)];
                  S.extra[x.symbol] = { symbol: x.symbol, name: x.name, sector: x.sector, price: m.price, return_1y: null, annual_return: m.annual_return, volatility: m.volatility, ratio: m.ratio, max_drawdown: m.max_drawdown, risk_level: m.volatility <= lo ? "Low" : m.volatility <= hi ? "Medium" : "High" };
                  toggle(x.symbol); moreBox.replaceChildren();
                } catch (ex) { ev.target.textContent = "Not available"; msg.textContent = ex.message; }
              } }, "Add")))) : h("p", { class: "muted" }, "No other listed company matches that search."));
        } catch (ex) { /* search is optional */ }
      }, 350);
    };

    const suggest = () => {
      const perSector = {}; const picks = [];
      for (const r of [...S.rows].sort((a, b) => b.ratio - a.ratio)) {
        if ((perSector[r.sector] || 0) >= 2) continue;
        perSector[r.sector] = (perSector[r.sector] || 0) + 1; picks.push(r.symbol);
        if (picks.length >= 10) break;
      }
      S.shortlist = picks; saveShort(); refresh();
    };

    shell(h("div", null,
      stepper(1),
      h("div", { class: "pagehead" }, h("h1", null, "Choose the stocks you are considering"),
        h("p", null, `We looked at ${S.rows.length} large Indian companies over roughly the past three years. Pick ${min} to ${max} to compare. Click a dot on the map or use the table.`)),
      h("div", { class: "panel" },
        h("div", { class: "mapbox" }, mapCanvas),
        h("div", { class: "legend" }, h("span", { style: "--c:var(--dot)" }, "Other stocks"), h("span", { style: "--c:var(--blue)" }, "On your shortlist"),
          h("span", null, "Up and to the left is the sweet spot: more growth, steadier ride."))),
      h("div", { class: "toolbar" },
        h("label", { class: "field", style: "flex:2" }, h("span", null, "Search any listed company"), h("input", { type: "text", placeholder: "e.g. Tata, Infosys, HDFC", oninput: onSearch, value: S.filter.text })),
        h("label", { class: "field" }, h("span", null, "Sector"), h("select", { onchange: (e) => { S.filter.sector = e.target.value; drawTable(); } }, h("option", { value: "" }, "All sectors"), sectors.map((s) => h("option", { value: s, selected: S.filter.sector === s }, s)))),
        h("label", { class: "field" }, h("span", null, "Risk level"), h("select", { onchange: (e) => { S.filter.risk = e.target.value; drawTable(); } }, ["", "Low", "Medium", "High"].map((s) => h("option", { value: s, selected: S.filter.risk === s }, s || "Any risk")))),
        h("button", { class: "btn alt", onclick: suggest }, "Suggest a shortlist")),
      moreBox, msg,
      h("div", { class: "panel", style: "overflow-x:auto" }, h("table", { class: "stocks" }, h("caption", { class: "sr" }, "Stocks you can add to your shortlist"), h("thead", null, head), tbody)),
      h("p", { class: "disclaimer" }, "Growth is the average yearly gain over the period shown. Ups and downs is how much the price typically swings in a year. “Growth for the risk” is growth divided by ups and downs, with no risk-free rate. Past results do not predict future results. This is an educational tool, not investment advice."),
      tray), "flow");
    makeChart();
    refresh();
  }

  // ---------- comfort ----------
  const QUESTIONS = [
    ["q1", "If your investments fell 20% in one month, you would…", ["Sell some to stop the losses", "Wait and do nothing", "Buy more while prices are low"]],
    ["q2", "When might you need this money?", ["Within a year", "In one to three years", "In more than three years"]],
    ["q3", "What matters more to you?", ["Not losing money", "A bit of both", "Growing my money"]],
  ];
  const qLabel = (q) => (q < 0.35 ? "Safety first" : q < 0.65 ? "Balanced" : "Growth first");

  async function viewComfort() {
    if (S.shortlist.length < 3) { location.hash = "#/"; return; }
    shell(h("p", { class: "boot" }, "Loading…"), "flow");
    try { await loadRows(); } catch (ex) { location.hash = "#/"; return; }
    const max = S.shortlist.length - 1;
    if (!S.k || S.k > max) S.k = Math.max(2, Math.min(max, Math.round(S.shortlist.length / 3)));
    const rows = S.shortlist.map(rowOf).filter(Boolean);
    const out = h("span", { class: "big" }), kEl = h("b", { class: "num" }), slider = h("input", { type: "range", class: "slider", min: 10, max: 90, step: 1, "aria-label": "Safety to growth", value: Math.round(S.q * 100) });
    const mapCanvas = h("canvas", { role: "img", "aria-label": "Preview map of your shortlist showing which stocks lean favoured" });
    const err = h("p", { class: "error", role: "alert" });
    const save = () => { store.set("qp_q", S.q); store.set("qp_k", S.k); store.set("qp_amount", S.amount); store.set("qp_answers", S.answers); };
    const z = (a) => { const m = a.reduce((x, y) => x + y, 0) / a.length, s = Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / a.length) || 1; return a.map((v) => (v - m) / s); };

    let chart;
    const makeChart = () => { chart = new Chart(mapCanvas, { type: "scatter",
      data: { datasets: [{ data: [], backgroundColor: [], borderColor: [], borderWidth: 3, pointRadius: 8 }] },
      options: { maintainAspectRatio: false, animation: false,
        scales: { x: { title: { display: true, text: "Typical ups and downs (% a year)" }, ticks: { callback: (v) => v + "%" } }, y: { title: { display: true, text: "Growth per year" }, ticks: { callback: (v) => v + "%" } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => it[0].raw.name, label: (it) => [`Growth per year: ${it.raw.y.toFixed(0)}%`, `Typical ups and downs: ${it.raw.x.toFixed(0)}%`] } } } } });
    charts.push(chart); };

    function preview() {
      const zr = z(rows.map((r) => r.annual_return)), zv = z(rows.map((r) => r.volatility));
      const score = rows.map((_, i) => S.q * zr[i] - (1 - S.q) * zv[i]);
      const top = new Set([...score.keys()].sort((a, b) => score[b] - score[a]).slice(0, S.k));
      chart.data.datasets[0].data = rows.map((r) => ({ x: r.volatility * 100, y: r.annual_return * 100, name: r.name }));
      chart.data.datasets[0].backgroundColor = rows.map((_, i) => (top.has(i) ? css("--orange") : css("--blue")));
      chart.data.datasets[0].borderColor = rows.map((_, i) => (top.has(i) ? css("--orange") : css("--surface")));
      chart.data.datasets[0].pointRadius = rows.map((_, i) => (top.has(i) ? 10 : 6));
      chart.update("none");
      out.textContent = qLabel(S.q); kEl.textContent = S.k; slider.value = Math.round(S.q * 100);
    }
    slider.addEventListener("input", () => { S.q = slider.value / 100; save(); preview(); });
    const setAnswer = (id, v) => {
      S.answers[id] = v;
      if (QUESTIONS.every(([qid]) => S.answers[qid] != null)) {
        const score = QUESTIONS.reduce((a, [qid]) => a + S.answers[qid], 0);
        S.q = +(0.2 + (score / 6) * 0.6).toFixed(2);
      }
      save(); preview();
    };
    const run = async (ev) => {
      ev.target.disabled = true; err.textContent = "";
      try {
        const r = await api("/api/recommend", { method: "POST", body: { symbols: S.shortlist, k: S.k, q: S.q, amount: S.amount, engine: S.engine } });
        location.hash = "#/result/" + r.run_id;
      } catch (ex) { err.textContent = ex.message; ev.target.disabled = false; }
    };
    const engines = [["qaoa_sim", "Quantum circuit simulator (recommended)"], ["classical", "Exact classical search only"]];
    if (S.health.ibm_configured) engines.splice(1, 0, ["qaoa_hw", "IBM quantum processor (slower, up to a few minutes)"]);

    shell(h("div", null, stepper(2),
      h("div", { class: "pagehead" }, h("h1", null, "How much ups and downs can you live with?"),
        h("p", null, "Answer three quick questions, or just move the slider. The preview shows which of your stocks lean favoured. The final pick comes from the optimiser on the next step.")),
      h("div", { class: "grid2" },
        h("div", { class: "panel" },
          QUESTIONS.map(([id, text, opts]) => h("fieldset", null, h("legend", null, text), opts.map((o, i) => h("label", { class: "opt" }, h("input", { type: "radio", name: id, checked: S.answers[id] === i, onchange: () => setAnswer(id, i) }), o)))),
          h("div", null, h("p", { style: "margin-bottom:6px" }, "Your comfort level: ", out), slider, h("div", { class: "sliderlabels" }, h("span", null, "Safety"), h("span", null, "Growth")))),
        h("div", null,
          h("div", { class: "panel" }, h("div", { class: "mapbox", style: "height:300px" }, mapCanvas),
            h("div", { class: "legend" }, h("span", { style: "--c:var(--orange)" }, "Leaning favoured"), h("span", { style: "--c:var(--blue)" }, "Other shortlisted stocks"))),
          h("div", { class: "panel" },
            h("div", { class: "rowline" }, h("span", null, "How many stocks to hold"), h("span", { class: "stepper" },
              h("button", { class: "btn alt small", "aria-label": "Fewer", onclick: () => { if (S.k > 1) { S.k--; save(); preview(); } } }, "−"), kEl,
              h("button", { class: "btn alt small", "aria-label": "More", onclick: () => { if (S.k < max) { S.k++; save(); preview(); } } }, "+"))),
            h("label", { class: "field", style: "margin-top:14px" }, h("span", null, "Amount to invest (₹)"), h("input", { type: "number", min: 1000, step: 1000, value: S.amount, oninput: (e) => { S.amount = Math.max(1000, +e.target.value || 1000); save(); } })),
            h("label", { class: "field" }, h("span", null, "Method"), h("select", { onchange: (e) => { S.engine = e.target.value; } }, engines.map(([v, t]) => h("option", { value: v }, t)))),
            err, h("button", { class: "btn", style: "width:100%", onclick: run }, "Analyse my shortlist"))))), "flow");
    makeChart();
    preview();
  }

  // ---------- result ----------
  async function viewResult(id) {
    shell(h("div", { class: "waiting" }, h("div", { class: "spinner" }), h("h2", null, "Analysing your shortlist"), h("p", { class: "muted" }, "This usually takes under a minute.")), "flow");
    const t0 = Date.now();
    const poll = async () => {
      let r;
      try { r = await api("/api/runs/" + id); } catch (ex) { shell(h("div", { class: "panel" }, h("h2", null, "We could not open this result"), h("p", { class: "error" }, ex.message), h("a", { class: "btn", href: "#/" }, "Back to stocks")), "flow"); return; }
      if (r.status === "running") {
        const s = Math.round((Date.now() - t0) / 1000);
        const p = document.querySelector(".waiting .muted"); if (p) p.textContent = `This usually takes under a minute. ${s}s so far.`;
        timer = setTimeout(poll, 1500); return;
      }
      if (r.status === "failed") { shell(h("div", { class: "panel" }, h("h2", null, "We could not finish this analysis"), h("p", { class: "error" }, r.error), h("a", { class: "btn", href: "#/comfort" }, "Change my choices")), "flow"); return; }
      drawResult(r);
    };
    poll();
  }

  function drawResult(run) {
    const R = run.result, m = R.metrics, c = R.charts, q = R.quantum, amt = R.params.amount, scale = amt / 100000;
    const last = c.basket.length - 1, years = (R.data.days / 252).toFixed(1);
    const end = (arr) => arr[last] * scale;
    const names = R.chosen.map((s) => (R.allocation.find((a) => a.symbol === s) || {}).name || short(s));
    const bench = m.benchmark;
    const tile = (label, val, cls, sub) => h("div", { class: "tile" }, h("b", { class: cls }, val), h("span", null, label), h("small", null, sub));

    const cv = (label) => h("canvas", { role: "img", "aria-label": label });
    const gCanvas = cv("Growth of your investment over time compared with the Nifty 50"), dCanvas = cv("How far the mix fell below its previous high"), kCanvas = cv("Every possible mix of stocks plotted by growth and ups and downs");

    shell(h("div", null, stepper(3),
      h("div", { class: "answer" },
        h("h1", null, "Hold ", names.join(", ").replace(/, ([^,]*)$/, " and $1"), " in equal amounts"),
        h("p", { class: "muted" }, `Chosen from your ${R.params.symbols.length} shortlisted stocks · ${R.params.risk_label.toLowerCase()} · based on ${R.data.first} to ${R.data.last}`),
        h("div", { class: "growth" },
          h("div", null, h("span", null, `${inr(amt)} in this mix, ${years} years ago, would be`), h("b", { class: signClass(end(c.basket) - amt) }, inr(end(c.basket)))),
          c.benchmark ? h("div", null, h("span", null, "Same amount in the Nifty 50"), h("b", null, inr(end(c.benchmark)))) : null,
          h("div", null, h("span", null, `Same amount across all ${R.params.symbols.length} shortlisted stocks`), h("b", null, inr(end(c.everything)))))),
      h("p", { class: "disclaimer" }, "This looks backwards. It shows how this mix would have done, not how it will do. Not investment advice."),
      h("div", { class: "tiles" },
        tile("Growth per year", spct(m.all.annual_return, 0), signClass(m.all.annual_return), bench ? `Nifty 50: ${spct(bench.annual_return, 0)}` : ""),
        tile("Typical ups and downs", pct(m.all.volatility, 0), "", bench ? `Nifty 50: ${pct(bench.volatility, 0)}` : ""),
        tile("Worst fall from a high", pct(m.all.max_drawdown, 0), "loss", bench ? `Nifty 50: ${pct(bench.max_drawdown, 0)}` : ""),
        tile("Worst single month", pct(m.all.worst_month, 0), "loss", `Recent period not used for picking: ${spct(m.test.annual_return, 0)} a year`)),

      h("div", { class: "panel" }, h("h2", null, "What to buy"),
        h("div", { style: "overflow-x:auto" }, h("table", { class: "stocks" }, h("thead", null, h("tr", null, ["Stock", "Share of money", "Price", "Shares to buy", "Amount"].map((t, i) => h("th", { scope: "col" }, t)))),
          h("tbody", null, R.allocation.map((a) => h("tr", null,
            h("td", { class: "nm" }, h("b", null, a.name), h("small", null, `${short(a.symbol)} · ${a.sector}`)),
            h("td", { "data-l": "Share of money", class: "num" }, pct(a.weight, 0), " ", h("span", { class: "bar", style: `width:${a.weight * 80}px` })),
            h("td", { "data-l": "Price", class: "num" }, inr(a.price)), h("td", { "data-l": "Shares to buy", class: "num" }, a.shares), h("td", { "data-l": "Amount", class: "num" }, inr(a.invest))))))),
        h("p", { class: "muted" }, `Cash left over after whole shares: ${inr(R.cash_left)}. Prices are the latest we have, not live quotes.`),
        h("button", { class: "btn alt small noprint", onclick: () => downloadCsv(R) }, "Download as CSV"), " ",
        h("button", { class: "btn alt small noprint", onclick: () => window.print() }, "Print")),

      h("div", { class: "panel" }, h("h2", null, `How ${inr(amt)} would have grown`), h("div", { class: "chartbox tall" }, gCanvas),
        h("div", { class: "legend" }, h("span", { style: "--c:var(--blue)" }, "Your mix"), c.benchmark ? h("span", { style: "--c:var(--muted)" }, "Nifty 50") : null, h("span", { style: "--c:var(--dot)" }, "All shortlisted stocks"))),
      h("div", { class: "grid2" },
        h("div", { class: "panel" }, h("h2", null, "How bad the falls got"), h("p", { class: "muted" }, "Distance below the highest value so far."), h("div", { class: "chartbox" }, dCanvas)),
        h("div", { class: "panel" }, h("h2", null, "Every possible mix"), h("p", { class: "muted" }, `Each dot is one way to pick ${R.params.k} of your ${R.params.symbols.length} stocks.`), h("div", { class: "chartbox" }, kCanvas),
          h("div", { class: "legend" }, h("span", { style: "--c:var(--orange)" }, "Your mix"), h("span", { style: "--c:transparent;--b:var(--orange)" }, "Best by the exact check"), h("span", { style: "--c:var(--dot)" }, "Other mixes")))),
      h("div", { class: "panel" }, h("h2", null, "Do these stocks move together?"), h("p", { class: "muted" }, "Closer to 1 means they rise and fall together, so owning both spreads less risk."), heat(c)),
      h("div", { class: "panel", style: "overflow-x:auto" }, h("h2", null, "Your shortlist at a glance"),
        h("table", { class: "stocks" }, h("thead", null, h("tr", null, ["Stock", "Growth per year", "Typical ups and downs", "Worst fall", "Growth for the risk", ""].map((t) => h("th", { scope: "col" }, t)))),
          h("tbody", null, R.stocks.map((s) => h("tr", { class: s.chosen ? "sel" : "" },
            h("td", { class: "nm" }, h("b", null, s.name), h("small", null, `${short(s.symbol)} · ${s.sector}`)),
            h("td", { "data-l": "Growth per year", class: "num" }, h("span", { class: signClass(s.annual_return) }, spct(s.annual_return, 0))),
            h("td", { "data-l": "Typical ups and downs", class: "num" }, pct(s.volatility, 0)), h("td", { "data-l": "Worst fall", class: "num loss" }, pct(s.max_drawdown, 0)),
            h("td", { "data-l": "Growth for the risk", class: "num" }, s.ratio.toFixed(2)), h("td", null, s.chosen ? h("span", { class: "tag" }, "In your mix") : ""))))),
        R.dropped.length ? h("p", { class: "muted" }, "Left out for too little price history: " + R.dropped.map(short).join(", ")) : null),

      how(R),
      h("p", { class: "disclaimer" }, `Equal amounts in each stock, rebalanced daily. Growth is the average yearly gain, ups and downs is yearly volatility. The Nifty 50 line uses the index, not an investable fund. Picks use the first 70% of days (to ${R.data.train_until}); the last 30% (from ${R.data.test_from}) are shown separately. Past results do not predict future results. This is an educational tool, not investment advice.`),
      h("div", { class: "noprint" }, h("a", { class: "btn alt", href: "#/" }, "Change my shortlist"), " ", h("a", { class: "btn alt", href: "#/history" }, "My saved results"))), "flow");

    // charts
    const splitIdx = c.dates.findIndex((d) => d >= c.split_date);
    const splitPlugin = { id: "split", afterDraw(chart, a, o) {
      if (o.index < 0) return; const x = chart.scales.x.getPixelForValue(o.index), { top, bottom } = chart.chartArea, g = chart.ctx;
      g.save(); g.strokeStyle = css("--muted"); g.setLineDash([4, 4]); g.beginPath(); g.moveTo(x, top); g.lineTo(x, bottom); g.stroke();
      g.fillStyle = css("--muted"); g.font = "12px " + Chart.defaults.font.family; g.textAlign = "right"; g.fillText("Not used for picking →", chart.chartArea.right, top + 14); g.restore(); } };
    const line = (label, data, color, w = 2.5) => ({ label, data: data.map((v) => v * scale), borderColor: color, borderWidth: w, pointRadius: 0, tension: 0.15 });
    const gset = [line("Your mix", c.basket, css("--blue"), 3)];
    if (c.benchmark) gset.push(line("Nifty 50", c.benchmark, css("--muted"), 2));
    gset.push(line("All shortlisted stocks", c.everything, css("--dot"), 2));
    charts.push(new Chart(gCanvas, { type: "line", data: { labels: c.dates, datasets: gset },
      options: { maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 7, callback(v) { return this.getLabelForValue(v).slice(0, 7); } }, grid: { display: false } }, y: { ticks: { callback: (v) => inr(v) } } },
        plugins: { legend: { display: false }, split: { index: splitIdx }, tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${inr(it.parsed.y)}` } } } }, plugins: [splitPlugin] }));
    charts.push(new Chart(dCanvas, { type: "line", data: { labels: c.dates, datasets: [{ label: "Below its high", data: c.drawdown, borderColor: css("--loss"), backgroundColor: css("--loss") + "33", fill: "origin", pointRadius: 0, borderWidth: 2 }] },
      options: { maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        scales: { x: { ticks: { maxTicksLimit: 5, callback(v) { return this.getLabelForValue(v).slice(0, 7); } }, grid: { display: false } }, y: { max: 0, ticks: { callback: (v) => v + "%" } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => `${it.parsed.y.toFixed(1)}% below its high` } } } } }));
    const others = c.cloud.filter((p) => !p.chosen && !p.best), mine = c.cloud.filter((p) => p.chosen), best = c.cloud.filter((p) => p.best);
    const ds = (d, col, r, bw, bc) => ({ data: d.map((p) => ({ x: p.v, y: p.r })), backgroundColor: col, borderColor: bc || col, borderWidth: bw || 0, pointRadius: r });
    charts.push(new Chart(kCanvas, { type: "scatter", data: { datasets: [ds(others, css("--dot") + "99", 3), ds(best, "transparent", 11, 3, css("--orange")), ds(mine, css("--orange"), 7)] },
      options: { maintainAspectRatio: false, animation: false,
        scales: { x: { title: { display: true, text: "Typical ups and downs (% a year)" }, ticks: { callback: (v) => v + "%" } }, y: { title: { display: true, text: "Growth per year" }, ticks: { callback: (v) => v + "%" } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => `Growth ${it.parsed.y.toFixed(0)}% · ups and downs ${it.parsed.x.toFixed(0)}%` } } } } }));
  }

  function heat(c) {
    const n = c.corr_names.length, box = h("div", { class: "heat", style: `grid-template-columns: 90px repeat(${n}, minmax(44px, 1fr))` });
    box.append(h("div", { class: "h" }));
    c.corr_names.forEach((s) => box.append(h("div", { class: "h" }, short(s))));
    c.corr.forEach((row, i) => { box.append(h("div", { class: "h", style: "text-align:left" }, short(c.corr_names[i])));
      row.forEach((v) => box.append(h("div", { class: "num", style: `background: color-mix(in srgb, var(--blue) ${Math.max(0, v) * 100}%, transparent); color: ${v > 0.55 ? "#fff" : "var(--ink)"}` }, v.toFixed(2)))); });
    return box;
  }

  function how(R) {
    const q = R.quantum, sampled = q.engine !== "classical";
    const top = q.samples.slice(0, 6);
    return h("details", { class: "panel" }, h("summary", null, "How this was chosen"),
      h("p", null, sampled
        ? `A quantum-computing method called QAOA (${q.label.toLowerCase().includes("ibm") ? "run on an IBM quantum processor" : "run on a simulator"}) searched all ways to pick ${R.params.k} of your ${q.qubits} stocks. It uses ${q.qubits} qubits${q.circuit_depth ? ` and a circuit about ${q.circuit_depth} steps deep` : ""}. The circuit was measured ${q.shots.toLocaleString("en-IN")} times, and we kept the lowest-cost valid mix it produced.`
        : "This result used an exact classical search over every possible mix."),
      sampled ? h("dl", { class: "kv" },
        h("dt", null, "Valid mixes"), h("dd", null, `${pct(q.feasible_mass, 0)} of measurements (blind guessing: ${pct(q.feasible_baskets / 2 ** q.qubits, 0)})`),
        h("dt", null, "Best mix"), h("dd", null, `${pct(q.p_best, 1)} chance per measurement (blind guessing: ${pct(q.p_best_blind, 1)})`),
        h("dt", null, "Your mix ranks"), h("dd", null, `#${q.rank} of ${q.feasible_baskets.toLocaleString("en-IN")} possible mixes`),
        h("dt", null, "Exact check"), h("dd", null, q.matches_exact ? "Matches the exact classical search." : `The exact search found a slightly better mix: ${q.exact_choice.map(short).join(", ")}.`),
        q.backend ? [h("dt", null, "Run on"), h("dd", null, q.backend + (q.job_id ? ` (job ${q.job_id})` : ""))] : null,
        h("dt", null, "Took"), h("dd", null, `${q.seconds} seconds`)) : null,
      sampled ? h("p", { class: "muted", style: "margin-top:12px" }, "In plain words: at this size the circuit mostly learns which mixes are valid and leans toward good ones. The exact check is what confirms the winner. Quantum computers are not faster than normal computers for a problem this small, so this is a working demonstration of the method, not a speed claim.") : null,
      sampled && top.length ? h("div", null, h("h3", { style: "margin-top:16px" }, "Mixes the circuit produced most often"),
        top.map((s) => h("div", { class: "rowline" }, h("span", null, s.stocks.map(short).join(", ") + (s.feasible ? "" : " (not a valid mix)")), h("span", { class: "num" }, pct(s.prob, 1), " ", h("span", { class: "bar", style: `width:${Math.min(160, s.prob * 1600)}px` }))))) : null,
      q.note ? h("p", { class: "note warn" }, q.note) : null);
  }

  function downloadCsv(R) {
    const rows = [["Stock", "Symbol", "Share of money", "Price", "Shares", "Amount"], ...R.allocation.map((a) => [a.name, a.symbol, (a.weight * 100).toFixed(0) + "%", a.price.toFixed(2), a.shares, a.invest])];
    const blob = new Blob([rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "qportal-mix.csv" }); document.body.append(a); a.click(); a.remove();
  }

  // ---------- history ----------
  async function viewHistory() {
    shell(h("p", { class: "boot" }, "Loading…"), "history");
    let runs = [];
    try { runs = await api("/api/runs"); } catch (ex) { return; }
    const list = h("div");
    const draw = () => list.replaceChildren(...(runs.length ? runs.map((r) => h("div", { class: "rowline" },
      h("div", null, h("b", null, r.title), h("div", { class: "muted" }, new Date(r.created.replace(" ", "T") + "Z").toLocaleString("en-IN"), " · ", r.status === "done" ? "Saved, encrypted" : r.status === "running" ? "Still running" : "Failed")),
      h("div", null, h("a", { class: "btn small", href: "#/result/" + r.id }, "Open"), " ", h("button", { class: "btn danger small", onclick: async () => { await api("/api/runs/" + r.id, { method: "DELETE" }); runs = runs.filter((x) => x.id !== r.id); draw(); } }, "Delete")))) : [h("p", null, "Nothing saved yet. Your results appear here, encrypted, after each analysis.")]));
    draw();
    shell(h("div", null, h("div", { class: "pagehead" }, h("h1", null, "My results"), h("p", null, "Every analysis is stored encrypted with your own key.")), h("div", { class: "panel" }, list), h("a", { class: "btn", href: "#/" }, "Start a new analysis")), "history");
  }

  // ---------- security ----------
  async function viewSecurity() {
    shell(h("p", { class: "boot" }, "Loading…"), "security");
    let st; try { st = await api("/api/security/status"); } catch (ex) { return; }
    const out = h("div"), k = st.key;
    const act = (path, label) => h("button", { class: "btn alt", onclick: async (e) => {
      e.target.disabled = true; out.replaceChildren(h("p", { class: "muted" }, "Running quantum circuits…"));
      try {
        const r = await api(path, { method: "POST" });
        if (path.includes("rotate")) { viewSecurity(); return; }
        out.replaceChildren(h("div", { class: r.status === "ABORTED" ? "note warn" : "note" }, h("b", null, r.status === "ABORTED" ? "Eavesdropper detected. " : "Secure. "),
          `Error rate ${pct(r.qber, 0)} across ${r.raw_qubits} qubits. ${r.reason || ""} Keys are only kept when the error rate stays below ${pct(st.qber_limit, 0)}.`));
      } catch (ex) { out.replaceChildren(h("p", { class: "error" }, ex.message)); }
      e.target.disabled = false; } }, label);
    shell(h("div", null, h("div", { class: "pagehead" }, h("h1", null, "Security"),
      h("p", null, "Your saved results are locked with a key that starts life in quantum circuits. Here is what that means and what it does not.")),
      h("div", { class: "panel" }, h("h2", null, "Your key"),
        k ? h("dl", { class: "kv" }, h("dt", null, "Key number"), h("dd", null, "#" + k.id), h("dt", null, "Created"), h("dd", null, k.created + " UTC"),
          h("dt", null, "Made on"), h("dd", null, k.backend), h("dt", null, "Quantum circuits run"), h("dd", null, `${k.sessions} sessions, ${k.raw_qubits} qubits`),
          h("dt", null, "Secret bits kept"), h("dd", null, `${k.secret_bits} (at least ${st.min_secret_bits} required)`), h("dt", null, "Measured error rate"), h("dd", null, pct(k.qber, 1)),
          h("dt", null, "Results saved with it"), h("dd", null, st.encrypted_runs)) : h("p", null, "No key yet. One is created with your next saved result."),
        h("p", { class: "muted", style: "margin-top:12px" }, `Fresh keys ready in the bank: ${st.bank_ready}.`),
        h("div", { class: "noprint" }, act("/api/security/rotate", "Use a fresh key for new results"), " ", act("/api/security/attack-check", "Test that tampering is detected")), out),
      h("div", { class: "panel" }, h("h2", null, "What this protects"),
        h("p", null, "The key comes from BB84, a method where any eavesdropper disturbs the quantum states and shows up as extra errors. We pool many runs until there are at least 256 secret bits, check that the error rate is safely low, and condense them into one 256-bit key. Each user's key is then mixed with a secret held only by this server."),
        h("p", null, "Your results are stored encrypted (AES-256) with that key. If someone copied the database, they would see unreadable data."),
        h("p", { class: "note warn" }, "Honest limit: the quantum circuits run on the server, so this is not a quantum link between your device and ours. Normal HTTPS still protects data in transit. The quantum part adds quantum-made randomness and a tamper check to the key, and the server secret means the final key is never weaker than standard practice."))), "security");
  }

  // ---------- router ----------
  async function route() {
    killCharts(); setupChartDefaults();
    if (!S.health) { try { S.health = await (await fetch("/api/health")).json(); } catch (e) { S.health = { synthetic: false, max_shortlist: 12, min_shortlist: 3 }; } }
    const [, page, arg] = (location.hash || "#/").split("/");
    if (!S.token) { viewLogin(); return; }
    if (page === "login") { location.hash = "#/"; return; }
    if (page === "comfort") return viewComfort();
    if (page === "result") return viewResult(arg);
    if (page === "history") return viewHistory();
    if (page === "security") return viewSecurity();
    return viewChoose();
  }
  window.addEventListener("hashchange", route);
  route();
})();
