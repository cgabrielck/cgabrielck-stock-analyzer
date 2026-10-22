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
    { id: "journal", label: "Trade journal" },
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
function accountChipLabel(account: any) {
  return account ? "Account loaded" : "Account —";
}
function workerChip(worker: any) {
  const display = worker?.display;
  if (display && display.worker_label) {
    const tone = display.worker_tone;
    const klass = tone === "on" ? "chip on" : tone === "warn" ? "chip warn" : "chip";
    return { label: String(display.worker_label), klass, on: !!display.auto_on };
  }
  const unverified = !!(worker?.unverified || worker?.process_evidence === "heartbeat_unverified");
  const stale = !!worker?.stale;
  const evidence = String(worker?.process_evidence || "");
  const alive = !!(worker?.alive || evidence === "pid_alive");
  const on = !!(worker?.running && alive && !stale && !unverified && evidence !== "pid_dead" && evidence !== "none");
  if (unverified) return { label: "Worker unverified", klass: "chip warn", on: false };
  if (stale) return { label: "Worker stale", klass: "chip warn", on: false };
  if (on) return { label: "Worker running", klass: "chip on", on: true };
  return { label: "Worker not running", klass: "chip", on: false };
}
function kpiSourceLabel(perf: any) {
  if (!perf || perf.empty_book || perf.kpi_source === "none" || !perf.kpi_source) return "empty";
  if (perf.kpi_label === "empty" || perf.kpi_label === "paper" || perf.kpi_label === "backtest") {
    return perf.kpi_label;
  }
  const src = perf.kpi_source;
  if (src === "paper" || src === "backtest") return src;
  return "empty";
}
function kpiNum(n?: number | null, suffix = "") {
  if (n == null || Number.isNaN(n)) return "—";
  return suffix ? `${n}${suffix}` : String(n);
}
function ordersBookDirty(book: any) {
  return !!(book?.degraded || book?.unverified || book?.source === "local_store");
}
function fillItemPrice(item: any): number | null {
  if (!item || typeof item !== "object") return null;
  const raw = item.price ?? item.filled_avg_price ?? item.fill_price ?? item.filled_price;
  if (typeof raw === "boolean") return null;
  const n = Number(raw);
  return raw != null && raw !== "" && !Number.isNaN(n) && n > 0 ? n : null;
}
function positiveFillPrice(o: any): number | null {
  if (!o) return null;
  const raw = o.filled_avg_price ?? o.fill_price ?? o.filled_price;
  if (typeof raw !== "boolean") {
    const n = Number(raw);
    if (raw != null && raw !== "" && !Number.isNaN(n) && n > 0) return n;
  }
  for (const item of o.fills || []) {
    const px = fillItemPrice(item);
    if (px != null) return px;
  }
  return null;
}
function isPricedFill(e: any) {
  return positiveFillPrice(e) != null;
}
function orderStatusLabel(o: any) {
  if (o?.display_status) return String(o.display_status);
  const st = String(o?.status || "").toLowerCase();
  let label = o?.status ? String(o.status) : "—";
  if ((st === "filled" || st === "partially_filled") && !isPricedFill(o)) {
    label = "unpriced — not a fill";
  }
  const dirty = !!(o?.degraded || o?.unverified || o?.source === "local_store");
  if (dirty) return label === "—" ? "local/unverified" : `${label} · local/unverified`;
  return label;
}
function fillPriceLabel(o: any) {
  const px = positiveFillPrice(o);
  return px == null ? "—" : `$${px.toFixed(2)}`;
}
function journalStatusLabel(e: any) {
  const st = String(e?.status || "").toLowerCase();
  if ((st === "filled" || st === "partially_filled") && !isPricedFill(e)) {
    return "unpriced — not a fill";
  }
  return e?.status ? String(e.status) : "—";
}
function journalSummaryLabel(e: any) {
  const summary = String(e?.user_visible_summary || e?.note || "").trim();
  const st = String(e?.status || "").toLowerCase();
  const claiming = st === "filled" || st === "partially_filled" || st.includes("unpriced");
  if (!claiming || isPricedFill(e)) {
    return summary || "—";
  }
  if (!summary) return "unpriced — not a fill";
  const low = summary.toLowerCase();
  if (low.includes("filled")) return "unpriced — not a fill";
  if (low.includes("unpriced")) return summary;
  return `${summary} · unpriced — not a fill`;
}
function mandateGap(ops: any) {
  const st = ops?.mandate_status;
  if (!st || st === "loaded") return "";
  return `Mandate ${st} — Desk and paper worker fail-closed until mandate.json loads.`;
}

