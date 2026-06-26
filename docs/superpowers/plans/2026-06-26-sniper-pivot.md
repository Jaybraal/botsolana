# BotSolana Sniper Pivot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el bot de copy-trader tardío a sniper autónomo data-driven usando los 4,913 trades reales de `wallet_history.db`.

**Architecture:** `snipe_trainer.py` lee la DB y genera `snipe_patterns.json` con WR por bucket (edad/mcap/momentum). `snipe_scorer.py` carga ese JSON y puntúa tokens en tiempo real. `autonomous_scanner.py` usa el nuevo scorer en vez de `stat_scorer` cuando `SNIPE_MODE=true`. `watcher.py` registra en un set compartido cuando una wallet élite compra un token, lo que boost el score en el scanner.

**Tech Stack:** Python 3, sqlite3, json, pytest, asyncio (existente)

## Global Constraints

- Python 3.10+ — usar `dict | None` y `tuple[int, bool, str]` como tipos
- Mismo contrato de `score_token` que `stat_scorer.py`: `(int, bool, str)` — nunca romper esta firma
- No tocar `executor.py`, `simulator.py`, ni `decoder.py` — están fuera de scope
- Todos los paths de archivos son relativos a `/Users/branel/Desktop/botsolana/`
- Tests en `tests/` — crear directorio si no existe
- Instalar pytest antes de ejecutar tests: `pip install pytest`
- Commits frecuentes, uno por tarea

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `data_collector/snipe_trainer.py` | **CREAR** | Lee DB, calcula WR por bucket, escribe `snipe_patterns.json` |
| `copytrade/snipe_scorer.py` | **CREAR** | Carga patterns, `score_token(token_info, elite_signal) -> (int, bool, str)` |
| `copytrade/signals.py` | **CREAR** | Set compartido de mints donde wallets élite compraron |
| `copytrade/autonomous_scanner.py` | **MODIFICAR** | Usar `snipe_scorer` cuando `SNIPE_MODE=true`; consultar elite signal |
| `copytrade/watcher.py` | **MODIFICAR** | Registrar en signals cuando wallet élite hace BUY |
| `config.py` | **MODIFICAR** | Agregar `SNIPE_MODE`, `ELITE_WALLETS`, ajustar defaults |
| `.env` | **MODIFICAR** | Activar SNIPE_MODE, AUTONOMOUS_MODE, parámetros corregidos |
| `requirements.txt` | **MODIFICAR** | Agregar `pytest` |
| `tests/__init__.py` | **CREAR** | Marcar directorio como paquete Python |
| `tests/test_snipe_trainer.py` | **CREAR** | Tests del trainer |
| `tests/test_snipe_scorer.py` | **CREAR** | Tests del scorer |
| `tests/test_signals.py` | **CREAR** | Tests del módulo de señales |

---

## Task 1: snipe_trainer.py

**Files:**
- Create: `data_collector/snipe_trainer.py`
- Create: `tests/__init__.py`
- Create: `tests/test_snipe_trainer.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produce: `data/snipe_patterns.json` con estructura:
  ```json
  {
    "generated_at": "ISO string",
    "total_trades": int,
    "age_buckets": [{"label": str, "min": float, "max": float, "wr": float, "n": int, "score_pts": int}],
    "mcap_buckets": [...same shape...],
    "buys_buckets": [...same shape...],
    "elite_wallet_boost": 15,
    "buy_threshold": 55
  }
  ```
- Produce: función `compute_patterns() -> dict` (consumida por tests)

- [ ] **Step 1: Agregar pytest a requirements**

Abrir `requirements.txt` y añadir al final:
```
pytest
```

Instalar:
```bash
pip install pytest
```

- [ ] **Step 2: Crear directorio tests**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 3: Escribir el test del trainer**

Crear `tests/test_snipe_trainer.py`:

```python
import sqlite3, json, os, pytest
from pathlib import Path


