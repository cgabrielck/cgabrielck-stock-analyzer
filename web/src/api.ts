async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data?.detail ?? res.statusText;
    const msg =
      detail && typeof detail === "object" && detail.message
        ? String(detail.message)
        : typeof detail === "string"
          ? detail
          : JSON.stringify(detail);
    throw new Error(msg);
  }
  return data as T;
}

export const api = {
  meta: () => req<{ name: string; paper: boolean; ignore_market_hours: boolean }>("/api/meta"),
  account: () => req<any>("/api/account"),
  positions: () => req<{ positions: any[]; count: number }>("/api/positions"),
  orders: (status = "all") => req<{ orders: any[] }>(`/api/orders?status=${status}`),
  submitOrder: (body: object) => req<any>("/api/orders", { method: "POST", body: JSON.stringify(body) }),
  cancelOrder: (id: string) => req<any>(`/api/orders/${id}/cancel`, { method: "POST" }),
  ops: () => req<any>("/api/ops"),
  kill: () => req<any>("/api/ops/kill", { method: "POST" }),
  resume: () => req<any>("/api/ops/resume", { method: "POST" }),
  aiMode: () => req<any>("/api/ai-mode"),
  saveAiMode: (body: object) => req<any>("/api/ai-mode", { method: "PUT", body: JSON.stringify(body) }),
  watchlist: () => req<{ symbols: string[] }>("/api/watchlist"),
  addWatch: (symbol: string) => req<any>("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol }) }),
  delWatch: (symbol: string) => req<any>(`/api/watchlist/${symbol}`, { method: "DELETE" }),
  quotes: () => req<{ quotes: any[] }>("/api/quotes"),
  chart: (symbol: string) => req<{ symbol: string; bars: any[] }>(`/api/chart/${symbol}`),
  research: (symbol: string) => req<any>(`/api/research/${symbol}`),
  options: (symbol: string) => req<any>(`/api/options/${symbol}`),
  calendar: () => req<{ events: any[] }>("/api/calendar"),
  journal: (kind = "all") => req<{ entries: any[]; empty?: boolean; disclaimer?: string; disclaimer_zh?: string }>("/api/journal?limit=120&kind=" + encodeURIComponent(kind)),
  addJournal: (body: object) => req<any>("/api/journal", { method: "POST", body: JSON.stringify(body) }),
  journalEntry: (logId: string) => req<any>("/api/journal/entry/" + encodeURIComponent(logId)),
  journalAudit: (eventId: string) => req<any>("/api/journal/audit/" + encodeURIComponent(eventId)),
  performance: () => req<any>("/api/performance"),
  workerStatus: () => req<any>("/api/worker/status"),
  universe: () => req<{ universe: any[] }>("/api/universe"),
};