const DUAL_BOOK_CUE = "Two books, two directories. Desk broker journal/ledger is backend/data/. Streamlit Kelly/lab portfolio is data/. They are not the same book.";
const HOLDINGS_HINT = "Alpaca Paper real book (not a model portfolio)";
const EMPTY_POSITIONS = "No positions yet — paper auto only fills when a signal passes every gate.";
const OPEN_ORDERS_HINT = "new / accepted = sent to the broker, not filled yet. The limit must trade before shares hit the book.";
const JOURNAL_LEAD = "Orders, risk rejects, and operator notes from the local ledger. Empty means nothing was recorded — not a live fill. Fills appear only with a recorded fill price. Streamlit journal is lab/legacy.";
const JOURNAL_EMPTY = "No recorded orders, rejects, or notes yet. This is not a live book.";
const ALERTS_BODY = "New rules attach to saved research plans. Telegram worker stays the delivery path. Not a live fill feed.";
const AIMODE_LEAD = "Only place to tune the agent and start paper auto trading. LLM cannot bypass RiskEngine / kill / mandate.";
const AIMODE_PAPER = "Orders stay on Alpaca paper while PAPER is shown.";
const DESK_LLM = "Tune the agent in AI Mode. LLM cannot bypass RiskEngine.";
const PAPER_GATE = "Do not set APCA_PAPER=false until the paper runbook is signed.";

export default function App() {
  const [page, setPage] = useState<Page>("desk");
  const [symbol, setSymbol] = useState("AAPL");
  const [account, setAccount] = useState<any>(null);
  const [worker, setWorker] = useState<any>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");

  async function refreshAccount() {
    try {
      setError("");
      setAccount(await api.account());
    } catch (e: any) {
      setError(e.message);
      setAccount(null);
    }
    try {
      setWorker(await api.workerStatus());
    } catch {
      setWorker({
        running: false,
        unverified: false,
        stale: false,
        alive: false,
        process_evidence: "none",
        display: { auto_on: false, worker_running: false, worker_label: "Worker not running", worker_tone: "off" },
      });
    }
  }
  useEffect(() => { refreshAccount(); }, []);
  const workerUi = workerChip(worker);

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
          <span className="chip">{accountChipLabel(account)}</span>
          <span className={workerUi.klass}>{workerUi.label}</span>
          <span className="chip">{money(account?.equity || account?.portfolio_value)}</span>
          <button className="btn ghost" onClick={refreshAccount}>Refresh</button>
        </header>
        <p className="books-banner" id="booksBanner">{DUAL_BOOK_CUE}</p>
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
  const [orderBook, setOrderBook] = useState<any>({ orders: [] });
  const [ticket, setTicket] = useState({ symbol: "AAPL", side: "buy", quantity: 1, order_type: "market" });
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.ops().then(setOps).catch(() => {});
    api.performance().then(setPerf).catch(() => {});
    api.orders("open").then(setOrderBook).catch(() => {});
  }, [account]);
  const orders = orderBook.orders || [];
  const ordersDirty = ordersBookDirty(orderBook);

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
      setOrderBook(await api.orders("open"));
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
        <div className="card kpi"><div className="lbl">Win rate ({kpiSourceLabel(perf)})</div><div className="val">{kpiNum(perf?.win_rate_pct, "%")}</div></div>
        <div className="card kpi"><div className="lbl">Profit factor ({kpiSourceLabel(perf)})</div><div className="val">{kpiNum(perf?.profit_factor)}</div></div>
        <div className="card kpi"><div className="lbl">Max DD ({kpiSourceLabel(perf)})</div><div className="val">{kpiNum(perf?.max_drawdown_pct, "%")}</div></div>
      </div>
      {perf && (perf.empty_book || kpiSourceLabel(perf) === "empty") && (
        <p className="muted">Empty paper book — not backtest figures.</p>
      )}
      <div className="layout">
        <div className="card">
          <h3>Holdings — broker inventory</h3>
          <p className="muted">{HOLDINGS_HINT}</p>
          <p className="muted">{DUAL_BOOK_CUE}</p>
          <table className="table">
            <thead><tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Last</th><th>Mkt</th><th>Unreal</th><th>Wgt</th><th></th></tr></thead>
            <tbody>
              {positions.length === 0 && <tr><td colSpan={8} className="muted">{EMPTY_POSITIONS}</td></tr>}
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
            <p className="muted">{OPEN_ORDERS_HINT}</p>
            {ordersDirty && <p className="muted">Orders source={orderBook.source || "local_store"} · degraded · unverified — not a clean broker book.</p>}
            {orders.length === 0 && <p className="muted">None</p>}
            {orders.slice(0, 6).map((o) => (
              <div key={o.id} className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
                <span>{o.side} {o.qty} {o.symbol} · {orderStatusLabel(o)} · {fillPriceLabel(o)}</span>
                <button className="btn ghost" onClick={() => api.cancelOrder(o.id).then(() => api.orders("open").then(setOrderBook))}>Cancel</button>
              </div>
            ))}
          </div>
          <div className="card">
            <h3>Risk / Ops</h3>
            <p className="muted">Kill switch {ops?.kill_switch ? "ON" : "OFF"}</p>
            {mandateGap(ops) && <p className="muted">{mandateGap(ops)}</p>}
            <div className="row">
              <button className="btn danger" onClick={() => api.kill().then(setOps)}>Halt buys</button>
              <button className="btn ghost" onClick={() => api.resume().then(setOps)}>Resume</button>
            </div>
            <p className="muted" style={{ marginTop: 8 }}>{DESK_LLM}</p>
          </div>
        </div>
      </div>
    </>
  );
}

