"""Why-no-trade codes — shared by strategies, worker heartbeat, desk, Telegram."""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

LABELS_EN: Dict[str, str] = {
    "already_held": "Already holding",
    "fund_lt_65": "Fundamental score below 65",
    "fund_below_min": "Fundamental score below strategy minimum",
    "llm_bearish": "LLM signal is bearish",
    "rsi_not_oversold": "RSI not oversold / no Connors pullback",
    "bb_not_low": "Price not at Bollinger lower band",
    "below_sma200": "Price below SMA200",
    "below_sma50": "Price below SMA50",
    "history_short": "Not enough price history",
    "no_price": "No OHLCV this cycle",
    "no_setup": "No entry setup",
    "no_vcp": "No VCP breakout",
    "volume_weak": "Volume surge missing",
    "rsi_overbought": "RSI already overbought",
    "research_stale": "Scan/signal as-of past max-age — no new buys (scores kept)",
    "not_in_scan_list": "Not in fresh Scan top-N",
    "kill_switch": "Kill switch on",
    "mandate": "Mandate rejected",
    "kelly_zero": "Kelly size is 0",
    "regime_gate": "Regime exposure cap",
    "universe_capped": "Not in this cycle's universe slice",
    "risk_rejected": "RiskEngine rejected",
    "not_stage2": "Not Minervini Stage-2",
    "rs_weak": "Relative strength vs SPY weak",
    "extended": "Too far from 52-week high / broken trend",
    "not_pullback": "No Stage-2 pullback reclaim",
    "regime_no_new_buys": "Regime blocks new buys",
    "macd_weak": "MACD histogram negative (breakout gate)",
    "sentiment_veto": "Scan sentiment/LLM veto (no new buy)",
    "order_open": "Open order already working",
    "not_defensive_asset": "Not GLD (defensive_gld only rotates into gold)",
    "not_defensive_regime": "Regime is not RED/PANIC — no GLD hedge",
    "cadence_wait": "Weekly cadence: wait for US Monday rebalance (unless PANIC)",
}

LABELS_ZH: Dict[str, str] = {
    "already_held": "已持有",
    "fund_lt_65": "基本面分數低於 65",
    "fund_below_min": "基本面分數低於策略門檻",
    "llm_bearish": "LLM 偏空",
    "rsi_not_oversold": "RSI 尚未超賣／無 Connors 回撤",
    "bb_not_low": "未碰到布林下軌",
    "below_sma200": "價格在 SMA200 之下",
    "below_sma50": "價格在 SMA50 之下",
    "history_short": "價格歷史不足",
    "no_price": "本輪沒有行情",
    "no_setup": "沒有進場型態",
    "no_vcp": "沒有 VCP 突破",
    "volume_weak": "成交量未放大",
    "rsi_overbought": "RSI 已超買",
    "research_stale": "掃描／訊號 as-of 超過新鮮度視窗（不開新倉；分數保留上次真實值）",
    "not_in_scan_list": "不在新鮮掃描前 N 名",
    "kill_switch": "急停中",
    "mandate": "授權拒絕",
    "kelly_zero": "凱利倉位為 0",
    "regime_gate": "市場狀態曝險上限",
    "universe_capped": "本輪宇宙切片未涵蓋",
    "risk_rejected": "風控拒絕",
    "not_stage2": "非 Minervini Stage-2",
    "rs_weak": "相對 SPY 強度偏弱",
    "extended": "距 52 週高過遠／趨勢破裂",
    "not_pullback": "無 Stage-2 回踩收復",
    "regime_no_new_buys": "市場狀態禁止開新倉",
    "macd_weak": "MACD 柱線為負（突破硬閘）",
    "sentiment_veto": "掃描情緒／LLM 否決（不開新倉）",
    "order_open": "已有未完成訂單",
    "not_defensive_asset": "不是 GLD（defensive_gld 只在紅／恐慌時買黃金）",
    "not_defensive_regime": "非 RED／PANIC 體制 — 不買 GLD 對沖",
    "cadence_wait": "週頻節奏：等到美東週一再平衡（PANIC 除外）",
}


def label(code: str, lang: str = "en") -> str:
    table = LABELS_ZH if str(lang).lower().startswith("zh") else LABELS_EN
    return table.get(code, code)


def top_skips(counts: Mapping[str, int], limit: int = 5) -> List[Tuple[str, int]]:
    rows = [(str(k), int(v)) for k, v in (counts or {}).items() if int(v) > 0]
    rows.sort(key=lambda kv: (-kv[1], kv[0]))
    return rows[: max(1, limit)]


def format_skip_lines(counts: Mapping[str, int], lang: str = "zh", limit: int = 5) -> str:
    rows = top_skips(counts, limit=limit)
    if not rows:
        return ""
    return " · ".join(f"{label(code, lang)} ×{n}" for code, n in rows)


def bump(counts: Dict[str, int], code: Optional[str], n: int = 1) -> None:
    if not code:
        return
    counts[code] = int(counts.get(code) or 0) + int(n)
