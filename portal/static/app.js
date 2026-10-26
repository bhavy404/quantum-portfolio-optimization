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
    amount: store.get("qp_amount", 100000), engine: "qaoa_sim", mixer: "xy", hwKey: false,
    sort: { key: "ratio", dir: -1 }, filter: { text: "", sector: "", risk: "" },
  };
  let charts = [];
  let timer = null;
  let marketTimer = null;

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
      h("a", { href: "#/home", "aria-current": active === "home" ? "page" : null }, "Market overview"),
      h("a", { href: "#/choose", "aria-current": active === "flow" ? "page" : null }, "Choose stocks"),
      h("a", { href: "#/fundamentals", "aria-current": active === "fundamentals" ? "page" : null }, "Fundamentals"),
      h("a", { href: "#/mutual-funds", "aria-current": active === "mutual-funds" ? "page" : null }, "Mutual funds"),
      h("a", { href: "#/sector-mix", "aria-current": active === "sector-mix" ? "page" : null }, "Sector mix"),
      h("a", { href: "#/history", "aria-current": active === "history" ? "page" : null }, "My results"),
      h("a", { href: "#/security", "aria-current": active === "security" ? "page" : null }, "Security")) : h("div", { class: "nav" });
    const who = S.token ? h("div", { class: "who" }, h("span", null, S.user && (S.user.name || S.user.email)),
      h("button", { class: "btn alt small", onclick: logout }, "Log out")) : null;
    $app.replaceChildren(...[
      S.health && S.health.synthetic ? h("div", { class: "banner", role: "alert" }, "TEST DATA – these prices are made up and only used for testing.") : null,
      h("header", { class: "topbar" }, h("div", { class: "wrap" }, h("a", { class: "brand", href: "#/home" }, h("i"), "QPortal"), nav, who)),
      h("main", { class: "wrap", id: "main" }, content)].filter(Boolean));
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
      form.replaceChildren(...[
        mode === "register" ? h("label", { class: "field" }, h("span", null, "Your name"), h("input", { type: "text", name: "name", autocomplete: "name" })) : null,
        h("label", { class: "field" }, h("span", null, "Email"), h("input", { type: "email", name: "email", autocomplete: "email", required: true })),
        h("label", { class: "field" }, h("span", null, "Password"), h("input", { type: "password", name: "password", autocomplete: mode === "login" ? "current-password" : "new-password", required: true, minLength: 8 }),
          mode === "register" ? h("small", { class: "muted" }, "At least 8 characters.") : null),
        err,
        h("button", { class: "btn", type: "submit", style: "width:100%" }, mode === "login" ? "Log in" : "Create account")].filter(Boolean));
      err.textContent = "";
    }
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form); err.textContent = "";
      const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
      try {
        const out = await api("/api/auth/" + mode, { method: "POST", body: { email: fd.get("email"), password: fd.get("password"), name: fd.get("name") || "" } });
        S.token = out.token; S.user = out.user; store.set("qp_token", out.token); store.set("qp_user", out.user);
        location.hash = "#/home"; route();
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

  // ---------- market home: indices are shown before the stock picker ----------
  async function viewHome() {
    shell(h("p", { class: "boot" }, "Loading market indices…"), "home");
    let data;
    try { data = await api("/api/market-indices"); }
    catch (ex) {
      shell(h("div", { class: "pagehead" }, h("h1", null, "Market overview"), h("p", { class: "error" }, ex.message),
        h("button", { class: "btn", onclick: () => { location.hash = "#/home"; route(); } }, "Try again")), "home");
      return;
    }
    const fmt = (v) => v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
    const fmtChange = (change, changePct) => change == null || changePct == null ? "Waiting for market data" : `${change >= 0 ? "+" : "−"}${Math.abs(change).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} points (${changePct >= 0 ? "+" : "−"}${Math.abs(changePct * 100).toFixed(2)}%)`;
    const cards = (data.indices || []).map((item) => {
      const up = item.change == null || item.change >= 0;
      return h("article", { class: "index-card panel", "data-index-symbol": item.symbol },
        h("div", { class: "index-card-top" }, h("div", null, h("p", { class: "index-eyebrow" }, "MARKET INDEX"), h("h2", null, item.name)),
          h("span", { class: "index-symbol" }, item.symbol)),
        h("p", { class: "index-value num" }, fmt(item.value)),
        h("p", { class: "index-change " + (item.change == null ? "muted" : up ? "gain" : "loss") }, fmtChange(item.change, item.change_pct)),
        h("p", { class: "index-foot muted" }, item.change_pct == null ? "Previous-session change unavailable" : `${item.change_pct >= 0 ? "+" : "−"}${Math.abs(item.change_pct * 100).toFixed(2)}% from previous close`));
    });
    shell(h("div", null,
      h("section", { class: "market-hero" },
        h("p", { class: "eyebrow" }, "INDIAN MARKETS · OVERVIEW"),
        h("h1", null, "Your market snapshot"),
        h("p", null, "Check the broad Indian market indices first. When you’re ready, continue to choose individual stocks for your portfolio.")),
      h("section", { class: "index-grid", id: "market-index-grid", "aria-label": "Indian market indices" }, cards),
      h("div", { class: "market-actions panel" },
        h("div", null, h("h2", null, "Build your portfolio"), h("p", { class: "muted" }, "NIFTY 50 and SENSEX are market indices, shown separately from individual stocks.")),
        h("button", { class: "btn", onclick: () => { location.hash = "#/choose"; } }, "Choose stocks →")),
      h("p", { class: "disclaimer", id: "market-index-disclaimer" }, marketDisclaimer(data)),
      h("p", { class: "index-updated muted", id: "market-index-updated" }, marketUpdatedText(data)),
      h("div", { id: "stock-glance" })), "home");
    try { const sec = await dashboardSection(); const slot = document.getElementById("stock-glance"); if (slot && !slot.hasChildNodes()) { slot.append(sec.node); sec.draw(); } } catch (ex) { /* index cards still work without this section */ }
    if (marketTimer) clearInterval(marketTimer);
    marketTimer = setInterval(refreshMarketCards, 30000);
  }

  async function dashboardSection() {
    let rows = [], runs = [], health = S.health || {};
    try { await loadRows(); rows = allRows().slice(); } catch (ex) { rows = []; }
    try { runs = await api("/api/runs"); } catch (ex) { runs = []; }
    const done = runs.filter(r => r.status === "done");
    const avgReturn = rows.length ? rows.reduce((a,r)=>a+(r.annual_return||0),0)/rows.length : 0;
    const avgVol = rows.length ? rows.reduce((a,r)=>a+(r.volatility||0),0)/rows.length : 0;
    const gainers = rows.filter(r => (r.return_1y||0)>0).length;
    const sorted = rows.slice().sort((a,b)=>(b.annual_return||0)-(a.annual_return||0));
    const search = h("input", { type:"text", placeholder:"Search stocks or symbols…", "aria-label":"Search stocks" });
    const sector = h("select", { "aria-label":"Filter by sector" }, h("option",{value:""},"All sectors"));
    [...new Set(rows.map(r=>r.sector||"Other"))].sort().forEach(v=>sector.append(h("option",{value:v},v)));
    const tableBody = h("tbody");
    let sortKey = "annual_return", sortDir = -1;
    function drawTable() {
      const q=search.value.trim().toLowerCase(), sec=sector.value;
      const filtered=rows.filter(r=>(!sec||r.sector===sec)&&(!q||(r.name+" "+r.symbol+" "+r.sector).toLowerCase().includes(q)))
        .sort((a,b)=>((a[sortKey]??-Infinity)-(b[sortKey]??-Infinity))*sortDir);
      tableBody.replaceChildren(...filtered.slice(0,12).map(r=>h("tr",null,
        h("td",{"data-l":"Stock"},h("span",{class:"nm"},h("b",null,r.name),h("small",null,short(r.symbol)+" · "+r.sector))),
        h("td",{"data-l":"Price"},inr(r.price)),
        h("td",{"data-l":"1Y return",class:signClass(r.return_1y||0)},r.return_1y==null?"—":spct(r.return_1y,1)),
        h("td",{"data-l":"Annual growth",class:signClass(r.annual_return||0)},spct(r.annual_return||0,1)),
        h("td",{"data-l":"Volatility"},pct(r.volatility||0,1)),
        h("td",{"data-l":"Risk"},h("span",{class:"tag "+(r.risk_level||"Medium")},r.risk_level||"Medium")),
        h("td",{"data-l":"Action"},h("button",{class:"btn alt small",onclick:()=>{if(!S.shortlist.includes(r.symbol)){if(S.shortlist.length >= health.max_shortlist){alert(`Shortlist limit is ${health.max_shortlist}. Remove a stock on Pick stocks first.`);return;}S.shortlist.push(r.symbol);saveShort();}location.hash="#/choose";}},S.shortlist.includes(r.symbol)?"In shortlist":"Add +"))
      )));
      if(!filtered.length)tableBody.append(h("tr",null,h("td",{colSpan:7},"No stocks match these filters.")));
    }
    search.addEventListener("input",drawTable); sector.addEventListener("change",drawTable);
    const table=h("table",{class:"stocks dashboard-stocks"},h("thead",null,h("tr",null,
      ...[["Stock","name"],["Price","price"],["1Y return","return_1y"],["Annual growth","annual_return"],["Volatility","volatility"],["Risk","risk_level"]].map(([label,key])=>h("th",null,h("button",{type:"button",onclick:()=>{if(sortKey===key)sortDir*=-1;else{sortKey=key;sortDir=-1;}drawTable();}},label+" ↕"))),h("th",null,"Action"))),tableBody);
    drawTable();
    const chartCanvas=h("canvas",{role:"img","aria-label":"Risk versus return chart for the available stock universe"});
    const sectorCanvas=h("canvas",{role:"img","aria-label":"Average annual return by sector"});
    const recent=h("div",{class:"recent-list"});
    recent.replaceChildren(...(runs.length?runs.slice(0,4).map(r=>h("div",{class:"rowline"},h("div",null,h("b",null,r.title||("Analysis #"+r.id)),h("div",{class:"muted"},r.status+" · "+new Date(String(r.created).replace(" ","T")+"Z").toLocaleDateString("en-IN"))),h("a",{class:"btn alt small",href:"#/result/"+r.id},"Open result"))):[h("p",{class:"muted"},"No saved analyses yet. Run your first portfolio optimization to see results here.")]));
    const sectorMap={}; rows.forEach(r=>{const k=r.sector||"Other";(sectorMap[k] ||= []).push(r.annual_return||0);});
    const sectorLabels=Object.keys(sectorMap).sort();
    const node=h("div",{class:"dashboard-page"},
      h("div",{class:"dashboard-heading"},h("div",null,h("p",{class:"eyebrow"},"STOCKS AT A GLANCE"),h("h2",null,"Your stock universe"),h("p",{class:"muted"},"Growth, risk and sectors across the tracked Indian stocks.")),h("div",{class:"dashboard-actions"},h("a",{class:"btn alt",href:"#/history"},"My results"))),
      h("div",{class:"dashboard-kpis"},
        h("article",{class:"kpi-card"},h("div",{class:"kpi-top"},h("span",null,"Stocks tracked"),h("span",{class:"kpi-icon"},"◉")),h("strong",null,rows.length.toLocaleString("en-IN")),h("small",null,"Across the core universe")),
        h("article",{class:"kpi-card"},h("div",{class:"kpi-top"},h("span",null,"Average annual growth"),h("span",{class:"kpi-icon green"},"↗")),h("strong",{class:signClass(avgReturn)},spct(avgReturn,1)),h("small",null,"Historical mean of tracked stocks")),
        h("article",{class:"kpi-card"},h("div",{class:"kpi-top"},h("span",null,"Average volatility"),h("span",{class:"kpi-icon amber"},"≈")),h("strong",null,pct(avgVol,1)),h("small",null,"Annualized historical variability")),
        h("article",{class:"kpi-card"},h("div",{class:"kpi-top"},h("span",null,"Saved analyses"),h("span",{class:"kpi-icon purple"},"⌁")),h("strong",null,runs.length),h("small",null,done.length+" completed · encrypted results"))),
      h("div",{class:"dashboard-grid"},
        h("section",{class:"panel dash-panel dash-wide"},h("div",{class:"panel-heading"},h("div",null,h("h2",null,"Risk vs. return"),h("p",{class:"muted"},"Compare historical annual growth with volatility. Each dot is a stock.")),h("span",{class:"mini-label"},"HISTORICAL")),h("div",{class:"chartbox dashboard-chart"},chartCanvas),h("div",{class:"chart-foot"},h("span",null,"● Growth per year vs. typical ups and downs"),h("span",null,"Past performance is not a forecast"))),
        h("section",{class:"panel dash-panel"},h("div",{class:"panel-heading"},h("div",null,h("h2",null,"Sector snapshot"),h("p",{class:"muted"},"Average annual return by sector"))),h("div",{class:"chartbox dashboard-sector-chart"},sectorCanvas)),
        h("section",{class:"panel dash-panel dash-wide"},h("div",{class:"panel-heading stock-heading"},h("div",null,h("h2",null,"Stock explorer"),h("p",{class:"muted"},"Search, sort and add a stock to your optimization shortlist.")),h("span",{class:"mini-label"},"TOP 12 MATCHES")),h("div",{class:"toolbar dashboard-toolbar"},h("label",{class:"field"},h("span",null,"Search"),search),h("label",{class:"field"},h("span",null,"Sector"),sector)),h("div",{class:"table-scroll"},table)),
        h("section",{class:"panel dash-panel"},h("div",{class:"panel-heading"},h("div",null,h("h2",null,"Recent analyses"),h("p",{class:"muted"},"Jump back into saved portfolio results")),h("a",{href:"#/history"},"View all →")),recent),
        h("section",{class:"panel dash-panel quick-panel"},h("div",{class:"panel-heading"},h("div",null,h("h2",null,"Quick actions"),h("p",{class:"muted"},"Continue where you left off"))),h("a",{class:"quick-action",href:"#/choose"},h("span",{class:"quick-icon"},"⌕"),h("span",null,h("b",null,"Explore stocks"),h("small",null,"Filter the universe and build a shortlist")),h("span",null,"→")),h("a",{class:"quick-action",href:"#/history"},h("span",{class:"quick-icon"},"▤"),h("span",null,h("b",null,"Saved results"),h("small",null,"Open or delete past analyses")),h("span",null,"→")),h("a",{class:"quick-action",href:"#/security"},h("span",{class:"quick-icon"},"◇"),h("span",null,h("b",null,"Security centre"),h("small",null,"Inspect key status and test tampering detection")),h("span",null,"→"))),
      h("p",{class:"disclaimer"},"Data is historical and may be delayed. Statistics are calculated from the available price history and are not investment advice. Verify prices and suitability independently.")));
    const draw=()=>{
    const pts=rows.map(r=>({x:(r.volatility||0)*100,y:(r.annual_return||0)*100,label:short(r.symbol)}));
    charts.push(new Chart(chartCanvas,{type:"scatter",data:{datasets:[{label:"Stocks",data:pts,backgroundColor:"rgba(61,139,245,.72)",borderColor:"#3987E5",pointRadius:4,pointHoverRadius:7}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>`${c.raw.label}: growth ${c.raw.y.toFixed(1)}%, volatility ${c.raw.x.toFixed(1)}%`}}},scales:{x:{title:{display:true,text:"Annualized volatility (%)"},grid:{color:css("--line")}},y:{title:{display:true,text:"Annual growth (%)"},grid:{color:css("--line")}}}}}));
    charts.push(new Chart(sectorCanvas,{type:"bar",data:{labels:sectorLabels,datasets:[{label:"Average annual return",data:sectorLabels.map(k=>sectorMap[k].reduce((a,b)=>a+b,0)/sectorMap[k].length*100),backgroundColor:sectorLabels.map(k=>(sectorMap[k].reduce((a,b)=>a+b,0)/sectorMap[k].length)>=0?"#3987E5":"#E66B66"),borderRadius:5}]},options:{indexAxis:"y",responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.raw.toFixed(1)+"%"}}},scales:{x:{ticks:{callback:v=>v+"%"},grid:{color:css("--line")}},y:{grid:{display:false},ticks:{autoSkip:false}}}}}));
    };
    return {node,draw};
  }

  function marketDisclaimer(data) {
    const mode = data && data.mode;
    if (mode === "intraday") return "Near-live 1-minute index data when available. Yahoo Finance may delay or omit quotes; this is not a guaranteed real-time feed. Cards refresh every 30 seconds while this page is open.";
    if (mode === "mixed") return "Some index quotes are intraday and others use the latest daily close. Yahoo Finance may delay or omit quotes. Cards refresh every 30 seconds while this page is open.";
    return "Showing the latest available daily close because intraday index quotes are unavailable. The app retries every 30 seconds; Yahoo Finance is not a guaranteed real-time feed.";
  }

  function marketUpdatedText(data) {
    if (data && data.as_of) return "Latest quote timestamp: " + new Date(data.as_of).toLocaleString();
    return "Market data timestamp unavailable.";
  }

  async function refreshMarketCards() {
    if (!location.hash.startsWith("#/home") && location.hash !== "#/") return;
    try {
      const data = await api("/api/market-indices");
      const grid = document.getElementById("market-index-grid");
      if (!grid) return;
      const fmt = (v) => v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
      const fmtChange = (change, changePct) => change == null || changePct == null ? "Change unavailable (daily close / no intraday quote)" : `${change >= 0 ? "+" : "−"}${Math.abs(change).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} points (${changePct >= 0 ? "+" : "−"}${Math.abs(changePct * 100).toFixed(2)}%)`;
      for (const item of (data.indices || [])) {
        const card = [...grid.querySelectorAll("[data-index-symbol]")].find((el) => el.getAttribute("data-index-symbol") === item.symbol);
        if (!card) continue;
        const value = card.querySelector(".index-value");
        const change = card.querySelector(".index-change");
        const foot = card.querySelector(".index-foot");
        if (value) value.textContent = fmt(item.value);
        if (change) {
          change.textContent = fmtChange(item.change, item.change_pct);
          change.className = "index-change " + (item.change == null ? "muted" : item.change >= 0 ? "gain" : "loss");
        }
        if (foot) foot.textContent = item.quote_type === "intraday" && item.quote_timestamp
          ? "Intraday quote: " + new Date(item.quote_timestamp).toLocaleTimeString()
          : "Latest daily close; intraday quote unavailable";
      }
      const updated = document.getElementById("market-index-updated");
      if (updated) updated.textContent = marketUpdatedText(data);
      const disclaimer = document.getElementById("market-index-disclaimer");
      if (disclaimer) disclaimer.textContent = marketDisclaimer(data);
    } catch (e) {
      const updated = document.getElementById("market-index-updated");
      if (updated) updated.textContent = "Could not refresh index quotes. Retrying in 30 seconds.";
    }
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
        const r = await api("/api/recommend", { method: "POST", body: { symbols: S.shortlist, k: S.k, q: S.q, amount: S.amount, engine: S.engine, mixer: S.mixer, hw_key: !!(S.hwKey && S.health.ibm_configured) } });
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
            h("label", { class: "field" }, h("span", null, "Circuit type"), h("select", { onchange: (e) => { S.mixer = e.target.value; } },
              h("option", { value: "xy" }, "Valid mixes only (recommended)"), h("option", { value: "x" }, "Standard QAOA (shallower, good for real hardware)")),
              h("small", { class: "muted" }, "The first builds only mixes with your chosen number of stocks and does far better, but its circuit is deeper. On a real quantum processor, noise grows with depth, so use 8 stocks or fewer there, or pick Standard.")),
            S.health.ibm_configured ? h("label", { class: "opt", style: "margin:6px 0 10px" }, h("input", { type: "checkbox", checked: S.hwKey, onchange: (e) => { S.hwKey = e.target.checked; } }),
              h("span", null, "Protect this result with a fresh quantum key made on the IBM quantum processor (adds a few minutes)")) : null,
            err, h("button", { class: "btn", style: "width:100%", onclick: run }, "Analyse my shortlist"))))), "flow");
    makeChart();
    preview();
  }

  // ---------- result ----------
  async function viewResult(id) {
    shell(h("div", { class: "waiting" }, h("div", { class: "spinner" }), h("h2", null, "Analysing your shortlist"), h("p", { class: "muted" }, S.hwKey && S.health.ibm_configured ? "Making a quantum key on the IBM processor can take a few minutes." : "This usually takes under a minute.")), "flow");
    const t0 = Date.now();
    const poll = async () => {
      let r;
      try { r = await api("/api/runs/" + id); } catch (ex) { shell(h("div", { class: "panel" }, h("h2", null, "We could not open this result"), h("p", { class: "error" }, ex.message), h("a", { class: "btn", href: "#/" }, "Back to stocks")), "flow"); return; }
      if (r.status === "running") {
        const s = Math.round((Date.now() - t0) / 1000);
        const p = document.querySelector(".waiting .muted"); if (p) p.textContent = (r.stage ? r.stage + ". " : "") + `${s}s so far. ` + (S.hwKey || S.engine === "qaoa_hw" ? "Real quantum computers have a queue, so this can take a few minutes. It stops waiting after 4 minutes and falls back to the simulator." : "This usually takes under a minute.");
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
      protect(R),
      h("p", { class: "disclaimer" }, `Equal amounts in each stock, rebalanced daily. Growth is the average yearly gain, ups and downs is yearly volatility. The Nifty 50 line uses the index, not an investable fund. Picks use the first 70% of days (to ${R.data.train_until}); the last 30% (from ${R.data.test_from}) are shown separately. Past results do not predict future results. This is an educational tool, not investment advice.`),
      h("div", { class: "noprint" }, h("a", { class: "btn alt", href: "#/choose" }, "Change my shortlist"), " ", h("a", { class: "btn alt", href: "#/history" }, "My saved results"))), "flow");

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

  function protect(R) {
    const k = R.key;
    if (!k || !k.source) return null;
    const hw = k.source === "hardware";
    return h("details", { class: "panel" }, h("summary", null, "How this result is protected"),
      h("p", null, hw
        ? `This result is encrypted with a key built from BB84 quantum key exchange run on a real IBM quantum processor (${k.backend}).${k.fresh_for_this_result ? " The key was made fresh for this result." : ""}`
        : "This result is encrypted with a key built from BB84 quantum key exchange run on a quantum circuit simulator."),
      R.key.fallback_reason ? h("p", { class: "note warn" }, `We could not make a fresh IBM key for this result (${R.key.fallback_reason}), so your regular saved key was used instead.`) : null,
      h("dl", { class: "kv" },
        h("dt", null, "Key made on"), h("dd", null, k.backend),
        k.job_ids && k.job_ids.length ? [h("dt", null, "IBM job"), h("dd", null, k.job_ids.join(", "))] : null,
        h("dt", null, "Qubits sent"), h("dd", null, `${k.raw_qubits} in ${k.sessions} sessions`),
        h("dt", null, "Secret bits kept"), h("dd", null, String(k.secret_bits)),
        h("dt", null, "Error rate seen"), h("dd", null, pct(k.qber, 1) + " (keys are rejected above 11%)")),
      h("p", { class: "muted", style: "margin-top:12px" }, "Honest limit: the sender and receiver in the key exchange are both this server, so it is not a quantum link to your device. The final encryption key also mixes in a server secret, and your result is stored encrypted with AES-256."));
  }

  function how(R) {
    const q = R.quantum, sampled = q.engine !== "classical";
    const top = q.samples.slice(0, 6);
    return h("details", { class: "panel" }, h("summary", null, "How this was chosen"),
      h("p", null, sampled
        ? `A quantum-computing method called QAOA (${q.label.toLowerCase().includes("ibm") ? "run on an IBM quantum processor" : "run on a simulator"}) searched all ways to pick ${R.params.k} of your ${q.qubits} stocks. It uses ${q.qubits} qubits${q.circuit_depth ? ` and a circuit about ${q.circuit_depth} steps deep` : ""}. ${q.mixer === "xy" ? "The circuit is built so it only ever considers mixes with exactly the number of stocks you asked for. " : ""}It was measured ${q.shots.toLocaleString("en-IN")} times, and we kept the lowest-cost valid mix it produced.`
        : "This result used an exact classical search over every possible mix."),
      sampled ? h("dl", { class: "kv" },
        h("dt", null, "Valid mixes"), h("dd", null, q.mixer === "xy" && !q.job_id ? `${pct(q.feasible_mass, 0)} of measurements (by design: this circuit only builds valid mixes)` : `${pct(q.feasible_mass, 0)} of measurements (blind guessing: ${pct(q.feasible_baskets / 2 ** q.qubits, 0)})`),
        h("dt", null, "Best mix"), h("dd", null, `${pct(q.p_best, 1)} chance per measurement (blind guessing: ${pct(q.p_best_blind, 1)})`),
        h("dt", null, "Your mix ranks"), h("dd", null, `#${q.rank} of ${q.feasible_baskets.toLocaleString("en-IN")} possible mixes`),
        h("dt", null, "Exact check"), h("dd", null, q.matches_exact ? "Matches the exact classical search." : `The exact search found a slightly better mix: ${q.exact_choice.map(short).join(", ")}.`),
        q.backend ? [h("dt", null, "Run on"), h("dd", null, q.backend + (q.job_id ? ` (job ${q.job_id})` : ""))] : null,
        h("dt", null, "Took"), h("dd", null, `${q.seconds} seconds`)) : null,
      sampled ? h("p", { class: "muted", style: "margin-top:12px" }, (q.mixer === "xy" ? "In plain words: the circuit only builds valid mixes and leans toward the better ones. Compare the best-mix chance above with blind guessing to see what the circuit adds. The exact check is what confirms the winner." : "In plain words: at this size the circuit mostly learns which mixes are valid and leans toward good ones. The exact check is what confirms the winner.") + " Quantum computers are not faster than normal computers for a problem this small, so this is a working demonstration of the method, not a speed claim.") : null,
      sampled && top.length ? h("div", null, h("h3", { style: "margin-top:16px" }, "Mixes the circuit produced most often"),
        top.map((s) => h("div", { class: "rowline" }, h("span", null, s.stocks.map(short).join(", ") + (s.feasible ? "" : " (not a valid mix)")), h("span", { class: "num" }, pct(s.prob, 1), " ", h("span", { class: "bar", style: `width:${Math.min(160, s.prob * 1600)}px` }))))) : null,
      q.note ? h("p", { class: "note warn" }, q.note) : null);
  }

  function downloadCsv(R) {
    const rows = [["Stock", "Symbol", "Share of money", "Price", "Shares", "Amount"], ...R.allocation.map((a) => [a.name, a.symbol, (a.weight * 100).toFixed(0) + "%", a.price.toFixed(2), a.shares, a.invest])];
    const blob = new Blob([rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "qportal-mix.csv" }); document.body.append(a); a.click(); a.remove();
  }

  // ---------- mutual funds: search, compare NAV history, and estimate SIP value ----------
  async function viewMutualFunds() {
    let selected = [];
    let searchTimer = null;
    let catalogRows = [];
    const searchInput = h("input", { type: "search", placeholder: "Filter by fund or scheme name (e.g. HDFC Flexi Cap)", "aria-label": "Search mutual funds in supplied dataset" });
    const categoryFilter = h("select", { "aria-label": "Filter mutual-fund category" }, h("option", { value: "" }, "All categories"));
    const amcFilter = h("select", { "aria-label": "Filter mutual-fund fund house" }, h("option", { value: "" }, "All fund houses"));
    const typeFilter = h("select", { "aria-label": "Filter mutual-fund scheme type" }, h("option", { value: "" }, "All scheme types"));
    const schemeSelect = h("select", { "aria-label": "Select a mutual-fund scheme" }, h("option", { value: "" }, "Loading dataset…"));
    const catalogStatus = h("p", { class: "muted" }, "Loading the supplied mutual-fund dataset…");
    const resultsBox = h("div", { class: "mf-search-results" });
    const chips = h("div", { class: "chips" });
    const tableWrap = h("div", { class: "table-wrap" });
    const status = h("p", { class: "muted" }, "Select up to five schemes to compare.");
    const searchError = h("p", { class: "error" });
    const fmt = (v, digits = 2) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
    const fmtReturn = (v) => v == null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(2) + "%";
    function drawChips() {
      chips.replaceChildren(...selected.map((x) => h("span", { class: "chip" }, x.scheme_name,
        h("button", { type: "button", "aria-label": "Remove " + x.scheme_name, onclick: () => { selected = selected.filter((f) => f.scheme_code !== x.scheme_code); drawChips(); updateSchemeOptions(); } }, "×"))));
    }
    function updateSchemeOptions() {
      const current = schemeSelect.value;
      const options = catalogRows.map((fund) => {
        const nav = fund.latest_nav == null ? "NAV unavailable in snapshot" : "NAV ₹" + fmt(fund.latest_nav, 4);
        const label = `${fund.display_name || fund.scheme_name} · ${fund.scheme_category || fund.scheme_type || "Unclassified"} · ${nav}`;
        return h("option", { value: String(fund.scheme_code), disabled: selected.some((x) => x.scheme_code === fund.scheme_code) }, label);
      });
      schemeSelect.replaceChildren(h("option", { value: "" }, catalogRows.length ? "Choose a mutual-fund scheme…" : "No schemes match these filters"), ...options);
      if (catalogRows.some((r) => String(r.scheme_code) === current && !selected.some((x) => String(x.scheme_code) === current))) schemeSelect.value = current;
    }
    let filtersLoaded = false;
    async function loadCatalog() {
      catalogStatus.textContent = "Filtering the supplied dataset…";
      searchError.textContent = "";
      const params = new URLSearchParams({
        q: searchInput.value.trim(),
        category: categoryFilter.value,
        amc: amcFilter.value,
        scheme_type: typeFilter.value,
        limit: "500"
      });
      try {
        const data = await api("/api/mutual-fund-catalog?" + params.toString());
        if (!filtersLoaded) {
          categoryFilter.replaceChildren(h("option", { value: "" }, "All categories"), ...(data.categories || []).map((x) => h("option", { value: x }, x)));
          amcFilter.replaceChildren(h("option", { value: "" }, "All fund houses"), ...(data.amcs || []).map((x) => h("option", { value: x }, x)));
          typeFilter.replaceChildren(h("option", { value: "" }, "All scheme types"), ...(data.scheme_types || []).map((x) => h("option", { value: x }, x)));
          filtersLoaded = true;
        }
        catalogRows = data.results || [];
        updateSchemeOptions();
        catalogStatus.textContent = `${data.total.toLocaleString("en-IN")} schemes match these filters; showing up to ${catalogRows.length}. Dataset NAV snapshot: ${data.nav_snapshot_date || "date unavailable"}.`;
      } catch (ex) {
        catalogRows = [];
        updateSchemeOptions();
        catalogStatus.textContent = "Could not load the supplied mutual-fund dataset.";
        searchError.textContent = ex.message;
      }
    }
    searchInput.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadCatalog, 250); });
    categoryFilter.addEventListener("change", loadCatalog);
    amcFilter.addEventListener("change", loadCatalog);
    typeFilter.addEventListener("change", loadCatalog);
    const addSchemeBtn = h("button", { class: "btn", onclick: () => {
      const fund = catalogRows.find((x) => String(x.scheme_code) === schemeSelect.value);
      if (!fund) { catalogStatus.textContent = "Choose a scheme from the dropdown first."; return; }
      if (selected.length >= 5) { catalogStatus.textContent = "You can compare up to five schemes at a time."; return; }
      if (!selected.some((x) => x.scheme_code === fund.scheme_code)) {
        selected.push({ scheme_code: fund.scheme_code, scheme_name: fund.display_name || fund.scheme_name });
        drawChips(); updateSchemeOptions();
      }
    } }, "Add selected scheme");
    const compareBtn = h("button", { class: "btn alt", onclick: loadComparison }, "Load / refresh comparison");
    async function loadComparison() {
      if (!selected.length) { status.textContent = "Select at least one fund from the dropdown first."; return; }
      compareBtn.disabled = true; compareBtn.textContent = "Loading NAV history…";
      status.textContent = "Fetching NAV history and calculating trailing returns. This may take a moment.";
      try {
        const details = await Promise.all(selected.map((fund) => api("/api/mutual-funds/" + fund.scheme_code).catch((e) => ({ scheme_code: fund.scheme_code, scheme_name: fund.scheme_name, error: e.message }))));
        const table = h("table", { class: "stocks fundamentals-table" },
          h("thead", null, h("tr", null, ...["Fund / scheme", "Latest NAV", "NAV date", "1-year return", "3-year CAGR", "5-year CAGR", "Category"].map((x) => h("th", null, x)))),
          h("tbody", null, ...details.map((fund) => h("tr", null,
            h("td", { class: "nm", "data-l": "Fund / scheme" }, h("b", null, fund.scheme_name || "Fund"), h("small", null, fund.fund_house || (fund.error ? fund.error : "Scheme " + fund.scheme_code))),
            h("td", { class: "num", "data-l": "Latest NAV" }, fund.latest_nav == null ? "—" : "₹" + fmt(fund.latest_nav, 4)),
            h("td", { class: "num", "data-l": "NAV date" }, fund.nav_date || "—"),
            h("td", { class: "num " + signClass(fund.returns?.["1y"] || 0), "data-l": "1-year return" }, fmtReturn(fund.returns?.["1y"])),
            h("td", { class: "num " + signClass(fund.returns?.["3y_cagr"] || 0), "data-l": "3-year CAGR" }, fmtReturn(fund.returns?.["3y_cagr"])),
            h("td", { class: "num " + signClass(fund.returns?.["5y_cagr"] || 0), "data-l": "5-year CAGR" }, fmtReturn(fund.returns?.["5y_cagr"])),
            h("td", { "data-l": "Category" }, fund.scheme_category || fund.scheme_type || "—")))));
        tableWrap.replaceChildren(table);
        status.textContent = "Returns are calculated from reported NAV history, before tax and investor-specific costs. Historical returns do not predict future performance.";
      } catch (ex) { status.textContent = ex.message; }
      finally { compareBtn.disabled = false; compareBtn.textContent = "Load / refresh comparison"; }
    }
    const monthly = h("input", { type: "number", min: 500, step: 500, value: 5000, "aria-label": "Monthly SIP amount" });
    const years = h("input", { type: "number", min: 1, max: 40, value: 10, "aria-label": "SIP duration in years" });
    const rate = h("input", { type: "number", min: 0, max: 30, step: 0.5, value: 12, "aria-label": "Assumed annual return percentage" });
    const sipResult = h("div", { class: "sip-result muted" }, "Enter your assumptions and calculate an illustrative SIP value.");
    const sipBtn = h("button", { class: "btn alt", onclick: () => {
      const p = Math.max(0, Number(monthly.value) || 0), n = Math.min(40, Math.max(1, Number(years.value) || 1)) * 12, r = Math.max(0, Number(rate.value) || 0) / 1200;
      const invested = p * n; const future = r ? p * ((Math.pow(1 + r, n) - 1) / r) * (1 + r) : invested;
      sipResult.className = "sip-result"; sipResult.replaceChildren(h("div", null, h("span", null, "Invested"), h("b", null, inr(invested))), h("div", null, h("span", null, "Illustrative value"), h("b", null, inr(future))), h("div", null, h("span", null, "Illustrative gain"), h("b", null, inr(future - invested))));
    } }, "Calculate SIP estimate");
    shell(h("div", null,
      h("section", { class: "pagehead" }, h("p", { class: "eyebrow" }, "INVESTMENT RESEARCH"), h("h1", null, "Mutual fund explorer"), h("p", null, "Browse your supplied dataset using category, fund-house, and scheme-type dropdowns. Compare historical NAV-based returns and estimate a monthly SIP.")),
      h("section", { class: "panel" }, h("h2", null, "1. Browse and select funds"), h("label", { class: "field" }, h("span", null, "Search dataset"), searchInput),
        h("div", { class: "sip-inputs" }, h("label", { class: "field" }, h("span", null, "Category"), categoryFilter), h("label", { class: "field" }, h("span", null, "Fund house / AMC"), amcFilter), h("label", { class: "field" }, h("span", null, "Scheme type"), typeFilter)),
        h("label", { class: "field" }, h("span", null, "Select scheme"), schemeSelect), addSchemeBtn, catalogStatus, searchError,
        h("p", { class: "muted" }, "Selected funds (up to 5)"), chips, compareBtn, status, tableWrap),
      h("section", { class: "panel" }, h("h2", null, "2. SIP calculator"), h("p", { class: "muted" }, "This is a mathematical illustration, not a return forecast or recommendation."),
        h("div", { class: "sip-inputs" }, h("label", { class: "field" }, h("span", null, "Monthly investment (₹)"), monthly), h("label", { class: "field" }, h("span", null, "Years"), years), h("label", { class: "field" }, h("span", null, "Assumed annual return (%)"), rate)), sipBtn, sipResult),
      h("p", { class: "disclaimer" }, "Dataset fields include scheme, AMC, category, NAV snapshot, minimum amount and AUM. NAV snapshot dates may be older than today's NAV; comparison fetches historical NAV from MFAPI / reported AMFI data when available. Expense ratios, exit loads, taxes and direct-vs-regular plan costs are not included. Verify scheme documents before investing.")), "mutual-funds");
    loadCatalog();
  }

  // ---------- sector mix builder ----------
  async function viewSectorMix() {
    shell(h("div", { class: "waiting" }, h("div", { class: "spinner" }), h("p", null, "Loading sector performance…")), "sector-mix");
    let data;
    try { data = await api("/api/sector-recommendations"); }
    catch (ex) { shell(h("section", { class: "panel" }, h("h1", null, "Sector mix builder"), h("p", { class: "error" }, ex.message)), "sector-mix"); return; }
    const amount = h("input", { type: "number", min: 1000, step: 1000, value: 100000, "aria-label": "Portfolio amount in rupees" });
    const selected = new Set();
    const sectorsWrap = h("div", { class: "sector-choice-grid" });
    const result = h("div", { class: "sector-results" });
    const note = h("p", { class: "muted" }, "The website calculates suggested weights from historical sector-stock performance. You can choose which sectors to include; weights are recalculated automatically.");
    const fmtGrowth = (v) => v == null || !Number.isFinite(Number(v)) ? "Not enough data" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1) + "%";
    const formatSuggested = (v) => (v * 100).toFixed(1) + "%";
    const byName = new Map((data.sectors || []).map((x) => [x.name, x]));
    const rankedSectors = [...(data.sectors || [])].sort((a, b) => (b.momentum ?? -Infinity) - (a.momentum ?? -Infinity));
    // Start with up to five sectors with the strongest available observed momentum.
    rankedSectors.slice(0, Math.min(5, rankedSectors.length)).forEach((x) => selected.add(x.name));
    function allocation() {
      const chosen = [...selected].map((name) => byName.get(name)).filter(Boolean);
      const totalScore = chosen.reduce((sum, x) => sum + (Number(x.score) || 0), 0);
      return chosen.map((sector) => ({ ...sector, weight: totalScore ? sector.score / totalScore : 1 / Math.max(1, chosen.length) }));
    }
    function build() {
      const chosen = allocation();
      if (chosen.length < 2) { note.textContent = "Choose at least two sectors. QPortal will suggest their relative weights automatically."; result.replaceChildren(); return; }
      const invested = Math.max(0, Number(amount.value) || 0);
      const rows = [];
      const sectorCards = chosen.map((sector) => {
        const sectorMoney = invested * sector.weight;
        const stocks = (sector.stocks || []).slice(0, 4);
        const perStock = stocks.length ? sectorMoney / stocks.length : 0;
        stocks.forEach((stock) => rows.push({ ...stock, sector: sector.name, weight: sector.weight / Math.max(1, stocks.length), amount: perStock }));
        return h("article", { class: "sector-result-card" },
          h("div", { class: "sector-result-head" }, h("h3", null, sector.name), h("b", null, formatSuggested(sector.weight))),
          h("p", { class: "muted" }, inr(sectorMoney) + " suggested allocation"),
          h("p", { class: "muted" }, "3-month growth: " + fmtGrowth(sector.return_3m) + " · 1-year growth: " + fmtGrowth(sector.return_1y)),
          h("p", { class: "sector-stock-list" }, stocks.map((x) => x.name).join(" · ") || "No starter stocks available"),
          h("div", { class: "bar-track" }, h("span", { class: "bar", style: "width:" + (sector.weight * 100) + "%" })));
      });
      const table = h("table", { class: "stocks" }, h("thead", null, h("tr", null, ...["Sector", "Example stock", "Portfolio weight", "Illustrative allocation"].map((x) => h("th", null, x)))),
        h("tbody", null, ...rows.map((r) => h("tr", null, h("td", { "data-l": "Sector" }, r.sector), h("td", { "data-l": "Example stock" }, h("b", null, r.name), h("small", null, r.symbol.replace(".NS", ""))), h("td", { class: "num", "data-l": "Portfolio weight" }, (r.weight * 100).toFixed(2) + "%"), h("td", { class: "num", "data-l": "Illustrative allocation" }, inr(r.amount))))));
      result.replaceChildren(
        h("div", { class: "tiles" }, h("div", { class: "tile" }, h("b", null, inr(invested)), h("span", null, "Portfolio amount")), h("div", { class: "tile" }, h("b", null, chosen.length), h("span", null, "Sectors included")), h("div", { class: "tile" }, h("b", null, rows.length), h("span", null, "Example stocks")),
        h("div", { class: "tile" }, h("b", null, data.synthetic ? "Synthetic" : "Historical"), h("span", null, "Price data mode"))),
        h("div", { class: "sector-result-grid" }, sectorCards), h("h2", null, "Illustrative stock allocation"), table);
      note.textContent = "Suggested weights are normalized to 100% among selected sectors. They use 35% 3-month and 65% 1-year average price performance; up to four starter stocks per sector are shown with equal weights within that sector. This is a historical-momentum heuristic, not a forecast or optimized portfolio.";
    }
    for (const sector of data.sectors || []) {
      const checked = h("input", { type: "checkbox", checked: selected.has(sector.name), "aria-label": "Include " + sector.name, onchange: (event) => {
        if (event.target.checked) selected.add(sector.name); else selected.delete(sector.name);
        build();
      } });
      const suggested = sector.suggested_weight == null ? "—" : formatSuggested(sector.suggested_weight);
      sectorsWrap.append(h("label", { class: "sector-choice" },
        h("div", { class: "sector-choice-title" }, checked, h("b", null, sector.name), h("small", null, (sector.stock_count || 0) + " starter stocks")),
        h("span", null, "Historical growth · 3M: " + fmtGrowth(sector.return_3m) + " · 1Y: " + fmtGrowth(sector.return_1y)),
        h("span", null, "Suggested weight across all sectors: " + suggested)));
    }
    const buildBtn = h("button", { class: "btn", onclick: build }, "Refresh suggested combination");
    amount.addEventListener("input", build);
    shell(h("div", null,
      h("section", { class: "pagehead" }, h("p", { class: "eyebrow" }, "DIVERSIFICATION WORKSPACE"), h("h1", null, "Auto-weighted sector portfolio"), h("p", null, "QPortal suggests sector weights from available historical stock-price growth. Select or deselect sectors; the site automatically redistributes weights across your chosen combination.")),
      h("section", { class: "panel" }, h("h2", null, "1. Set portfolio size"), h("label", { class: "field" }, h("span", null, "Portfolio amount (₹)"), amount),
        h("h2", null, "2. Choose sectors (weights are automatic)"), sectorsWrap, buildBtn, note),
      h("section", { class: "panel" }, h("h2", null, "3. Suggested sector combination"), result),
      h("p", { class: "disclaimer" }, (data.method || "") + " " + (data.note || "")),), "sector-mix");
    build();
  }

  // ---------- fundamentals and fundamental stock ranker ----------
  async function viewFundamentals() {
    let selected = [];
    let rows = [];
    let searchTimer = null;
    let ranked = [];
    const status = h("p", { class: "muted" }, "Search for companies, add up to eight, then load their reported fundamentals.");
    const searchInput = h("input", { type: "search", placeholder: "Search company or NSE symbol (e.g. TCS, Infosys)", "aria-label": "Search companies" });
    const resultsBox = h("div");
    const chips = h("div", { class: "chips" });
    const tableWrap = h("div", { class: "table-wrap" });
    const rankWrap = h("div", { class: "table-wrap" });
    const profileDefinitions = {
      balanced: { label: "Balanced fundamentals", weights: { profitability: 30, growth: 25, valuation: 20, financial_health: 15, dividends: 10 } },
      quality: { label: "Quality & financial strength", weights: { profitability: 40, growth: 15, valuation: 10, financial_health: 25, dividends: 10 } },
      growth: { label: "Growth stocks", weights: { profitability: 25, growth: 40, valuation: 15, financial_health: 15, dividends: 5 } },
      value: { label: "Value stocks", weights: { profitability: 20, growth: 15, valuation: 40, financial_health: 20, dividends: 5 } },
      dividend: { label: "Dividend stocks", weights: { profitability: 20, growth: 10, valuation: 10, financial_health: 20, dividends: 40 } },
      custom: { label: "Custom weights", weights: { profitability: 30, growth: 25, valuation: 20, financial_health: 15, dividends: 10 } },
    };
    const categories = [
      ["profitability", "Profitability"], ["growth", "Growth"], ["valuation", "Valuation"],
      ["financial_health", "Financial health"], ["dividends", "Dividends"]
    ];
    const metricLabels = { pe_ratio: "P/E ratio", eps: "EPS", pb_ratio: "P/B ratio", roe: "ROE", roce: "ROCE", debt_to_equity: "Debt-to-equity", revenue_growth: "Revenue growth", net_profit_margin: "Net profit margin", dividend_yield: "Dividend yield", market_cap: "Market capitalization" };
    const scoringMetrics = [
      { key: "roe", category: "profitability", higher: true },
      { key: "roce", category: "profitability", higher: true },
      { key: "net_profit_margin", category: "profitability", higher: true },
      { key: "revenue_growth", category: "growth", higher: true },
      { key: "pe_ratio", category: "valuation", higher: false },
      { key: "pb_ratio", category: "valuation", higher: false },
      { key: "debt_to_equity", category: "financial_health", higher: false },
      { key: "dividend_yield", category: "dividends", higher: true },
    ];
    const profile = h("select", { "aria-label": "Scoring strategy" }, ...Object.entries(profileDefinitions).map(([v, p]) => h("option", { value: v }, p.label)));
    const weightInputs = {};
    const weightGrid = h("div", { class: "fundamental-weight-grid" });
    const metricFilter = h("select", { "aria-label": "Filter by metric" },
      h("option", { value: "all" }, "All metrics"),
      ...Object.entries(metricLabels).map(([v, l]) => h("option", { value: v }, l)));
    const sectorFilter = h("select", { "aria-label": "Filter by sector" }, h("option", { value: "" }, "All sectors"));
    const formatMetric = (key, value, currency) => {
      if (value == null || !Number.isFinite(Number(value))) return "—";
      value = Number(value);
      if (["roe", "roce", "revenue_growth", "net_profit_margin", "dividend_yield"].includes(key)) return (value * 100).toFixed(2) + "%";
      if (key === "market_cap") return (currency === "INR" ? "₹" : currency + " ") + new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 2 }).format(value);
      if (key === "eps") return (currency === "INR" ? "₹" : currency + " ") + value.toFixed(2);
      return value.toFixed(2) + (key === "debt_to_equity" ? "%" : "");
    };
    function currentWeights() {
      const out = {};
      for (const [key] of categories) out[key] = Math.max(0, Number(weightInputs[key].value) || 0);
      const sum = Object.values(out).reduce((a, b) => a + b, 0);
      if (!sum) return out;
      for (const key of Object.keys(out)) out[key] = out[key] / sum * 100;
      return out;
    }
    function drawTable() {
      const sector = sectorFilter.value; const metric = metricFilter.value;
      const filtered = rows.filter((r) => !sector || r.sector === sector);
      const metrics = Object.keys(metricLabels).filter((k) => metric === "all" || metric === k);
      const table = h("table", { class: "stocks fundamentals-table" },
        h("thead", null, h("tr", null, h("th", null, "Company"), ...metrics.map((k) => h("th", null, metricLabels[k])))),
        h("tbody", null, ...filtered.map((r) => h("tr", null,
          h("td", { class: "nm", "data-l": "Company" }, h("b", null, r.name), h("small", null, `${short(r.symbol)} · ${r.sector}`)),
          ...metrics.map((k) => h("td", { class: "num", "data-l": metricLabels[k] }, formatMetric(k, r.values[k], r.currency)))))));
      tableWrap.replaceChildren(table);
      if (!filtered.length) tableWrap.replaceChildren(h("p", { class: "muted" }, "Add companies and load their fundamentals to compare them here."));
    }
    function drawChips() {
      chips.replaceChildren(...selected.map((x) => h("span", { class: "chip" }, `${x.name} (${short(x.symbol)})`,
        h("button", { type: "button", "aria-label": "Remove " + x.name, onclick: () => { selected = selected.filter((s) => s.symbol !== x.symbol); drawChips(); } }, "×"))));
    }
    function rankRows() {
      if (!rows.length) { rankWrap.replaceChildren(h("p", { class: "muted" }, "Load company fundamentals to generate a ranking.")); return; }
      const weights = currentWeights();
      // Convert each metric into a percentile within the currently selected peer group.
      const percentiles = {};
      for (const metric of scoringMetrics) {
        const valid = rows.filter((r) => r.available && Number.isFinite(Number(r.values[metric.key])) && r.values[metric.key] != null);
        percentiles[metric.key] = new Map();
        for (const row of valid) {
          const value = Number(row.values[metric.key]);
          const below = valid.filter((x) => (metric.higher ? Number(x.values[metric.key]) < value : Number(x.values[metric.key]) > value)).length;
          const equal = valid.filter((x) => Number(x.values[metric.key]) === value).length;
          const p = valid.length <= 1 ? 50 : ((below + (equal - 1) / 2) / (valid.length - 1)) * 100;
          percentiles[metric.key].set(row.symbol, p);
        }
      }
      ranked = rows.map((row) => {
        let total = 0; let used = 0; const breakdown = {};
        for (const [category] of categories) {
          const members = scoringMetrics.filter((m) => m.category === category && percentiles[m.key].has(row.symbol));
          if (!members.length || weights[category] <= 0) { breakdown[category] = null; continue; }
          const catScore = members.reduce((sum, m) => sum + percentiles[m.key].get(row.symbol), 0) / members.length;
          breakdown[category] = catScore;
          total += catScore * weights[category]; used += weights[category];
        }
        return { ...row, score: used ? total / used : null, breakdown, coverage: scoringMetrics.filter((m) => percentiles[m.key].has(row.symbol)).length };
      }).sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
      const fmtScore = (v) => v == null ? "Insufficient data" : v.toFixed(1) + "/100";
      const table = h("table", { class: "stocks fundamentals-table ranking-table" },
        h("thead", null, h("tr", null, h("th", null, "Rank"), h("th", null, "Company"), h("th", null, "Score"), h("th", null, "Metrics available"), h("th", null, "Category scores"))),
        h("tbody", null, ...ranked.map((r, i) => h("tr", null,
          h("td", { class: "num" }, r.score == null ? "—" : "#" + (i + 1)),
          h("td", { class: "nm" }, h("b", null, r.name), h("small", null, `${short(r.symbol)} · ${r.sector}`)),
          h("td", { class: "num score-cell" }, fmtScore(r.score)),
          h("td", { class: "num" }, `${r.coverage}/${scoringMetrics.length}`),
          h("td", { class: "category-scores" }, categories.map(([key, label]) => r.breakdown[key] == null ? null : h("span", { class: "score-tag" }, `${label}: ${r.breakdown[key].toFixed(0)}`)))))));
      rankWrap.replaceChildren(table);
      const available = ranked.filter((r) => r.score != null).length;
      rankingStatus.textContent = `${available} of ${ranked.length} companies ranked. Scores are relative to the selected companies, not absolute quality ratings. EPS and market capitalization are shown in the comparison table but excluded from the score because raw EPS and company size are not directly comparable across businesses.`;
    }
    function setProfileWeights() {
      const p = profileDefinitions[profile.value];
      for (const [key] of categories) weightInputs[key].value = p.weights[key];
      const custom = profile.value === "custom";
      for (const [key] of categories) weightInputs[key].disabled = !custom;
      rankRows();
    }
    const rankingStatus = h("p", { class: "muted" }, "Load company fundamentals to generate a ranking.");
    for (const [key, label] of categories) {
      const input = h("input", { type: "number", min: 0, max: 100, step: 5, value: profileDefinitions.balanced.weights[key], "aria-label": label + " weight" });
      input.addEventListener("input", () => { profile.value = "custom"; for (const [k] of categories) weightInputs[k].disabled = false; rankRows(); });
      weightInputs[key] = input;
      weightGrid.append(h("label", { class: "field weight-field" }, h("span", null, label + " (%)"), input));
    }
    profile.addEventListener("change", setProfileWeights);
    searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer); const q = searchInput.value.trim();
      if (q.length < 2) { resultsBox.replaceChildren(); return; }
      searchTimer = setTimeout(async () => {
        try {
          const found = await api("/api/stocks/search?q=" + encodeURIComponent(q));
          resultsBox.replaceChildren(...found.filter((x) => !x.symbol.startsWith("^") && !selected.some((s) => s.symbol === x.symbol)).slice(0, 8).map((x) =>
            h("div", { class: "rowline" }, h("span", null, `${x.name} (${short(x.symbol)}) · ${x.sector}`),
              h("button", { class: "btn small", disabled: selected.length >= 8, onclick: () => { if (selected.length < 8) selected.push(x); drawChips(); resultsBox.replaceChildren(); searchInput.value = ""; } }, "Add"))));
          if (!resultsBox.childNodes.length) resultsBox.replaceChildren(h("p", { class: "muted" }, "No matching companies available."));
        } catch (ex) { resultsBox.replaceChildren(h("p", { class: "error" }, ex.message)); }
      }, 300);
    });
    const load = h("button", { class: "btn", onclick: async () => {
      if (!selected.length) { status.textContent = "Add at least one company first."; return; }
      load.disabled = true; load.textContent = "Loading fundamentals…"; status.textContent = "Fetching reported company metrics. This can take a few seconds.";
      try {
        const data = await api("/api/fundamentals?symbols=" + encodeURIComponent(selected.map((x) => x.symbol).join(",")));
        rows = data.items || [];
        const sectors = [...new Set(rows.map((r) => r.sector).filter(Boolean))].sort();
        sectorFilter.replaceChildren(h("option", { value: "" }, "All sectors"), ...sectors.map((x) => h("option", { value: x }, x)));
        const missing = rows.filter((r) => !r.available).length;
        status.textContent = `${rows.length} companies loaded. Values are provider-reported; ${missing} company/companies have no available fundamentals. Cached for up to 6 hours.`;
        drawTable(); rankRows();
      } catch (ex) { status.textContent = ex.message; }
      finally { load.disabled = false; load.textContent = "Load / refresh comparison"; }
    } }, "Load / refresh comparison");
    sectorFilter.addEventListener("change", drawTable); metricFilter.addEventListener("change", drawTable);
    setProfileWeights();
    shell(h("div", null,
      h("div", { class: "pagehead" }, h("h1", null, "Fundamental analysis & stock ranker"), h("p", null, "Compare ten reported fundamentals and rank your selected companies using five investment profiles.")),
      h("section", { class: "panel" }, h("label", { class: "field" }, h("span", null, "Find companies"), searchInput), resultsBox,
        h("h3", null, "Selected companies"), chips, h("p", { class: "muted" }, "Select between 1 and 8 companies. Indices are excluded because they are not individual companies."), load, status),
      h("section", { class: "panel" }, h("h2", null, "Fundamental stock ranking"), h("p", { class: "muted" }, "Choose a strategy. Custom weights are normalized automatically to total 100% when scoring."),
        h("label", { class: "field" }, h("span", null, "Scoring profile"), profile), weightGrid, rankingStatus, rankWrap),
      h("section", { class: "panel" }, h("div", { class: "toolbar" }, h("label", { class: "field" }, h("span", null, "Filter sector"), sectorFilter), h("label", { class: "field" }, h("span", null, "Show metric"), metricFilter)), tableWrap),
      h("p", { class: "disclaimer" }, "Scoring uses relative percentiles among the companies you selected. Higher ROE, ROCE, margin, revenue growth and dividend yield score better; lower P/E, P/B and debt-to-equity score better. EPS and market capitalization are displayed but not scored. Scores are screening aids, not guarantees or investment advice.")), "fundamentals");
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
    killCharts();
    if (marketTimer) { clearInterval(marketTimer); marketTimer = null; }
    setupChartDefaults();
    if (!S.health) { try { S.health = await (await fetch("/api/health")).json(); } catch (e) { S.health = { synthetic: false, max_shortlist: 12, min_shortlist: 3 }; } }
    const [, page, arg] = (location.hash || "#/").split("/");
    if (!S.token) { viewLogin(); return; }
    if (page === "login") { location.hash = "#/home"; return; }
    if (page === "home" || !page) return viewHome();
    if (page === "choose") return viewChoose();
    if (page === "fundamentals") return viewFundamentals();
    if (page === "mutual-funds") return viewMutualFunds();
    if (page === "sector-mix") return viewSectorMix();
    if (page === "comfort") return viewComfort();
    if (page === "result") return viewResult(arg);
    if (page === "history") return viewHistory();
    if (page === "security") return viewSecurity();
    return viewChoose();
  }
  window.addEventListener("hashchange", route);
  route();
})();