function Orders() {
  const [book, setBook] = useState<any>({ orders: [] });
  const [filter, setFilter] = useState("all");
  useEffect(() => { api.orders(filter === "open" ? "open" : "all").then(setBook).catch(() => {}); }, [filter]);
  const rows = book.orders || [];
  const dirty = ordersBookDirty(book);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Orders</h3>
        <div className="tabs">
          <button className={`tab ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>All</button>
          <button className={`tab ${filter === "open" ? "on" : ""}`} onClick={() => setFilter("open")}>Open</button>
        </div>
      </div>
      <p className="muted">{DUAL_BOOK_CUE}</p>
      <p className="muted">{OPEN_ORDERS_HINT}</p>
      {dirty && <p className="muted">Orders source={book.source || "local_store"} · degraded · unverified — not a clean broker book.</p>}
      <table className="table">
        <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th><th>Fill</th><th>Type</th><th>Status</th><th>Time</th></tr></thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id}><td>{o.symbol}</td><td>{o.side}</td><td>{o.qty || o.quantity}</td><td>{o.limit_price != null ? `$${Number(o.limit_price).toFixed(2)}` : "—"}</td><td>{fillPriceLabel(o)}</td><td>{o.type || o.order_type}</td><td>{orderStatusLabel(o)}</td><td>{String(o.submitted_at || "").slice(0, 19)}</td></tr>
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
  const [meta, setMeta] = useState<{ vendor?: string; asof?: string | null; fallback?: boolean }>({});
  const [err, setErr] = useState("");
  useEffect(() => {
    setErr("");
    api.chart(symbol).then((d) => {
      setBars(Array.isArray(d.bars) ? d.bars : []);
      setMeta({ vendor: d.vendor, asof: d.asof, fallback: d.fallback });
    }).catch((e) => {
      setBars([]);
      setMeta({});
      setErr(e.message);
    });
  }, [symbol]);
  const closes = bars.map((b) => b.c).filter((n) => typeof n === "number" && Number.isFinite(n));
  const max = Math.max(...closes, 1);
  const min = Math.min(...closes, 0);
  const span = max - min || 1;
  const vendor = meta.vendor || "none";
  const asof = meta.asof || "—";
  const fallback = meta.fallback ? " · Yahoo labeled fallback" : "";
  return (
    <div className="card">
      <div className="row"><h3>Chart</h3><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></div>
      {err && <p className="err">{err}</p>}
      {!err && !closes.length && <p className="muted">No history for this symbol — not a filled series.</p>}
      <div className="bars" style={{ marginTop: 16 }}>
        {closes.length ? bars.slice(-80).map((b, i) => (
          <div key={i} className="bar" title={`${b.t} ${b.c}`} style={{ height: `${((b.c - min) / span) * 100}%` }} />
        )) : null}
      </div>
      <p className="muted">{vendor} · as-of {asof}{fallback}. Daily snapshot. Not live / not verified.</p>
      <p className="muted">Secondary React page — TradingView-style candles ship on Desk web/static. Not an official TradingView widget. Indicators do not place orders.</p>
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
        <p className="muted">{AIMODE_LEAD}</p>
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
        <p className="muted">{AIMODE_PAPER} LLM is a modifier, not an execution override.</p>
      </div>
    </form>
  );
}

function Strategies() {
  const [perf, setPerf] = useState<any>(null);
  useEffect(() => { api.performance().then(setPerf); }, []);
  const bt = perf?.backtest;
  const hasBt = !!(bt && bt.source && bt.source !== "none");
  return (
    <div className="card">
      <h3>Strategies</h3>
      <p className="muted">{hasBt ? "Last stable strategy backtest (next-bar open + slippage + 10 bps)." : "No stable backtest summary."}</p>
      <div className="grid3">
        <div className="kpi"><div className="lbl">Return (backtest)</div><div className="val">{hasBt ? (bt.total_return_pct ?? "—") : "—"}%</div></div>
        <div className="kpi"><div className="lbl">Sharpe (backtest)</div><div className="val">{hasBt ? (bt.sharpe_ratio ?? "—") : "—"}</div></div>
        <div className="kpi"><div className="lbl">Trades (backtest)</div><div className="val">{hasBt ? (bt.num_trades ?? "—") : "—"}</div></div>
      </div>
      <p className="muted">Re-run: python scripts/run_strategy_backtest_stable.py</p>
    </div>
  );
}

function Alerts() {
  return (
    <div className="card">
      <h3>Alerts</h3>
      <p className="muted">{ALERTS_BODY}</p>
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
  const [kind, setKind] = useState("all");
  const [disclaimer, setDisclaimer] = useState(JOURNAL_LEAD);
  const [detail, setDetail] = useState("");
  const load = (filter = kind) => {
    api.journal(filter).then((d) => {
      setEntries(d.entries || []);
      setDisclaimer(d.disclaimer || JOURNAL_LEAD);
    }).catch(() => setEntries([]));
  };
  useEffect(() => { load(kind); }, [kind]);
  return (
    <div className="card">
      <h3>Trade journal</h3>
      <p className="muted">{DUAL_BOOK_CUE}</p>
      <p className="muted">{disclaimer}</p>
      <div className="row" style={{ margin: "10px 0" }}>
        {["all", "reject", "order", "note"].map((k) => (
          <button key={k} className={`tab${kind === k ? " on" : ""}`} onClick={() => setKind(k)}>{k}</button>
        ))}
      </div>
      <form className="row" onSubmit={async (e) => { e.preventDefault(); if (!note.trim()) return; const d = await api.addJournal({ symbol, note }); setEntries(d.entries || []); setNote(""); }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} style={{ width: 90 }} />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (not a fill)" style={{ flex: 1 }} />
        <button className="btn">Add note</button>
      </form>
      <table className="table">
        <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th><th>Reasons</th><th>Summary</th><th>Provenance</th><th>Audit</th></tr></thead>
        <tbody>
          {entries.length === 0 && <tr><td colSpan={9} className="muted">{JOURNAL_EMPTY}</td></tr>}
          {entries.map((e, i) => (
            <tr key={e.log_id || e.id || i} onClick={async () => {
              if (!e.log_id) return;
              try {
                const payload = await api.journalEntry(e.log_id);
                setDetail(JSON.stringify({ entry: payload.entry, audit: payload.audit }, null, 2));
              }
              catch (err: any) { setDetail(err.message || "not found"); }
            }}>
              <td>{String(e.ts || "").replace("T", " ").slice(0, 19)}</td>
              <td>{e.symbol}</td>
              <td>{e.side || "—"}</td>
              <td>{e.qty || "—"}</td>
              <td>{journalStatusLabel(e)}</td>
              <td>{(e.reason_codes || []).join(" · ") || "—"}</td>
              <td>{journalSummaryLabel(e)}</td>
              <td>{e.provenance?.source || "unknown"} · unverified</td>
              <td>{e.links?.audit_ref ? "open" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail && <pre className="muted" style={{ whiteSpace: "pre-wrap" }}>{detail}</pre>}
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
      {mandateGap(ops) && <p className="muted">{mandateGap(ops)}</p>}
      <p className="muted">{PAPER_GATE}</p>
    </div>
  );
}
