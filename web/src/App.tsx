import { FormEvent, useEffect, useState } from "react";
import { api } from "./api";

type Page =
  | "desk"
  | "orders"
  | "research"
  | "market"
  | "chart"
  | "aimode"
  | "strategies"
  | "alerts"
  | "calendar"
  | "journal"
  | "options"
  | "settings";

const NAV = [
  { group: "NAVIGATION", sub: "Daily workspace", mark: "N", klass: "g-nav", items: [
    { id: "desk", label: "Desk" },
    { id: "orders", label: "Orders" },
    { id: "research", label: "Research" },
    { id: "market", label: "Market" },
    { id: "chart", label: "Chart" },
  ]},
  { group: "APPS", sub: "Tune & automate", mark: "A", klass: "g-apps", items: [
    { id: "aimode", label: "AI Mode" },
    { id: "strategies", label: "Strategies" },
    { id: "alerts", label: "Alerts" },
    { id: "calendar", label: "Calendar" },
  ]},
  { group: "MORE", sub: "Guide & extras", mark: "+", klass: "g-more", items: [
    { id: "journal", label: "Journal" },
    { id: "options", label: "Options" },
    { id: "settings", label: "Settings" },
  ]},
] as const;

function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}
function pct(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Math.abs(n) <= 1.5 && Math.abs(n) !== 0 ? n * 100 : n;
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function cls(n?: number | null) {
  if (n == null) return "";
  return n >= 0 ? "pos" : "neg";
}

export default function App() {
  const [page, setPage] = useState<Page>("desk");
  const [symbol, setSymbol] = useState("AAPL");
  const [account, setAccount] = useState<any>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  async function refreshAccount() {
    try {
      setError("");
      setAccount(await api.account());
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => { refreshAccount(); }, []);

  function goSymbol(next: string) {
    const s = next.toUpperCase().trim();
    if (!s) return;
    setSymbol(s);
    setPage("research");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark" /> Cgab</div>
        <div className="nav-stack">
        {NAV.map((g) => (
          <section key={g.group} className={`nav-group ${g.klass}`}>
            <div className="nav-head">
              <span className="nav-ico">{g.mark}</span>
              <div>
                <div className="nav-k">{g.group}</div>
                <div className="nav-sub">{g.sub}</div>
              </div>
            </div>
            {g.items.map((it) => (
              <button key={it.id} className={`nav-btn ${page === it.id ? "active" : ""}`} onClick={() => setPage(it.id as Page)}>
                {it.label}
              </button>
            ))}
          </section>
        ))}
        </div>
        <div className="sidebar-foot">
          <span className="paper-pill">PAPER</span>
          <div className="muted" style={{ marginTop: 8 }}>US equity AI desk</div>
        </div>
      </aside>
      <section className="main">
        <header className="topbar">
          <form onSubmit={(e) => { e.preventDefault(); goSymbol(search); }}>
            <input className="search" placeholder="Search symbol…" value={search} onChange={(e) => setSearch(e.target.value)} />
          </form>
          <span className="chip">Worker {account ? "linked" : "…"}</span>
          <span className="chip">{money(account?.equity || account?.portfolio_value)}</span>
          <button className="btn ghost" onClick={refreshAccount}>Refresh</button>
        </header>
        <div className="content">
          {error && <p className="err">{error}</p>}
          {page === "desk" && <Desk account={account} onTrade={(s) => { setSymbol(s); }} onRefresh={refreshAccount} />}
          {page === "orders" && <Orders />}
          {page === "research" && <Research symbol={symbol} setSymbol={setSymbol} />}
          {page === "market" && <Market onOpen={goSymbol} />}
          {page === "chart" && <ChartPage symbol={symbol} setSymbol={setSymbol} />}
          {page === "aimode" && <AiMode />}
          {page === "strategies" && <Strategies />}
          {page === "alerts" && <Alerts />}
          {page === "calendar" && <Calendar />}
          {page === "journal" && <Journal />}
          {page === "options" && <Options symbol={symbol} setSymbol={setSymbol} />}
          {page === "settings" && <Settings />}
        </div>
      </section>
    </div>
  );
}

function Desk({ account, onTrade, onRefresh }: { account: any; onTrade: (s: string) => void; onRefresh: () => void }) {
  const [ops, setOps] = useState<any>(null);
  const [perf, setPerf] = useState<any>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [ticket, setTicket] = useState({ symbol: "AAPL", side: "buy", quantity: 1, order_type: "market" });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.ops().then(setOps).catch(() => {});
    api.performance().then(setPerf).catch(() => {});
    api.orders("open").then((d) => setOrders(d.orders || [])).catch(() => {});
  }, [account]);

  const positions = account?.positions || [];
  const day = account?.day_pnl || 0;
  const equity = account?.equity || account?.portfolio_value || 0;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await api.submitOrder(ticket);
      setMsg("Order submitted (paper).");
      onRefresh();
      setOrders((await api.orders("open")).orders || []);
    } catch (err: any) {
      setMsg(err.message);
    }
  }

  return (
    <>
      <div className="kpis">
        <div className="card kpi"><div className="lbl">Equity</div><div className="val">{money(equity)}</div></div>
        <div className="card kpi"><div className="lbl">Day P/L</div><div className={`val ${cls(day)}`}>{money(day)}</div></div>
        <div className="card kpi"><div className="lbl">Cash</div><div className="val">{money(account?.cash)}</div></div>
        <div className="card kpi"><div className="lbl">Positions</div><div className="val">{positions.length}</div></div>
        <div className="card kpi"><div className="lbl">Unrealized</div><div className={`val ${cls(account?.unrealized_pl)}`}>{money(account?.unrealized_pl)}</div></div>
        <div className="card kpi"><div className="lbl">Buying power</div><div className="val">{money(account?.buying_power)}</div></div>
      </div>
      <div className="grid3" style={{ marginBottom: 14 }}>
        <div className="card kpi"><div className="lbl">Win rate (backtest)</div><div className="val">{perf?.win_rate_pct ?? "—"}%</div></div>
        <div className="card kpi"><div className="lbl">Profit factor</div><div className="val">{perf?.profit_factor ?? "—"}</div></div>
        <div className="card kpi"><div className="lbl">Max DD</div><div className="val">{perf?.max_drawdown_pct ?? "—"}%</div></div>
      </div>
      <div className="layout">
        <div className="card">
          <h3>Holdings — broker inventory</h3>
          <p className="muted">Alpaca Paper real book (not Kelly model book)</p>
          <table className="table">
            <thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Mkt</th><th>Unreal</th><th>Wgt</th><th></th></tr></thead>
            <tbody>
              {positions.length === 0 && <tr><td colSpan={8} className="muted">No positions yet</td></tr>}
              {positions.map((p: any) => {
                const equity = account?.equity || account?.portfolio_value || 0;
                const wgt = equity && p.market_value ? (100 * p.market_value / equity).toFixed(1) : "—";
                return (
                <tr key={p.symbol}>
                  <td>{p.symbol}</td>
                  <td>{p.quantity}</td>
                  <td>{p.average_entry_price?.toFixed?.(2)}</td>
                  <td>{p.current_price?.toFixed?.(2) ?? "—"}</td>
                  <td>{money(p.market_value)}</td>
                  <td className={cls(p.unrealized_pl)}>{money(p.unrealized_pl)}</td>
                  <td>{wgt === "—" ? "—" : `${wgt}%`}</td>
                  <td><button className="btn" onClick={() => { setTicket((t) => ({ ...t, symbol: p.symbol })); onTrade(p.symbol); }}>Trade</button></td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div>
          <div className="card" style={{ marginBottom: 12 }}>
            <h3>Trade ticket</h3>
            <form onSubmit={submit}>
              <div className="row" style={{ marginBottom: 8 }}>
                <button type="button" className={`tab ${ticket.side === "buy" ? "on" : ""}`} onClick={() => setTicket({ ...ticket, side: "buy" })}>Buy</button>
                <button type="button" className={`tab ${ticket.side === "sell" ? "on" : ""}`} onClick={() => setTicket({ ...ticket, side: "sell" })}>Sell</button>
              </div>
              <label className="field">Symbol<input value={ticket.symbol} onChange={(e) => setTicket({ ...ticket, symbol: e.target.value.toUpperCase() })} /></label>
              <label className="field">Qty<input type="number" min={1} value={ticket.quantity} onChange={(e) => setTicket({ ...ticket, quantity: Number(e.target.value) })} /></label>
              <label className="field">Type
                <select value={ticket.order_type} onChange={(e) => setTicket({ ...ticket, order_type: e.target.value })}>
                  <option value="market">Market</option>
                  <option value="limit">Limit</option>
                </select>
              </label>
              <button className="btn" style={{ marginTop: 10, width: "100%" }}>Submit order</button>
              {msg && <p className="muted">{msg}</p>}
            </form>
          </div>
          <div className="card" style={{ marginBottom: 12 }}>
            <h3>Open orders</h3>
            {orders.length === 0 && <p className="muted">None</p>}
            {orders.slice(0, 6).map((o) => (
              <div key={o.id} className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
                <span>{o.side} {o.qty} {o.symbol}</span>
                <button className="btn ghost" onClick={() => api.cancelOrder(o.id).then(() => api.orders("open").then((d) => setOrders(d.orders)))}>Cancel</button>
              </div>
            ))}
          </div>
          <div className="card">
            <h3>Risk / Ops</h3>
            <p className="muted">Kill switch {ops?.kill_switch ? "ON" : "OFF"}</p>
            <div className="row">
              <button className="btn danger" onClick={() => api.kill().then(setOps)}>Halt buys</button>
              <button className="btn ghost" onClick={() => api.resume().then(setOps)}>Resume</button>
            </div>
            <p className="muted" style={{ marginTop: 8 }}>Tune strategy in AI Mode. LLM cannot bypass RiskEngine.</p>
          </div>
        </div>
      </div>
    </>
  );
}

function Orders() {
  const [rows, setRows] = useState<any[]>([]);
  const [filter, setFilter] = useState("all");
  useEffect(() => { api.orders(filter === "open" ? "open" : "all").then((d) => setRows(d.orders || [])).catch(() => {}); }, [filter]);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Orders</h3>
        <div className="tabs">
          <button className={`tab ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>All</button>
          <button className={`tab ${filter === "open" ? "on" : ""}`} onClick={() => setFilter("open")}>Open</button>
        </div>
      </div>
      <table className="table">
        <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Status</th><th>Time</th></tr></thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id}><td>{o.symbol}</td><td>{o.side}</td><td>{o.qty || o.quantity}</td><td>{o.type || o.order_type}</td><td>{o.status}</td><td>{String(o.submitted_at || "").slice(0, 19)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Research({ symbol, setSymbol }: { symbol: string; setSymbol: (s: string) => void }) {
  const [tab, setTab] = useState<"analyze" | "news" | "valuation">("analyze");
  const [info, setInfo] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setErr("");
    api.research(symbol).then(setInfo).catch((e) => setErr(e.message));
  }, [symbol]);
  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input className="search" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        <div className="tabs">
          <button className={`tab ${tab === "analyze" ? "on" : ""}`} onClick={() => setTab("analyze")}>Analyze</button>
          <button className={`tab ${tab === "news" ? "on" : ""}`} onClick={() => setTab("news")}>News</button>
          <button className={`tab ${tab === "valuation" ? "on" : ""}`} onClick={() => setTab("valuation")}>Valuation</button>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      <div className="layout">
        <div className="card">
          <h3>{info?.name || symbol}</h3>
          <p className="muted">{info?.sector} · {money(info?.price)}</p>
          {tab === "analyze" && <p>{info?.summary || "Load a symbol to see research."}</p>}
          {tab === "news" && <p className="muted">Name-level news stays in this tab (single news entry). Yahoo/AI impact runs from the research pipeline.</p>}
          {tab === "valuation" && (
            <div>
              <p>P/E {info?.pe ?? "—"} · Forward P/E {info?.forward_pe ?? "—"}</p>
              <p>52w {info?.fifty_two_low ?? "—"} – {info?.fifty_two_high ?? "—"}</p>
              <p className="muted">DCF / comps wire in Phase D of the research backend.</p>
            </div>
          )}
        </div>
        <div className="card">
          <h3>Plan</h3>
          <p className="muted">{info?.note}</p>
          <button className="btn" onClick={() => api.addWatch(symbol)}>Add to watchlist</button>
        </div>
      </div>
    </div>
  );
}

function Market({ onOpen }: { onOpen: (s: string) => void }) {
  const [quotes, setQuotes] = useState<any[]>([]);
  const [watch, setWatch] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function load() {
    setBusy(true); setErr("");
    try {
      const [q, w] = await Promise.all([api.quotes(), api.watchlist()]);
      setQuotes(q.quotes || []);
      setWatch(w.symbols || []);
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  }
  useEffect(() => { load(); }, []);
  return (
    <div className="layout">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3>Market scan</h3>
          <button className="btn" onClick={load} disabled={busy}>{busy ? "Scanning…" : "Run scan"}</button>
        </div>
        {err && <p className="err">{err}</p>}
        <table className="table">
          <thead><tr><th>Symbol</th><th>Name</th><th>Last</th><th>Chg</th><th>Sector</th></tr></thead>
          <tbody>
            {quotes.map((q) => (
              <tr key={q.symbol} onClick={() => onOpen(q.symbol)} style={{ cursor: "pointer" }}>
                <td>{q.symbol}</td><td>{q.name}</td><td>{q.last?.toFixed?.(2)}</td>
                <td className={cls(q.chg_pct)}>{pct(q.chg_pct)}</td><td>{q.sector}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h3>Watchlist</h3>
        {watch.map((s) => (
          <div key={s} className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
            <button className="nav-btn" onClick={() => onOpen(s)}>{s}</button>
            <button className="btn ghost" onClick={() => api.delWatch(s).then((d) => setWatch(d.symbols))}>Remove</button>
          </div>
        ))}
        <WatchAdd onAdd={(sym) => api.addWatch(sym).then((d) => setWatch(d.symbols))} />
      </div>
    </div>
  );
}

function WatchAdd({ onAdd }: { onAdd: (s: string) => void }) {
  const [s, setS] = useState("");
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); onAdd(s); setS(""); }}>
      <input value={s} onChange={(e) => setS(e.target.value.toUpperCase())} placeholder="Add ticker" />
      <button className="btn">Add</button>
    </form>
  );
}

function ChartPage({ symbol, setSymbol }: { symbol: string; setSymbol: (s: string) => void }) {
  const [bars, setBars] = useState<any[]>([]);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.chart(symbol).then((d) => setBars(d.bars || [])).catch((e) => setErr(e.message));
  }, [symbol]);
  const max = Math.max(...bars.map((b) => b.c), 1);
  const min = Math.min(...bars.map((b) => b.c), 0);
  const span = max - min || 1;
  return (
    <div className="card">
      <div className="row"><h3>Chart</h3><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></div>
      {err && <p className="err">{err}</p>}
      <div className="bars" style={{ marginTop: 16 }}>
        {bars.slice(-80).map((b, i) => (
          <div key={i} className="bar" title={`${b.t} ${b.c}`} style={{ height: `${((b.c - min) / span) * 100}%` }} />
        ))}
      </div>
      <p className="muted">Daily closes · Yahoo. Full drawing tools can come later.</p>
    </div>
  );
}

function AiMode() {
  const [cfg, setCfg] = useState<any>(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.aiMode().then(setCfg); }, []);
  if (!cfg) return <p className="muted">Loading…</p>;
  async function save(e: FormEvent) {
    e.preventDefault();
    const saved = await api.saveAiMode(cfg);
    setCfg(saved);
    setMsg("Saved. Worker CLI still uses --strategy on start; this file is the UI source of truth.");
  }
  return (
    <form className="layout" onSubmit={save}>
      <div className="card">
        <h3>AI Mode</h3>
        <p className="muted">Only place to tune the trading agent. Cannot bypass RiskEngine / kill switch / mandate.</p>
        <div className="row" style={{ margin: "12px 0" }}>
          {["stable", "aggressive", "hybrid"].map((s) => (
            <button type="button" key={s} className={`tab ${cfg.strategy === s ? "on" : ""}`} onClick={() => setCfg({ ...cfg, strategy: s })}>{s}</button>
          ))}
        </div>
        <label className="field">LLM influence % (cap 40)
          <input type="range" min={0} max={40} value={cfg.llm_influence} onChange={(e) => setCfg({ ...cfg, llm_influence: Number(e.target.value) })} />
          <span>{cfg.llm_influence}%</span>
        </label>
        <label className="field">Entry threshold
          <input type="number" value={cfg.entry_threshold} onChange={(e) => setCfg({ ...cfg, entry_threshold: Number(e.target.value) })} />
        </label>
        <label className="field">Max positions
          <input type="number" value={cfg.max_positions} onChange={(e) => setCfg({ ...cfg, max_positions: Number(e.target.value) })} />
        </label>
        <label className="field">Risk tolerance
          <select value={cfg.risk_tolerance} onChange={(e) => setCfg({ ...cfg, risk_tolerance: e.target.value })}>
            <option>low</option><option>medium</option><option>high</option>
          </select>
        </label>
        <button className="btn" style={{ marginTop: 12 }}>Save</button>
        {msg && <p className="ok">{msg}</p>}
      </div>
      <div className="card">
        <h3>Gates</h3>
        <p>Kill switch: {cfg.kill_switch ? "ON" : "OFF"}</p>
        <p className="muted">Paper mode only. LLM is a modifier, not an execution override.</p>
      </div>
    </form>
  );
}

function Strategies() {
  const [perf, setPerf] = useState<any>(null);
  useEffect(() => { api.performance().then(setPerf); }, []);
  return (
    <div className="card">
      <h3>Strategies</h3>
      <p className="muted">Last stable strategy backtest (next-bar open + slippage + 10 bps).</p>
      <div className="grid3">
        <div className="kpi"><div className="lbl">Return</div><div className="val">{perf?.total_return_pct ?? "—"}%</div></div>
        <div className="kpi"><div className="lbl">Sharpe</div><div className="val">{perf?.sharpe_ratio ?? "—"}</div></div>
        <div className="kpi"><div className="lbl">Trades</div><div className="val">{perf?.num_trades ?? "—"}</div></div>
      </div>
      <p className="muted">Re-run: python scripts/run_strategy_backtest_stable.py</p>
    </div>
  );
}

function Alerts() {
  return (
    <div className="card">
      <h3>Alerts</h3>
      <p className="muted">Rules from saved research plans + Telegram delivery live in the existing alert worker. Inbox wiring continues here.</p>
      <button className="btn">New rule</button>
    </div>
  );
}

function Calendar() {
  const [events, setEvents] = useState<any[]>([]);
  useEffect(() => { api.calendar().then((d) => setEvents(d.events || [])).catch(() => {}); }, []);
  return (
    <div className="card">
      <h3>Calendar</h3>
      <table className="table">
        <thead><tr><th>Date</th><th>Event</th><th>Impact</th><th>Symbol</th></tr></thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={i}><td>{e.date}</td><td>{e.event}</td><td>{e.impact}</td><td>{e.symbol}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Journal() {
  const [entries, setEntries] = useState<any[]>([]);
  const [note, setNote] = useState("");
  const [symbol, setSymbol] = useState("AAPL");
  useEffect(() => { api.journal().then((d) => setEntries(d.entries || [])).catch(() => {}); }, []);
  return (
    <div className="card">
      <h3>Journal</h3>
      <form className="row" onSubmit={async (e) => { e.preventDefault(); const d = await api.addJournal({ symbol, note }); setEntries(d.entries); setNote(""); }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: 90 }} />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note" style={{ flex: 1 }} />
        <button className="btn">Add</button>
      </form>
      <table className="table">
        <thead><tr><th>Time</th><th>Symbol</th><th>Note</th></tr></thead>
        <tbody>
          {[...entries].reverse().slice(0, 40).map((e, i) => (
            <tr key={e.id || i}><td>{String(e.ts || e.created_at || "").slice(0, 19)}</td><td>{e.symbol}</td><td>{e.note || JSON.stringify(e)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Options({ symbol, setSymbol }: { symbol: string; setSymbol: (s: string) => void }) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setErr("");
    api.options(symbol).then(setData).catch((e) => setErr(e.message));
  }, [symbol]);
  return (
    <div className="card">
      <div className="row"><h3>Options chain</h3><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></div>
      {err && <p className="err">{err}</p>}
      <p className="muted">Expiry {data?.expiry || "—"}</p>
      <div className="grid2">
        <div>
          <h3>Calls</h3>
          <table className="table"><thead><tr><th>Strike</th><th>Last</th><th>IV</th></tr></thead>
            <tbody>{(data?.calls || []).map((c: any, i: number) => <tr key={i}><td>{c.strike}</td><td>{c.lastPrice}</td><td>{c.impliedVolatility?.toFixed?.(2)}</td></tr>)}</tbody>
          </table>
        </div>
        <div>
          <h3>Puts</h3>
          <table className="table"><thead><tr><th>Strike</th><th>Last</th><th>IV</th></tr></thead>
            <tbody>{(data?.puts || []).map((c: any, i: number) => <tr key={i}><td>{c.strike}</td><td>{c.lastPrice}</td><td>{c.impliedVolatility?.toFixed?.(2)}</td></tr>)}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Settings() {
  const [meta, setMeta] = useState<any>(null);
  const [ops, setOps] = useState<any>(null);
  useEffect(() => { api.meta().then(setMeta); api.ops().then(setOps); }, []);
  return (
    <div className="card">
      <h3>Settings</h3>
      <p>App name: <strong>Cgab</strong></p>
      <p>Paper: {String(meta?.paper)}</p>
      <p>Ignore market hours env: {String(meta?.ignore_market_hours)}</p>
      <p className="muted">Kill switch: {ops?.kill_switch ? "ON" : "OFF"}</p>
      <p className="muted">Do not set APCA_PAPER=false until the paper runbook is signed.</p>
    </div>
  );
}
