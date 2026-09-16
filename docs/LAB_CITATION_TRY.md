# Lab citation-try（`lab/citation-try`）

**目的：** 對照「裝可選庫」vs「唔裝」嘅*可審計*指標，**唔**用 Yahoo 12 檔 bakeoff 贏 SPY 當勝負。  
**紅線：** LLM／FinBERT／Advisory／QuantStats **永遠唔下單**；唔改 breakout MACD／RS／1R 硬閘。

## 主線（唔停）

1. Desk AI Mode 揀具名 **`breakout`** → Start（或 systemd `deploy/alphadesk-worker.service`）。
2. `python scripts/check_paper_calendar.py` 睇心跳／skip／paper vs SPY／Polygon。
3. 有 `POLYGON_API_KEY` 先用付費日 K；否則 Yahoo fallback，**唔改 live 敘事**。
4. ≥30 曆日（目標 90）先 `scripts/run_evaluation.py --seal`。

## Lab extras（預設關）

```bash
# optional heavy deps
.venv/Scripts/pip install -r requirements-lab.txt

# flags (process env)
set LAB_INSTRUCTOR=1
set LAB_FINBERT=0
set LAB_QUANTSTATS=0
set LAB_ADVISORY=1
# soft veto still separate:
set WORKER_SENTIMENT_VETO=0
```

| Flag | 作用 | 對照指標 |
|------|------|----------|
| `LAB_INSTRUCTOR` | Scan LLM → Pydantic／Instructor schema | `lab_schema_metrics.json` success_rate |
| `LAB_FINBERT` | 新聞情緒用 FinBERT（無則 VADER） | agreement_rate vs VADER |
| `LAB_QUANTSTATS` | tear HTML；關則 JSON stub | tear.complete |
| `LAB_ADVISORY` | Bull/Bear/Risk 草稿；可建議改 top-N | rewrites；**worker 忽略** |

```bash
python scripts/run_lab_ab_compare.py --demo --enable-lab-flags
# → backend/data/lab_ab_report.json
```

API：`GET /api/lab/status` · `GET /api/lab/ab-report` · `POST /api/lab/advisory` · `POST /api/lab/tear`

## 仍唔裝／永不

見 [`CITATION_REFUSALS.md`](CITATION_REFUSALS.md)。