def _make_test_db(path: str):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY, wallet TEXT, wallet_label TEXT, tx_sig TEXT,
        ts INTEGER, token_mint TEXT, token_symbol TEXT, program TEXT,
        sol_spent REAL, price_usd REAL, mcap_usd REAL, liquidity_usd REAL,
        token_age_min REAL, pair_created_at INTEGER, price_change_5m REAL,
        price_change_1h REAL, buys_5m INTEGER, sells_5m INTEGER,
        sell_ts INTEGER, hold_min REAL, pnl_pct REAL, outcome TEXT
    )""")
    rows = [
        # 3 wins en ventana 1-3 min, mcap 30-70k, buys 50-150
        (1,"w1","Theo","s1",1,"m1","A","Pump.fun",0.1,1e-6,50000,0,2.0,1,0,0,80,10,2,5.0,120.0,"WIN"),
        (2,"w1","Theo","s2",2,"m2","B","Pump.fun",0.1,1e-6,50000,0,1.5,2,0,0,60,8, 3,4.0, 80.0,"WIN"),
        (3,"w1","Theo","s3",3,"m3","C","Pump.fun",0.1,1e-6,40000,0,2.5,3,0,0,100,15,4,3.0, 50.0,"WIN"),
        # 1 loss en 30+ min, mcap bajo, pocos buys
        (4,"w2","Decu","s4",4,"m4","D","Pump.fun",0.1,1e-6,10000,0,35.0,4,0,0,5,2, 5,60.0,-30.0,"LOSS"),
        # 1 UNKNOWN — debe ser ignorado
        (5,"w2","Decu","s5",5,"m5","E","Pump.fun",0.1,1e-6,50000,0,2.0, 5,0,0,80,10,0,0.0,  0.0,"UNKNOWN"),
    ]
    c.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_and_out(tmp_path):
    db = str(tmp_path / "test.db")
    out = str(tmp_path / "patterns.json")
    _make_test_db(db)
    os.environ["DB_PATH"] = db
    os.environ["PATTERNS_PATH"] = out
    yield db, out
    os.environ.pop("DB_PATH", None)
    os.environ.pop("PATTERNS_PATH", None)


def _get_trainer():
    import importlib
    import data_collector.snipe_trainer as m
    importlib.reload(m)
    return m


def test_only_win_loss_counted(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    assert p["total_trades"] == 4  # excluye UNKNOWN


def test_age_bucket_1_3min(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    b = next(x for x in p["age_buckets"] if x["label"] == "1-3min")
    assert b["n"] == 3
    assert b["wr"] == 100.0
    assert b["score_pts"] >= 35  # máximo para WR >= 90%


def test_age_bucket_30plus_low_pts(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    b = next(x for x in p["age_buckets"] if x["label"] == "30+min")
    assert b["n"] == 1
    assert b["wr"] == 0.0
    assert b["score_pts"] <= 15


def test_output_file_written(db_and_out):
    _, out = db_and_out
    trainer = _get_trainer()
    trainer.main()
    assert Path(out).exists()
    with open(out) as f:
        data = json.load(f)
    assert "age_buckets" in data
    assert "mcap_buckets" in data
    assert "buys_buckets" in data
    assert data["buy_threshold"] == 55
```

- [ ] **Step 4: Correr tests — esperar FAIL (módulo no existe aún)**

```bash
cd /Users/branel/Desktop/botsolana
python -m pytest tests/test_snipe_trainer.py -v 2>&1 | head -20
```

Esperado: `ModuleNotFoundError: No module named 'data_collector.snipe_trainer'`

- [ ] **Step 5: Crear `data_collector/snipe_trainer.py`**

```python
"""
Genera data/snipe_patterns.json desde wallet_history.db.
Ejecutar una vez antes de arrancar el bot:
    python3 -m data_collector.snipe_trainer
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/wallet_history.db")
OUTPUT_PATH = os.getenv("PATTERNS_PATH", "data/snipe_patterns.json")

_AGE_BUCKETS = [
    {"label": "<1min",    "min": 0,      "max": 1   },
    {"label": "1-3min",   "min": 1,      "max": 3   },
    {"label": "3-10min",  "min": 3,      "max": 10  },
    {"label": "10-30min", "min": 10,     "max": 30  },
    {"label": "30+min",   "min": 30,     "max": 9999},
]

_MCAP_BUCKETS = [
    {"label": "<10k",    "min": 0,       "max": 10000     },
    {"label": "10-30k",  "min": 10000,   "max": 30000     },
    {"label": "30-70k",  "min": 30000,   "max": 70000     },
    {"label": "70-150k", "min": 70000,   "max": 150000    },
    {"label": "150k+",   "min": 150000,  "max": 999999999 },
]

_BUYS_BUCKETS = [
    {"label": "<10",     "min": 0,   "max": 10    },
    {"label": "10-50",   "min": 10,  "max": 50    },
    {"label": "50-150",  "min": 50,  "max": 150   },
    {"label": "150-300", "min": 150, "max": 300   },
    {"label": "300+",    "min": 300, "max": 999999},
]


