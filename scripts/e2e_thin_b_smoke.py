"""Thin Slice B API e2e smoke checks."""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000"
bugs: list[str] = []


def get(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=60) as r:
            return r.status, json.load(r)
    except Exception as exc:  # noqa: BLE001
        bugs.append(f"GET {path}: {exc}")
        return None, None


def check(name: str, cond: bool, detail="") -> None:
    if cond:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}  {detail}")
        bugs.append(f"{name}: {detail}")


st, health = get("/api/health")
check("health", st == 200 and (health or {}).get("status") == "ok", health)

st, meta = get("/api/meta")
check("meta.scan_auto", bool((meta or {}).get("scan_auto")), meta)
check("meta.next_scan_at", bool((meta or {}).get("next_scan_at")), meta)
check("meta.scan_as_of", "scan_as_of" in (meta or {}), meta)

st, scan = get("/api/scan/latest")
check("scan.available_key", "available" in (scan or {}), scan)
recs = (scan or {}).get("recommendations") or []
ranks = (scan or {}).get("rankings") or []
has_book_prov = isinstance((scan or {}).get("provenance"), dict)
row_prov = False
sample = (recs[0] if recs else None) or (ranks[0] if ranks else None)
if isinstance(sample, dict):
    row_prov = isinstance(sample.get("provenance"), dict)
    if (scan or {}).get("available") and (recs or ranks) and not has_book_prov and not row_prov:
        bugs.append("scan.provenance missing after backfill")
        print("FAIL  scan.provenance missing after backfill")
    else:
        check("scan.provenance_ok", bool(has_book_prov or row_prov or not (recs or ranks)))
if has_book_prov:
    book = (scan or {}).get("provenance") or {}
    check(
        "scan.book_vendor_displayable",
        bool(book.get("vendor") or book.get("vendor_primary")),
        book,
    )


st, perf = get("/api/performance?refresh=1")
check("performance.http", st == 200, perf)
paper = (perf or {}).get("paper") or {}
check("performance.paper_object", isinstance(paper, dict) and "benchmark" in paper, list(paper.keys()))
bench = paper.get("benchmark") or {}
check("performance.cost_bps", bench.get("assumed_cost_bps") is not None, bench)
check("performance.disclaimer", bool(paper.get("disclaimer")), paper.get("disclaimer", "")[:80])
check("performance.top_level_alpha", "alpha_net_of_costs_pct" in (perf or {}), list((perf or {}).keys())[:20])

# Empty-book honesty: beats_spy_net should be None when no trades, not pretend YES
if paper.get("empty_book"):
    check(
        "performance.empty_beats_null",
        bench.get("beats_spy_net") is None,
        f"beats={bench.get('beats_spy_net')} alpha={bench.get('alpha_net_of_costs_pct')}",
    )

st, worker = get("/api/worker/status")
check("worker.http", st == 200, worker)
research = (worker or {}).get("research") or {}
msg = (research.get("message_en") or "") + (research.get("message_zh") or "")
check(
    "worker.research_no_fake_50",
    "default to 50" not in msg.lower() and "變成 50" not in msg and "变成 50" not in msg,
    msg[:220],
)
check(
    "worker.next_scan_visible",
    bool(worker.get("next_scan_at") or worker.get("scan_auto") is not None or "下次" in msg or "next=" in msg.lower()),
    {"next": worker.get("next_scan_at"), "auto": worker.get("scan_auto"), "msg": msg[:160]},
)

st, deep = get("/api/deep/latest")
reports = (deep or {}).get("reports") or {}
if reports:
    first = next(iter(reports.values()))
    if isinstance(first, dict) and not first.get("error") and "provenance" not in first:
        print("WARN  deep.provenance missing on cached report (new deep run needed)")
    else:
        print("PASS  deep.latest present")
else:
    print("SKIP  deep.latest empty")

# desk static serves
try:
    with urllib.request.urlopen(BASE + "/", timeout=15) as r:
        html = r.read().decode("utf-8", errors="replace")
    check("desk.html", "Cgab" in html and ("desk.vs_spy" in html or "紙上 vs SPY" in html or "Paper vs SPY" in html), "missing vs SPY i18n/copy")
    check("desk.provenance_js", "provenanceChip" in html, "provenanceChip helper missing")
except Exception as exc:  # noqa: BLE001
    bugs.append(f"GET /: {exc}")
    print(f"FAIL  desk.html  {exc}")

print("\n=== BUGS ===")
if bugs:
    for b in bugs:
        print("-", b)
else:
    print("none")
raise SystemExit(1 if bugs else 0)