def _wr_to_pts(wr: float) -> int:
    """Convierte WR% a puntos de score (0-40). Derivado de los datos."""
    if wr >= 90:
        return 40
    if wr >= 87:
        return 35
    if wr >= 82:
        return 25
    if wr >= 79:
        return 15
    return 5


def _bucket_stats(cursor: sqlite3.Cursor, col: str, lo: float, hi: float) -> dict:
    cursor.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) "
        f"FROM trades "
        f"WHERE outcome IN ('WIN','LOSS') AND {col} >= ? AND {col} < ?",
        (lo, hi),
    )
    n, wins = cursor.fetchone()
    n = n or 0
    wins = wins or 0
    wr = round(wins / n * 100, 1) if n else 0.0
    return {"n": n, "wins": wins, "wr": wr}


def compute_patterns() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM trades WHERE outcome IN ('WIN','LOSS')")
    total = c.fetchone()[0]

    def _enrich(template_list: list[dict], col: str) -> list[dict]:
        result = []
        for b in template_list:
            stats = _bucket_stats(c, col, b["min"], b["max"])
            result.append({**b, **stats, "score_pts": _wr_to_pts(stats["wr"])})
        return result

    patterns = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_trades":       total,
        "age_buckets":        _enrich(_AGE_BUCKETS,  "token_age_min"),
        "mcap_buckets":       _enrich(_MCAP_BUCKETS, "mcap_usd"),
        "buys_buckets":       _enrich(_BUYS_BUCKETS, "buys_5m"),
        "elite_wallet_boost": 15,
        "buy_threshold":      55,
    }

    conn.close()
    return patterns


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"ERROR: DB no encontrada en {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    patterns = compute_patterns()
    Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(patterns, f, indent=2)

    print(f"✅ {OUTPUT_PATH} generado — {patterns['total_trades']} trades analizados")
    print("\nResumen por edad del token:")
    for b in patterns["age_buckets"]:
        print(f"  {b['label']:10s}: WR={b['wr']:5.1f}%  n={b['n']:4d}  pts={b['score_pts']}")
    print("\nResumen por mcap:")
    for b in patterns["mcap_buckets"]:
        print(f"  {b['label']:10s}: WR={b['wr']:5.1f}%  n={b['n']:4d}  pts={b['score_pts']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Correr tests — esperar PASS**

```bash
python -m pytest tests/test_snipe_trainer.py -v
```

Esperado:
```
tests/test_snipe_trainer.py::test_only_win_loss_counted PASSED
tests/test_snipe_trainer.py::test_age_bucket_1_3min PASSED
tests/test_snipe_trainer.py::test_age_bucket_30plus_low_pts PASSED
tests/test_snipe_trainer.py::test_output_file_written PASSED
4 passed
```

- [ ] **Step 7: Commit**

```bash
git add data_collector/snipe_trainer.py tests/__init__.py tests/test_snipe_trainer.py requirements.txt
git commit -m "feat: snipe_trainer — genera patrones WR desde wallet_history.db"
```

---

## Task 2: snipe_scorer.py + signals.py

**Files:**
- Create: `copytrade/snipe_scorer.py`
- Create: `copytrade/signals.py`
- Create: `tests/test_snipe_scorer.py`
- Create: `tests/test_signals.py`

**Interfaces:**
- Consumes: `data/snipe_patterns.json` (output de Task 1)
- Produces:
  - `score_token(token_info: dict, elite_signal: bool = False) -> tuple[int, bool, str]`
  - `should_buy(token_info: dict, elite_signal: bool = False) -> tuple[bool, str]`
  - `register_elite_buy(mint: str) -> None`
  - `is_elite_signal(mint: str) -> bool`
  - `clear_mint(mint: str) -> None`

- [ ] **Step 1: Escribir tests del scorer**

Crear `tests/test_snipe_scorer.py`:

```python
import json, os, pytest
from pathlib import Path

MOCK_PATTERNS = {
    "generated_at": "2026-06-26T00:00:00+00:00",
    "total_trades": 2487,
    "age_buckets": [
        {"label": "<1min",    "min": 0,  "max": 1,    "wr": 79.1, "n": 829, "score_pts": 15},
        {"label": "1-3min",   "min": 1,  "max": 3,    "wr": 90.6, "n": 287, "score_pts": 40},
        {"label": "3-10min",  "min": 3,  "max": 10,   "wr": 87.3, "n": 157, "score_pts": 35},
        {"label": "10-30min", "min": 10, "max": 30,   "wr": 81.6, "n": 158, "score_pts": 15},
        {"label": "30+min",   "min": 30, "max": 9999, "wr": 78.9, "n": 147, "score_pts": 5 },
    ],
    "mcap_buckets": [
        {"label": "<10k",    "min": 0,      "max": 10000,     "wr": 81.6, "score_pts": 15},
        {"label": "10-30k",  "min": 10000,  "max": 30000,     "wr": 81.9, "score_pts": 20},
        {"label": "30-70k",  "min": 30000,  "max": 70000,     "wr": 91.3, "score_pts": 30},
        {"label": "70-150k", "min": 70000,  "max": 150000,    "wr": 87.5, "score_pts": 20},
        {"label": "150k+",   "min": 150000, "max": 999999999, "wr": 74.5, "score_pts": 5 },
    ],
    "buys_buckets": [
        {"label": "<10",     "min": 0,   "max": 10,    "wr": 75.0, "score_pts": 5 },
        {"label": "10-50",   "min": 10,  "max": 50,    "wr": 78.0, "score_pts": 10},
        {"label": "50-150",  "min": 50,  "max": 150,   "wr": 90.5, "score_pts": 20},
        {"label": "150-300", "min": 150, "max": 300,   "wr": 85.0, "score_pts": 15},
        {"label": "300+",    "min": 300, "max": 999999,"wr": 87.5, "score_pts": 15},
    ],
    "elite_wallet_boost": 15,
    "buy_threshold": 55,
}


@pytest.fixture(autouse=True)
def patch_patterns(tmp_path):
    p = tmp_path / "snipe_patterns.json"
    p.write_text(json.dumps(MOCK_PATTERNS))
    os.environ["PATTERNS_PATH"] = str(p)
    import importlib
    import copytrade.snipe_scorer as m
    m._patterns = None
    yield
    m._patterns = None
    os.environ.pop("PATTERNS_PATH", None)


def test_gold_zone_passes():
    from copytrade.snipe_scorer import score_token
    # edad 1-3min (+40) + mcap 30-70k (+30) + buys 50-150 (+20) = 90 → PASS
    score, passed, reason = score_token({
        "token_age_min": 2.0,
        "mcap_usd": 50000,
        "buys_5m": 80,
    })
    assert passed
    assert score == 90
    assert "1-3min" in reason
    assert "30-70k" in reason


def test_old_token_fails():
    from copytrade.snipe_scorer import score_token
    # edad 30+min (+5) + mcap <10k (+15) + buys <10 (+5) = 25 → FAIL
    score, passed, _ = score_token({
        "token_age_min": 45.0,
        "mcap_usd": 5000,
        "buys_5m": 3,
    })
    assert not passed
    assert score == 25


def test_elite_boost_adds_15():
    from copytrade.snipe_scorer import score_token
    base_score, _, _ = score_token({"token_age_min": 2.0, "mcap_usd": 5000, "buys_5m": 3})
    boosted_score, _, reason = score_token(
        {"token_age_min": 2.0, "mcap_usd": 5000, "buys_5m": 3},
        elite_signal=True,
    )
    assert boosted_score == base_score + 15
    assert "élite" in reason


def test_empty_token_info_no_crash():
    from copytrade.snipe_scorer import score_token
    score, passed, reason = score_token({})
    assert isinstance(score, int)
    assert isinstance(passed, bool)
    assert reason == "sin señales"


def test_score_clamped_0_100():
    from copytrade.snipe_scorer import score_token
    score, _, _ = score_token({
        "token_age_min": 2.0,
        "mcap_usd": 50000,
        "buys_5m": 200,
        # todos los buckets máximos + boost = potencialmente >100
    }, elite_signal=True)
    assert 0 <= score <= 100


def test_missing_patterns_raises():
    import copytrade.snipe_scorer as m
    m._patterns = None
    os.environ["PATTERNS_PATH"] = "/nonexistent/path.json"
    with pytest.raises(FileNotFoundError, match="snipe_patterns.json"):
        m.score_token({"token_age_min": 2.0})
```

Crear `tests/test_signals.py`:

```python
def test_register_and_check():
    from copytrade.signals import register_elite_buy, is_elite_signal, clear_mint, _elite_mints
    _elite_mints.clear()
    register_elite_buy("mint_abc")
    assert is_elite_signal("mint_abc")
    assert not is_elite_signal("mint_xyz")


def test_clear_removes_mint():
    from copytrade.signals import register_elite_buy, is_elite_signal, clear_mint, _elite_mints
    _elite_mints.clear()
    register_elite_buy("mint_abc")
    clear_mint("mint_abc")
    assert not is_elite_signal("mint_abc")


def test_clear_nonexistent_no_crash():
    from copytrade.signals import clear_mint
    clear_mint("never_registered")  # no debe lanzar
```

- [ ] **Step 2: Correr tests — esperar FAIL**

```bash
python -m pytest tests/test_snipe_scorer.py tests/test_signals.py -v 2>&1 | head -15
```

Esperado: `ModuleNotFoundError: No module named 'copytrade.snipe_scorer'`

- [ ] **Step 3: Crear `copytrade/signals.py`**

```python
"""Señales compartidas entre watcher (copy) y autonomous_scanner (snipe)."""

_elite_mints: set[str] = set()


def register_elite_buy(mint: str) -> None:
    """Registra que una wallet élite compró este mint."""
    _elite_mints.add(mint)


def is_elite_signal(mint: str) -> bool:
    """¿Alguna wallet élite compró este mint?"""
    return mint in _elite_mints


def clear_mint(mint: str) -> None:
    """Limpia la señal cuando el bot ya abrió o descartó la posición."""
    _elite_mints.discard(mint)
```

- [ ] **Step 4: Crear `copytrade/snipe_scorer.py`**

```python
"""
Scorer data-driven para sniping de tokens nuevos en Pump.fun.
Contrato idéntico a stat_scorer: score_token(token_info) -> (int, bool, str)

Requiere data/snipe_patterns.json — generarlo primero con:
    python3 -m data_collector.snipe_trainer
"""
import json
import os
from pathlib import Path

from utils.logger import get_logger

log = get_logger("snipe_scorer")

PATTERNS_PATH = os.getenv("PATTERNS_PATH", "data/snipe_patterns.json")
_patterns: dict | None = None


def _load() -> dict:
    global _patterns
    if _patterns is None:
        p = Path(PATTERNS_PATH)
        if not p.exists():
            raise FileNotFoundError(
                f"snipe_patterns.json no encontrado en {PATTERNS_PATH}. "
                "Ejecuta: python3 -m data_collector.snipe_trainer"
            )
        with open(p) as f:
            _patterns = json.load(f)
    return _patterns


def _match_bucket(value: float, buckets: list[dict]) -> dict | None:
    for b in buckets:
        if b["min"] <= value < b["max"]:
            return b
    return None


def score_token(
    token_info: dict, elite_signal: bool = False
) -> tuple[int, bool, str]:
    """
    Evalúa un token nuevo con patrones derivados de wallet_history.db.
    Retorna (score 0-100, passed, reason_str).
    """
    p = _load()
    score = 0
    reasons: list[str] = []

    # ── Edad del token (feature más predictiva) ──────────────────────────
    age = token_info.get("token_age_min")
    if age is not None:
        b = _match_bucket(float(age), p["age_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(
                f"+{b['score_pts']} edad {float(age):.1f}min [{b['label']} WR={b['wr']}%]"
            )

    # ── Market cap ───────────────────────────────────────────────────────
    mcap = float(token_info.get("mcap_usd") or 0)
    if mcap > 0:
        b = _match_bucket(mcap, p["mcap_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(f"+{b['score_pts']} mcap ${mcap:,.0f} [{b['label']} WR={b['wr']}%]")

    # ── Momentum (buys acumulados) ───────────────────────────────────────
    buys = int(token_info.get("buys_5m") or 0)
    if buys > 0:
        b = _match_bucket(float(buys), p["buys_buckets"])
        if b:
            score += b["score_pts"]
            reasons.append(f"+{b['score_pts']} buys={buys} [{b['label']} WR={b['wr']}%]")

    # ── Señal de wallet élite ────────────────────────────────────────────
    if elite_signal:
        boost = int(p.get("elite_wallet_boost", 15))
        score += boost
        reasons.append(f"+{boost} wallet élite compró este token")

    score = max(0, min(100, score))
    threshold = int(p.get("buy_threshold", 55))
    passed = score >= threshold
    reason = " | ".join(reasons) if reasons else "sin señales"

    return score, passed, reason


def should_buy(
    token_info: dict, elite_signal: bool = False
) -> tuple[bool, str]:
    """Interfaz simple: (comprar, motivo)."""
    score, passed, reason = score_token(token_info, elite_signal)
    log.info(
        f"[snipe_scorer] score={score} {'✅ COMPRAR' if passed else '❌ SKIP'} | {reason}"
    )
    return passed, f"score={score} | {reason}"
```

- [ ] **Step 5: Correr tests — esperar PASS**

```bash
python -m pytest tests/test_snipe_scorer.py tests/test_signals.py -v
```

Esperado:
```
tests/test_snipe_scorer.py::test_gold_zone_passes PASSED
tests/test_snipe_scorer.py::test_old_token_fails PASSED
tests/test_snipe_scorer.py::test_elite_boost_adds_15 PASSED
tests/test_snipe_scorer.py::test_empty_token_info_no_crash PASSED
tests/test_snipe_scorer.py::test_score_clamped_0_100 PASSED
tests/test_snipe_scorer.py::test_missing_patterns_raises PASSED
tests/test_signals.py::test_register_and_check PASSED
tests/test_signals.py::test_clear_removes_mint PASSED
tests/test_signals.py::test_clear_nonexistent_no_crash PASSED
9 passed
```

- [ ] **Step 6: Commit**

```bash
git add copytrade/snipe_scorer.py copytrade/signals.py tests/test_snipe_scorer.py tests/test_signals.py
git commit -m "feat: snipe_scorer data-driven + signals compartidas"
```

---

## Task 3: Conectar snipe_scorer al autonomous_scanner

**Files:**
- Modify: `copytrade/autonomous_scanner.py` (líneas 36 y 357)

**Interfaces:**
- Consumes: `score_token` de `snipe_scorer` / `stat_scorer` según `SNIPE_MODE`
- Consumes: `is_elite_signal(mint)` y `clear_mint(mint)` de `signals`

- [ ] **Step 1: Reemplazar el import del scorer en `autonomous_scanner.py`**

Localizar línea 36:
```python
from copytrade.stat_scorer import score_token
```

Reemplazarla con:
```python
import os as _os
if _os.getenv("SNIPE_MODE", "false").lower() == "true":
    from copytrade.snipe_scorer import score_token
    from copytrade.signals import is_elite_signal, clear_mint as _clear_mint
else:
    from copytrade.stat_scorer import score_token as _stat_score

    def score_token(token_info: dict, elite_signal: bool = False) -> tuple[int, bool, str]:
        return _stat_score(token_info)

    def is_elite_signal(mint: str) -> bool:
        return False

    def _clear_mint(mint: str) -> None:
        pass
```

Esto garantiza que `score_token(token_info, elite_signal=elite)` funciona en ambos modos.

- [ ] **Step 2: Usar elite_signal en la llamada a score_token**

Localizar línea 357 (dentro de `_evaluate_token`):
```python
score, passed, reason = score_token(token_info)
```

Reemplazarla con:
```python
elite = is_elite_signal(mint)
score, passed, reason = score_token(token_info, elite_signal=elite)
```

- [ ] **Step 3: Limpiar la señal cuando se cierra la posición**

Localizar la función `_monitor_position` — buscar donde se llama `_auto_positions.pop(mint, None)` o similar al cerrar posición. Añadir `_clear_mint(mint)` justo antes o después de ese pop.

Buscar en `autonomous_scanner.py`:
```bash
grep -n "_auto_positions.pop\|position.*pop\|clear.*mint" copytrade/autonomous_scanner.py
```

En la línea encontrada (ejemplo: línea 290 — `execute_copy(sell_swap)`) buscar el bloque de cierre y añadir `_clear_mint(mint)` al final del bloque de cierre.

El bloque de cierre típicamente tiene:
```python
execute_copy(sell_swap)
_auto_positions.pop(mint, None)
```

Añadir:
```python
execute_copy(sell_swap)
_auto_positions.pop(mint, None)
_clear_mint(mint)
```

- [ ] **Step 4: Verificar que el bot arranca sin errores en modo SNIPE**

Primero generar los patrones (si no existen aún):
```bash
python3 -m data_collector.snipe_trainer
```

Luego probar import:
```bash
SNIPE_MODE=true python3 -c "
from copytrade.autonomous_scanner import _evaluate_token
print('✅ imports OK con SNIPE_MODE=true')
"
```

Esperado: `✅ imports OK con SNIPE_MODE=true`

También verificar modo legacy:
```bash
SNIPE_MODE=false python3 -c "
from copytrade.autonomous_scanner import _evaluate_token
print('✅ imports OK con SNIPE_MODE=false')
"
```

- [ ] **Step 5: Commit**

```bash
git add copytrade/autonomous_scanner.py
git commit -m "feat: autonomous_scanner usa snipe_scorer cuando SNIPE_MODE=true"
```

---

## Task 4: Registrar señal élite en watcher.py

**Files:**
- Modify: `copytrade/watcher.py` (dos puntos — handler Helius y handler PumpPortal)
- Modify: `config.py`

**Interfaces:**
- Consumes: `register_elite_buy(mint)` de `signals`
- Consumes: `ELITE_WALLETS` de `config`

- [ ] **Step 1: Añadir ELITE_WALLETS a config.py**

En `config.py`, después del bloque de `WALLET_LABELS` (aproximadamente línea 35), añadir:

```python
# Wallets con WR real > 84% (excluyen RC 0% y Trey 48.6%)
ELITE_WALLETS: frozenset[str] = frozenset({
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",  # Theo    89.3%
    "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC",  # Nyhrox  87.1%
    "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f",  # Cupsey  84.9%
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",  # Decu    84.4%
})

SNIPE_MODE = os.getenv("SNIPE_MODE", "false").lower() == "true"
```

- [ ] **Step 2: Importar ELITE_WALLETS y register_elite_buy en watcher.py**

En `watcher.py`, línea 22 (después de los imports de config):
```python
from config import RPC_HTTP, RPC_WS, RPC_WS_FALLBACK, TARGET_WALLETS, WALLET_LABELS, TOKENS
```

Cambiar a:
```python
from config import (
    RPC_HTTP, RPC_WS, RPC_WS_FALLBACK, TARGET_WALLETS,
    WALLET_LABELS, TOKENS, ELITE_WALLETS, SNIPE_MODE,
)
from copytrade.signals import register_elite_buy
```

- [ ] **Step 3: Registrar señal en handler de Helius (línea ~122)**

Localizar el bloque:
```python
wallet_addr = swap["wallet"]
if wallet_addr not in WALLET_LABELS:
    ...
label = WALLET_LABELS[wallet_addr]
swap["wallet_label"] = label
```

Añadir justo después de `swap["wallet_label"] = label`:
```python
if SNIPE_MODE and wallet_addr in ELITE_WALLETS:
    token_out = swap.get("token_out", "")
    if token_out and token_out != TOKENS.get("SOL", ""):
        register_elite_buy(token_out)
```

- [ ] **Step 4: Registrar señal en handler de PumpPortal (línea ~314)**

Localizar el bloque:
```python
wallet_addr  = swap["wallet"]
label        = WALLET_LABELS.get(wallet_addr, f"{wallet_addr[:8]}...")
swap["wallet_label"] = label
```

Añadir justo después de `swap["wallet_label"] = label`:
```python
if SNIPE_MODE and wallet_addr in ELITE_WALLETS:
    token_out = swap.get("token_out", "")
    if token_out and token_out != TOKENS.get("SOL", ""):
        register_elite_buy(token_out)
```

- [ ] **Step 5: Verificar que watcher importa sin error**

```bash
python3 -c "
import copytrade.watcher as w
print('✅ watcher imports OK')
print('ELITE_WALLETS cargadas:', len(__import__('config').ELITE_WALLETS))
"
```

Esperado:
```
✅ watcher imports OK
ELITE_WALLETS cargadas: 4
```

- [ ] **Step 6: Commit**

```bash
git add config.py copytrade/watcher.py
git commit -m "feat: watcher registra señal élite cuando SNIPE_MODE=true"
```

---

## Task 5: Config, .env y puesta en marcha

**Files:**
- Modify: `.env`

**Interfaces:**
- Sin nuevas interfaces — activación de todo lo construido

- [ ] **Step 1: Actualizar .env**

Abrir `.env` y asegurarse de que estos valores están presentes (crear o editar):

```bash
# Modo de operación
LIVE_MODE=false
AUTONOMOUS_MODE=true
SNIPE_MODE=true

# Scanner autónomo — calibrado con datos reales
AUTO_EVAL_DELAY_MIN=1
AUTO_MOMENTUM_BUYS=40
AUTO_MAX_HOLD_MIN=8
AUTO_STOP_LOSS_PCT=-20
AUTO_TAKE_PROFIT_PCT=80
AUTO_MAX_POSITIONS=3
AUTO_TRAILING_PEAK=20
AUTO_TRAILING_DROP=10

# Scorer
SCORER_THRESHOLD=55

# Target wallets — RC y Trey eliminados
TARGET_WALLETS=Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt,6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC,2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f,4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9,4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk,CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o,3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww
```

- [ ] **Step 2: Generar snipe_patterns.json**

```bash
cd /Users/branel/Desktop/botsolana
python3 -m data_collector.snipe_trainer
```

Esperado (valores aproximados):
```
✅ data/snipe_patterns.json generado — 2487 trades analizados

Resumen por edad del token:
  <1min     : WR= 79.1%  n= 829  pts=15
  1-3min    : WR= 90.6%  n= 287  pts=40
  3-10min   : WR= 87.3%  n= 157  pts=35
  10-30min  : WR= 81.6%  n= 158  pts=15
  30+min    : WR= 78.9%  n= 147  pts= 5
```

Si el output no muestra esos valores, revisar la DB: `python3 -c "import sqlite3; c=sqlite3.connect('data/wallet_history.db').cursor(); c.execute(\"SELECT COUNT(*) FROM trades WHERE outcome IN ('WIN','LOSS')\"); print(c.fetchone())"` — debe retornar ~2487.

- [ ] **Step 3: Correr todos los tests**

```bash
python -m pytest tests/ -v
```

Esperado: todos los tests en verde (`passed`), 0 errores.

- [ ] **Step 4: Smoke test del bot en modo snipe**

```bash
SNIPE_MODE=true AUTONOMOUS_MODE=true LIVE_MODE=false python3 -c "
import asyncio, os
os.environ.setdefault('SNIPE_MODE', 'true')
from copytrade.snipe_scorer import score_token
from copytrade.signals import register_elite_buy, is_elite_signal

# Simular un token en la zona de oro
token = {'token_age_min': 2.0, 'mcap_usd': 50000, 'buys_5m': 80}
score, passed, reason = score_token(token)
print(f'Token zona de oro: score={score}, passed={passed}')
assert passed, 'Debería pasar!'

# Simular boost élite
register_elite_buy('test_mint_123')
assert is_elite_signal('test_mint_123')
score2, passed2, reason2 = score_token(token, elite_signal=True)
print(f'Con boost élite: score={score2}')
assert score2 == score + 15

print('✅ Smoke test pasado')
"
```

Esperado:
```
Token zona de oro: score=90, passed=True
Con boost élite: score=100
✅ Smoke test pasado
```

- [ ] **Step 5: Arrancar el bot en tmux**

```bash
# Instalar tmux si no está
brew install tmux

# Configurar el Mac para no dormir mientras corre
# Ir a: Preferencias del Sistema → Batería → "Prevent computer from sleeping automatically" ON

# Arrancar
tmux new -s botsolana
cd /Users/branel/Desktop/botsolana
python3 main.py
# Ctrl+B, D  → desconectar (bot sigue corriendo)
```

- [ ] **Step 6: Verificar que el bot está detectando tokens**

Después de 5-10 minutos (Pump.fun crea cientos de tokens por día):

```bash
tmux attach -t botsolana
```

Buscar en la salida líneas como:
```
[auto] ❌ SKIP TOKEN_X | score=25 | +5 edad 0.3min [<1min WR=79.1%] | +15 mcap $8000 ...
[auto] ✅ COMPRAR TOKEN_Y | score=75 | +40 edad 1.8min [1-3min WR=90.6%] | +30 mcap $45000 ...
```

Si no aparece nada después de 10 minutos: verificar que PumpPortal WS está conectado buscando `[PumpPortal WS] Conectado` en los logs.

- [ ] **Step 7: Commit final**

```bash
git add .env data/snipe_patterns.json
git commit -m "config: activar snipe mode + parámetros data-driven + patterns generados"
```

---

## Criterios de validación (después de 7 días de SIM)

Para considerar el bot listo para live mode:

| Métrica | Umbral mínimo |
|---|---|
| Win Rate (neto con fees) | > 60% |
| Profit Factor | > 1.4 |
| Número de trades | ≥ 50 |
| Drawdown máximo | < 25% del capital SIM |

Comando de evaluación:
```bash
tmux attach -t botsolana
# buscar líneas RESUMEN o correr:
grep -E "(WIN|LOSS|RESUMEN)" ~/Desktop/botsolana/logs/bot.log 2>/dev/null | tail -50
```

Si los criterios se cumplen → activar `LIVE_MODE=true` con **mínimo $200 de capital real**.
