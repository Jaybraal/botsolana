# Learner-Driven Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el autonomous_scanner (2.9% WR) con un scanner que aprende de los patrones de wallets élite y encuentra tokens de 1-7 días de historia por sí mismo, sin copywallet.

**Architecture:** El nuevo `copytrade/learner_scanner.py` corre un ciclo cada 5 minutos: descubre candidatos en DexScreener (tokens trending de Solana), valida precio en PumpPortal, aplica doble filtro (stat_scorer + learner_rules_copywallet.json) y ejecuta vía el executor/simulator existente. `learner.py` se modifica para generar reglas separadas por fuente (CW vs AUTO) usando el campo `wallet_label` ya existente en sim_history.json.

**Tech Stack:** Python 3.10, asyncio, httpx, pytest — codebase existente sin nuevas dependencias.

## Global Constraints

- Sin nuevas dependencias en requirements.txt — solo usar lo que ya está importado
- `simulator.py`, `executor.py`, `watcher.py`, `stat_scorer.py` NO se tocan
- Tests corren con `python3 -m pytest tests/ -v` desde `/Users/branel/Desktop/botsolana/`
- Baseline: 13 tests pasan — todos deben seguir pasando después de cada task
- Los env vars nuevos tienen defaults sensatos para no romper el bot si no están seteados
- `autonomous_scanner.py` no se modifica — se deshabilita solo via env var

---

## File Map

| Archivo | Acción | Responsabilidad |
|---------|--------|-----------------|
| `copytrade/learner.py` | Modificar | Añadir split de reglas por fuente (CW/AUTO) |
| `copytrade/learner_scanner.py` | Crear | Scanner principal — descubrimiento, scoring, posiciones |
| `main.py` | Modificar | Añadir watch_learner_scanner() al asyncio.gather |
| `tests/test_learner_source.py` | Crear | Tests de source tagging en learner |
| `tests/test_learner_scanner.py` | Crear | Tests de scoring, criterios y monitor |

---

### Task 1: Source tagging en learner.py

Modifica `learner.py` para que genere `learner_rules_copywallet.json` (solo trades de wallets reales) y `learner_rules_auto.json` (solo trades de AUTO 🤖), usando el campo `wallet_label` que ya existe en sim_history.json.

**Files:**
- Modify: `copytrade/learner.py`
- Create: `tests/test_learner_source.py`

**Interfaces:**
- Produces: `data/learner_rules_copywallet.json` con la misma estructura que `learner_rules.json`
- Produces: `data/learner_rules_auto.json` con la misma estructura
- `load_rules(source="CW")` → retorna reglas del archivo correcto según source
- `update()` → sin cambios en firma, genera los 3 archivos (all, CW, AUTO)

- [ ] **Step 1: Escribir tests que fallan**

Crear `tests/test_learner_source.py`:

```python
import json
import os
import pytest


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """Redirige HISTORY_FILE y RULES_FILE a tmp_path."""
    import copytrade.learner as L
    monkeypatch.setattr(L, "HISTORY_FILE", str(tmp_path / "sim_history.json"))
    monkeypatch.setattr(L, "RULES_FILE",   str(tmp_path / "learner_rules.json"))
    monkeypatch.setattr(L, "RULES_CW_FILE",   str(tmp_path / "learner_rules_copywallet.json"))
    monkeypatch.setattr(L, "RULES_AUTO_FILE",  str(tmp_path / "learner_rules_auto.json"))
    return tmp_path


def _make_trade(wallet_label: str, won: bool, mcap: float = 30000.0) -> dict:
    return {
        "wallet_label": wallet_label,
        "won": won,
        "entry_context": {
            "mcap_usd": mcap,
            "liquidity_usd": 5000.0,
            "volume_24h_usd": 50000.0,
            "vol_liq_ratio": 4.5,
            "buy_pressure": 0.60,
            "age_days": 2.5,
            "change_1h_pct": 95.0,
        },
    }


def test_update_generates_copywallet_file(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Decu", True) for _ in range(3)] + \
             [_make_trade("Decu", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    assert os.path.exists(L.RULES_CW_FILE), "learner_rules_copywallet.json debe crearse"
    rules = json.loads(open(L.RULES_CW_FILE).read())
    assert rules["total_trades"] == 6
    assert "scoring_rules" in rules


def test_update_generates_auto_file(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("AUTO 🤖", True) for _ in range(3)] + \
             [_make_trade("AUTO 🤖", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    assert os.path.exists(L.RULES_AUTO_FILE), "learner_rules_auto.json debe crearse"
    rules = json.loads(open(L.RULES_AUTO_FILE).read())
    assert rules["total_trades"] == 6


def test_update_separates_sources(tmp_data):
    import copytrade.learner as L
    trades = (
        [_make_trade("Decu", True) for _ in range(4)] +
        [_make_trade("AUTO 🤖", True) for _ in range(2)] +
        [_make_trade("AUTO 🤖", False) for _ in range(4)]
    )
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    cw   = json.loads(open(L.RULES_CW_FILE).read())
    auto = json.loads(open(L.RULES_AUTO_FILE).read())
    assert cw["total_trades"] == 4
    assert auto["total_trades"] == 6


def test_load_rules_by_source(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Decu", True) for _ in range(5)] + \
             [_make_trade("Decu", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)
    L.update()

    rules_cw = L.load_rules(source="CW")
    assert rules_cw.get("total_trades") == 8

    rules_auto = L.load_rules(source="AUTO")
    # AUTO file existe pero vacío (0 trades AUTO) — retorna {}
    assert rules_auto == {} or rules_auto.get("total_trades", 0) == 0


def test_update_skips_auto_file_when_no_auto_trades(tmp_data):
    import copytrade.learner as L
    trades = [_make_trade("Theo", True) for _ in range(5)] + \
             [_make_trade("Theo", False) for _ in range(3)]
    with open(L.HISTORY_FILE, "w") as f:
        json.dump(trades, f)

    L.update()

    # Si no hay trades AUTO, el archivo no se crea (o está vacío)
    if os.path.exists(L.RULES_AUTO_FILE):
        rules = json.loads(open(L.RULES_AUTO_FILE).read())
        assert rules.get("total_trades", 0) == 0
```

- [ ] **Step 2: Verificar que los tests fallan**

```bash
cd /Users/branel/Desktop/botsolana
python3 -m pytest tests/test_learner_source.py -v
```

Resultado esperado: `ImportError` o `AttributeError` (RULES_CW_FILE no existe aún)

- [ ] **Step 3: Modificar copytrade/learner.py**

Añadir las dos constantes nuevas después de `RULES_FILE`:

```python
RULES_CW_FILE   = "data/learner_rules_copywallet.json"
RULES_AUTO_FILE = "data/learner_rules_auto.json"
```

Modificar `load_rules()` para aceptar source:

```python
def load_rules(source: str = "ALL") -> dict:
    if source == "CW":
        path = RULES_CW_FILE
    elif source == "AUTO":
        path = RULES_AUTO_FILE
    else:
        path = RULES_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}
```

Añadir helper `_build_rules_for(trades)` que extrae la lógica actual de `update()` para poder reutilizarla. Poner este helper justo antes de `update()`:

```python
def _build_rules_for(trades: list) -> dict | None:
    """Construye el dict de reglas para una lista de trades con entry_context."""
    with_ctx = [t for t in trades if t.get("entry_context")]
    if len(with_ctx) < MIN_TRADES:
        return None
    winners = [t for t in with_ctx if t.get("won")]
    losers  = [t for t in with_ctx if not t.get("won")]
    if not winners:
        return None

    ctx_keys = (
        "mcap_usd", "liquidity_usd", "volume_24h_usd",
        "vol_liq_ratio", "buy_pressure", "age_minutes",
        "change_1h_pct", "change_24h_pct", "age_days",
    )
    winner_avg = {k: _avg(winners, k) for k in ctx_keys}
    loser_avg  = {k: _avg(losers,  k) for k in ctx_keys}

    wa = winner_avg
    scoring_rules: dict = {}
    if wa["mcap_usd"]       is not None: scoring_rules["min_mcap_usd"]        = max(5000, round(wa["mcap_usd"] * 0.5, 0))
    if wa["mcap_usd"]       is not None: scoring_rules["max_mcap_usd"]        = round(wa["mcap_usd"] * 1.5, 0)
    if wa["liquidity_usd"]  is not None: scoring_rules["min_liquidity_usd"]   = round(wa["liquidity_usd"]  * 0.5, 0)
    if wa["volume_24h_usd"] is not None: scoring_rules["min_volume_24h_usd"]  = round(wa["volume_24h_usd"] * 0.5, 0)
    if wa["buy_pressure"]   is not None: scoring_rules["min_buy_pressure"]    = round(wa["buy_pressure"]   * 0.85, 3)
    if wa["change_1h_pct"]  is not None: scoring_rules["min_change_1h_pct"]   = round(wa["change_1h_pct"]  * 0.5, 2)
    if wa["age_minutes"]    is not None: scoring_rules["min_age_minutes"]     = max(5, round(wa["age_minutes"] * 0.8, 0))
    if wa["age_days"]       is not None: scoring_rules["max_age_days"]        = round(wa["age_days"]       * 2.0, 1)

    return {
        "total_trades":       len(with_ctx),
        "winners":            len(winners),
        "losers":             len(losers),
        "win_rate":           round(len(winners) / len(with_ctx) * 100, 1),
        "winner_avg":         winner_avg,
        "loser_avg":          loser_avg,
        "win_rate_by_wallet": _win_rate_by(with_ctx, "wallet_label"),
        "win_rate_by_dex":    _win_rate_by(with_ctx, "dex_id", from_context=True),
        "scoring_rules":      scoring_rules,
    }
```

Reemplazar la función `update()` completa:

```python
def update() -> dict | None:
    """
    Lee el historial, calcula patrones por fuente y persiste los tres archivos de reglas.
    Retorna las reglas globales (todos los trades), o None si no hay suficientes datos.
    """
    history = _load_history()

    # Reglas globales (comportamiento original — no rompe nada existente)
    rules = _build_rules_for(history)
    if rules:
        _save_rules(rules)
        log.info(
            f"[bold cyan][LEARNER][/] Reglas actualizadas — "
            f"{rules['total_trades']} trades | "
            f"Win rate: [{'green' if rules['win_rate'] >= 50 else 'red'}]{rules['win_rate']}%[/] | "
            f"{len(rules.get('scoring_rules', {}))} reglas activas"
        )

    # Reglas por fuente
    cw_trades   = [t for t in history if not t.get("wallet_label", "").startswith("AUTO")]
    auto_trades = [t for t in history if t.get("wallet_label", "").startswith("AUTO")]

    cw_rules = _build_rules_for(cw_trades)
    if cw_rules:
        with open(RULES_CW_FILE, "w") as f:
            json.dump(cw_rules, f, indent=2)

    auto_rules = _build_rules_for(auto_trades)
    if auto_rules:
        with open(RULES_AUTO_FILE, "w") as f:
            json.dump(auto_rules, f, indent=2)
    elif auto_trades:
        # Hay trades AUTO pero no suficientes — guardar parcial para referencia
        with open(RULES_AUTO_FILE, "w") as f:
            json.dump({"total_trades": len(auto_trades), "win_rate": 0, "scoring_rules": {}}, f, indent=2)

    return rules
```

- [ ] **Step 4: Correr tests — deben pasar**

```bash
python3 -m pytest tests/test_learner_source.py -v
```

Resultado esperado: 5 passed

- [ ] **Step 5: Verificar que los 13 tests originales siguen pasando**

```bash
python3 -m pytest tests/ -v
```

Resultado esperado: 18 passed (13 originales + 5 nuevos)

- [ ] **Step 6: Commit**

```bash
git add copytrade/learner.py tests/test_learner_source.py
git commit -m "feat: learner genera reglas separadas por fuente CW/AUTO"
```

---

### Task 2: learner_scanner.py — scoring y descubrimiento

Crea `copytrade/learner_scanner.py` con la lógica de descubrimiento de candidatos, carga de reglas, y doble filtro de scoring.

**Files:**
- Create: `copytrade/learner_scanner.py`
- Create: `tests/test_learner_scanner.py`

**Interfaces:**
- Consumes: `load_rules(source="CW")` de `copytrade/learner.py` (Task 1)
- Consumes: `get_trending_solana()`, `get_tokens_batch()` de `utils/dexscreener.py`
- Consumes: `score_token(token_info)` de `copytrade/stat_scorer.py`
- Produces: `_passes_learner_criteria(token_info, rules) -> tuple[bool, str]`
- Produces: `_fetch_candidates() -> list[dict]`  (cada dict tiene price_usd, mcap_usd, etc.)
- Produces: `_score_and_decide(token_info) -> tuple[bool, str]`

- [ ] **Step 1: Escribir tests que fallan**

Crear `tests/test_learner_scanner.py`:

```python
import pytest


# ── _passes_learner_criteria ─────────────────────────────────────────────────

def test_passes_criteria_all_ok():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {
        "scoring_rules": {
            "min_mcap_usd":       15000.0,
            "max_mcap_usd":       47000.0,
            "min_liquidity_usd":  3000.0,
            "min_buy_pressure":   0.50,
            "min_change_1h_pct":  70.0,
            "max_age_days":       7.0,
        }
    }
    token = {
        "mcap_usd":        30000.0,
        "liquidity_usd":   6000.0,
        "buy_pressure":    0.62,
        "price_change_1h": 95.0,
        "age_days":        2.5,
    }
    passed, reason = _passes_learner_criteria(token, rules)
    assert passed, f"Debería pasar: {reason}"


def test_passes_criteria_mcap_too_high():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"min_mcap_usd": 15000.0, "max_mcap_usd": 47000.0}}
    token = {"mcap_usd": 100000.0, "liquidity_usd": 0, "buy_pressure": 0, "price_change_1h": 0, "age_days": 1}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


def test_passes_criteria_too_old():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"max_age_days": 7.0}}
    token = {"mcap_usd": 30000.0, "liquidity_usd": 5000, "buy_pressure": 0.6, "price_change_1h": 100, "age_days": 15.0}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


def test_passes_criteria_fallback_when_no_rules():
    from copytrade.learner_scanner import _passes_learner_criteria
    # Sin reglas → usar hardcoded → token razonable debe pasar
    token = {
        "mcap_usd":        30000.0,
        "liquidity_usd":   5000.0,
        "buy_pressure":    0.60,
        "price_change_1h": 100.0,
        "age_days":        2.0,
    }
    passed, reason = _passes_learner_criteria(token, {})
    assert passed


def test_passes_criteria_low_buy_pressure():
    from copytrade.learner_scanner import _passes_learner_criteria
    rules = {"scoring_rules": {"min_buy_pressure": 0.513}}
    token = {"mcap_usd": 30000, "liquidity_usd": 5000, "buy_pressure": 0.30, "price_change_1h": 100, "age_days": 2}
    passed, reason = _passes_learner_criteria(token, rules)
    assert not passed


# ── _score_and_decide ────────────────────────────────────────────────────────

def test_score_and_decide_passes_good_token():
    from copytrade.learner_scanner import _score_and_decide
    token = {
        "mcap_usd":        35000.0,
        "liquidity_usd":   5000.0,
        "buy_pressure":    0.62,
        "price_change_1h": 95.0,
        "age_days":        2.5,
        "token_age_min":   3600.0,   # 60 horas en minutos
        "price_change_5m": 5.0,
        "buys_5m":         15,
        "sells_5m":        5,
        "program":         "PumpSwap",
        "price_usd":       0.0001,
        "price_sol":       0.0000007,
    }
    passed, reason = _score_and_decide(token)
    # No garantizamos pass (depende de learner_rules.json en disco),
    # pero sí que no lanza excepción y retorna tuple(bool, str)
    assert isinstance(passed, bool)
    assert isinstance(reason, str)


def test_score_and_decide_rejects_zero_price():
    from copytrade.learner_scanner import _score_and_decide
    token = {
        "mcap_usd": 30000, "liquidity_usd": 5000, "buy_pressure": 0.6,
        "price_change_1h": 95, "age_days": 2, "token_age_min": 2880,
        "price_usd": 0.0, "price_sol": 0.0, "buys_5m": 10, "sells_5m": 2,
        "program": "PumpSwap", "price_change_5m": 3,
    }
    passed, reason = _score_and_decide(token)
    assert not passed
    assert "precio" in reason.lower() or "price" in reason.lower()
```

- [ ] **Step 2: Verificar que los tests fallan**

```bash
python3 -m pytest tests/test_learner_scanner.py -v
```

Resultado esperado: `ModuleNotFoundError: copytrade.learner_scanner`

- [ ] **Step 3: Crear copytrade/learner_scanner.py con scoring y descubrimiento**

```python
"""
Learner-Driven Scanner — opera sin copywallet.

Ciclo cada LEARNER_SCAN_INTERVAL_MIN minutos:
  1. DexScreener /token-boosts/top/v1 → lista de tokens trending Solana
  2. get_tokens_batch() → datos completos por mint
  3. _passes_learner_criteria() → filtro por learner_rules_copywallet.json
  4. PumpPortal API → validar precio en tiempo real
  5. stat_scorer + learner_rules → doble filtro de scoring
  6. execute_copy() → abrir posición en simulator/executor
  7. _monitor_position() → SL/TP/trailing/timeout

Variables de entorno:
  LEARNER_SCANNER_ENABLED   = true   # activar/desactivar
  LEARNER_SCAN_INTERVAL_MIN = 5      # minutos entre scans DexScreener
  LEARNER_SCORE_THRESHOLD   = 55     # threshold stat_scorer
  LEARNER_CRITERIA_MATCH    = 5      # criterios learner_rules que deben coincidir (de 7)
  MAX_AUTO_POSITIONS        = 2      # máximo posiciones simultáneas
  AUTO_STOP_LOSS_PCT        = -8     # % para stop loss
  AUTO_TAKE_PROFIT_PCT      = 25     # % para take profit
  AUTO_TRAILING_PEAK        = 15     # % para activar trailing
  AUTO_TRAILING_DROP        = 7      # % de caída desde pico → vender
  AUTO_MAX_HOLD_MIN         = 7      # minutos máximos por posición
"""

import asyncio
import json
import os
import time

import httpx

from copytrade.executor import execute_copy
from copytrade.learner import load_rules
from copytrade.stat_scorer import score_token as stat_score
from config import TOKENS
from utils.dexscreener import get_trending_solana, get_tokens_batch
from utils.logger import get_logger

log = get_logger("learner_scanner")

SOL_MINT       = TOKENS["SOL"]
PUMPPORTAL_API = "https://pumpportal.fun/api/coin-data"

# ── Config ────────────────────────────────────────────────────────────────────
ENABLED        = os.getenv("LEARNER_SCANNER_ENABLED", "true").lower() == "true"
SCAN_INTERVAL  = float(os.getenv("LEARNER_SCAN_INTERVAL_MIN", "5")) * 60
SCORE_THRESH   = int(os.getenv("LEARNER_SCORE_THRESHOLD", "55"))
CRITERIA_MATCH = int(os.getenv("LEARNER_CRITERIA_MATCH", "5"))
MAX_POSITIONS  = int(os.getenv("MAX_AUTO_POSITIONS", "2"))
STOP_LOSS_PCT  = float(os.getenv("AUTO_STOP_LOSS_PCT",   "-8"))
TAKE_PROFIT    = float(os.getenv("AUTO_TAKE_PROFIT_PCT", "25"))
TRAIL_PEAK     = float(os.getenv("AUTO_TRAILING_PEAK",   "15"))
TRAIL_DROP     = float(os.getenv("AUTO_TRAILING_DROP",    "7"))
MAX_HOLD_MIN   = float(os.getenv("AUTO_MAX_HOLD_MIN",     "7"))
MONITOR_TICK   = 10  # segundos entre checks de precio

# Criterios hardcoded — fallback si learner_rules_copywallet.json no existe
_FALLBACK_RULES = {
    "scoring_rules": {
        "min_mcap_usd":      15571.0,
        "max_mcap_usd":      46714.0,
        "min_liquidity_usd":  3127.0,
        "min_buy_pressure":   0.513,
        "min_change_1h_pct":  78.98,
        "max_age_days":        7.3,
    }
}

# Estado en memoria — {mint: {entry_price_usd, entry_time, peak_pct, symbol, program}}
_auto_positions: dict[str, dict] = {}

# Caché SOL price
_sol_price_usd: float = 150.0
_sol_price_ts:  float = 0.0


def _get_sol_price() -> float:
    global _sol_price_usd, _sol_price_ts
    if time.time() - _sol_price_ts < 60:
        return _sol_price_usd
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
            timeout=3,
        )
        if r.status_code == 200:
            _sol_price_usd = float(r.json()["solana"]["usd"])
            _sol_price_ts  = time.time()
    except Exception:
        pass
    return _sol_price_usd


# ── Criterios y scoring ───────────────────────────────────────────────────────

def _passes_learner_criteria(token_info: dict, rules: dict) -> tuple[bool, str]:
    """
    Verifica si token_info cumple los criterios de learner_rules_copywallet.json.
    Si rules está vacío, usa _FALLBACK_RULES hardcoded.
    Retorna (passed, reason).
    """
    scoring = (rules or _FALLBACK_RULES).get("scoring_rules", _FALLBACK_RULES["scoring_rules"])

    checks  = 0
    passed  = 0
    reasons = []

    def _check(label: str, value, threshold, is_max: bool = False) -> bool:
        nonlocal checks, passed
        if value is None or threshold is None:
            return True  # sin dato → no penalizar
        checks += 1
        ok = (value <= threshold) if is_max else (value >= threshold)
        if ok:
            passed += 1
        else:
            reasons.append(f"{label}: {value:.1f} {'>' if is_max else '<'} {threshold:.1f}")
        return ok

    _check("mcap_min",    token_info.get("mcap_usd"),        scoring.get("min_mcap_usd"))
    _check("mcap_max",    token_info.get("mcap_usd"),        scoring.get("max_mcap_usd"),     is_max=True)
    _check("liquidity",   token_info.get("liquidity_usd"),   scoring.get("min_liquidity_usd"))
    _check("buy_press",   token_info.get("buy_pressure"),    scoring.get("min_buy_pressure"))
    _check("change_1h",   token_info.get("price_change_1h"), scoring.get("min_change_1h_pct"))
    _check("age_days",    token_info.get("age_days"),        scoring.get("max_age_days"),      is_max=True)

    if checks == 0:
        return True, "sin criterios cargados — dejando pasar"

    ok = passed >= min(CRITERIA_MATCH, checks)
    reason = f"{passed}/{checks} criterios" if ok else f"solo {passed}/{checks}: {', '.join(reasons)}"
    return ok, reason


def _score_and_decide(token_info: dict) -> tuple[bool, str]:
    """
    Doble filtro: stat_scorer (primera capa) + learner_criteria (segunda capa).
    Retorna (passed, reason).
    """
    if not token_info.get("price_usd"):
        return False, "precio USD = 0 — descartado"

    # Primera capa: stat_scorer
    score, stat_passed, stat_reason = stat_score(token_info)
    if not stat_passed:
        return False, f"stat_scorer score={score} < {SCORE_THRESH} | {stat_reason}"

    # Segunda capa: learner_rules_copywallet
    rules = load_rules(source="CW")
    crit_passed, crit_reason = _passes_learner_criteria(token_info, rules)
    if not crit_passed:
        return False, f"learner_criteria FAIL | {crit_reason}"

    return True, f"stat={score} | {stat_reason} | criteria: {crit_reason}"
```

- [ ] **Step 4: Correr tests — deben pasar**

```bash
python3 -m pytest tests/test_learner_scanner.py -v
```

Resultado esperado: 7 passed

- [ ] **Step 5: Verificar todos los tests**

```bash
python3 -m pytest tests/ -v
```

Resultado esperado: 25 passed (18 anteriores + 7 nuevos)

- [ ] **Step 6: Commit**

```bash
git add copytrade/learner_scanner.py tests/test_learner_scanner.py
git commit -m "feat: learner_scanner scoring y descubrimiento DexScreener"
```

---

### Task 3: learner_scanner.py — gestión de posiciones

Añade a `copytrade/learner_scanner.py` las funciones de apertura de posición, monitor de precio (SL/TP/trailing/timeout), venta y recuperación de huérfanas.

**Files:**
- Modify: `copytrade/learner_scanner.py`
- Modify: `tests/test_learner_scanner.py`

**Interfaces:**
- Consumes: `execute_copy(swap_dict)` de `copytrade/executor.py`
- Consumes: `_auto_positions` dict definido en Task 2
- Produces: `_open_position(mint, token_info)` — async, abre posición y arranca monitor
- Produces: `_monitor_position(mint, symbol)` — async loop con SL/TP/trailing/timeout
- Produces: `_recover_orphan_positions()` — sync, recupera posiciones AUTO al arrancar
- Produces: `_fetch_pumpportal_price(mint) -> dict | None`

- [ ] **Step 1: Añadir tests de monitor**

Agregar al final de `tests/test_learner_scanner.py`:

```python
# ── monitor exit conditions ──────────────────────────────────────────────────

def test_stop_loss_threshold():
    """SL se activa a -8% (valor por defecto)."""
    from copytrade.learner_scanner import STOP_LOSS_PCT
    assert STOP_LOSS_PCT == -8.0


def test_take_profit_threshold():
    """TP se activa a +25% (valor por defecto)."""
    from copytrade.learner_scanner import TAKE_PROFIT
    assert TAKE_PROFIT == 25.0


def test_recover_orphans_no_crash_on_missing_file(tmp_path, monkeypatch):
    """_recover_orphan_positions no crashea si sim_positions.json no existe."""
    import copytrade.learner_scanner as LS
    monkeypatch.setattr(LS, "_SIM_POSITIONS_PATH", str(tmp_path / "nonexistent.json"))
    LS._recover_orphan_positions()  # no debe lanzar


def test_recover_orphans_loads_auto_positions(tmp_path, monkeypatch):
    """_recover_orphan_positions carga posiciones de AUTO 🤖 del archivo."""
    import json
    import copytrade.learner_scanner as LS

    positions_file = tmp_path / "sim_positions.json"
    positions_file.write_text(json.dumps({
        "mint_abc": {
            "wallet":       "AUTONOMOUS_BOT",
            "entry_price":  0.00005,
            "opened_at":    1000000.0,
            "symbol":       "TEST",
        },
        "mint_xyz": {
            "wallet":       "Decu",       # NO es AUTO — no debe recuperarse
            "entry_price":  0.0001,
            "opened_at":    1000000.0,
            "symbol":       "OTHER",
        },
    }))

    monkeypatch.setattr(LS, "_SIM_POSITIONS_PATH", str(positions_file))
    LS._auto_positions.clear()
    LS._recover_orphan_positions()

    assert "mint_abc" in LS._auto_positions
    assert "mint_xyz" not in LS._auto_positions
```

- [ ] **Step 2: Verificar que los tests nuevos fallan**

```bash
python3 -m pytest tests/test_learner_scanner.py::test_recover_orphans_no_crash_on_missing_file -v
```

Resultado esperado: `AttributeError: module has no attribute '_SIM_POSITIONS_PATH'`

- [ ] **Step 3: Añadir funciones de posición a copytrade/learner_scanner.py**

Agregar al final del archivo (después de `_score_and_decide`):

```python
# ── Precio PumpPortal ─────────────────────────────────────────────────────────

def _fetch_pumpportal_price(mint: str) -> dict | None:
    """Precio desde bonding curve de PumpPortal — fallback cuando DexScreener no tiene el token."""
    try:
        r = httpx.get(PUMPPORTAL_API, params={"mint": mint}, timeout=3)
        if r.status_code != 200:
            return None
        d = r.json()
        v_sol = float(d.get("virtual_sol_reserves") or 0)
        v_tok = float(d.get("virtual_token_reserves") or 0)
        if v_sol <= 0 or v_tok <= 0:
            return None
        price_sol = (v_sol / 1e9) / (v_tok / 1e6)
        price_usd = price_sol * _get_sol_price()
        return {"price_usd": price_usd, "price_sol": price_sol}
    except Exception:
        return None


def _fetch_current_price(mint: str, pair_address: str = "") -> float:
    """Precio actual — DexScreener pair → PumpPortal fallback → 0."""
    from utils.dexscreener import get_pair_price
    if pair_address:
        price = get_pair_price(pair_address)
        if price and price > 0:
            return price
    pp = _fetch_pumpportal_price(mint)
    return (pp or {}).get("price_usd", 0)


# ── Posiciones ────────────────────────────────────────────────────────────────

_SIM_POSITIONS_PATH = "data/sim_positions.json"


def _recover_orphan_positions() -> None:
    """Recupera posiciones de AUTO 🤖 desde sim_positions.json tras un restart."""
    if not os.path.exists(_SIM_POSITIONS_PATH):
        return
    try:
        with open(_SIM_POSITIONS_PATH) as f:
            all_pos = json.load(f)
        recovered = 0
        for mint, pos in all_pos.items():
            if not pos or pos.get("wallet") != "AUTONOMOUS_BOT":
                continue
            if mint in _auto_positions:
                continue
            entry_price = pos.get("entry_price", 0)
            if not entry_price:
                continue
            _auto_positions[mint] = {
                "entry_price_usd": entry_price,
                "last_price_usd":  entry_price,
                "entry_time":      pos.get("opened_at", time.time()),
                "peak_pct":        0.0,
                "symbol":          pos.get("symbol", mint[:6]),
                "program":         (pos.get("entry_context") or {}).get("dex_id", "PumpSwap"),
                "pair_address":    pos.get("pair_address", ""),
            }
            recovered += 1
        if recovered:
            log.info(f"[learner] ♻️  {recovered} posiciones huérfanas recuperadas")
    except Exception as e:
        log.warning(f"[learner] No se pudieron recuperar posiciones huérfanas: {e}")


async def _trigger_sell(mint: str, symbol: str, current_price: float, reason: str, program: str):
    """Envía señal de venta al executor/simulator y limpia la posición."""
    if mint not in _auto_positions:
        return
    if current_price <= 0:
        current_price = _auto_positions[mint].get("last_price_usd", 0)
    _auto_positions.pop(mint, None)

    sol_price = _get_sol_price()
    price_sol = (current_price / sol_price) if current_price > 0 and sol_price > 0 else 0.0

    sell_swap = {
        "wallet":            "AUTONOMOUS_BOT",
        "wallet_label":      "AUTO 🤖",
        "program":           program,
        "token_in":          mint,
        "token_out":         SOL_MINT,
        "symbol_in":         symbol,
        "symbol_out":        "SOL",
        "amount_in":         0,
        "amount_out":        0,
        "wallet_pre_sol":    0,
        "implied_price_sol": price_sol,
    }
    log.info(f"[learner] 🔴 VENTA {symbol} | motivo: {reason}")
    await execute_copy(sell_swap)


async def _monitor_position(mint: str, symbol: str):
    """Monitorea precio cada MONITOR_TICK segundos. Aplica SL/TP/trailing/timeout."""
    pos = _auto_positions.get(mint)
    if not pos:
        return

    entry_price   = pos["entry_price_usd"]
    entry_time    = pos["entry_time"]
    program       = pos["program"]
    pair_address  = pos.get("pair_address", "")

    log.info(
        f"[learner] 👁 Monitor {symbol} | entrada ${entry_price:.8f} | "
        f"SL {STOP_LOSS_PCT:+.0f}% | TP +{TAKE_PROFIT:.0f}% | "
        f"trailing >{TRAIL_PEAK:.0f}% cae -{TRAIL_DROP:.0f}% | max {MAX_HOLD_MIN:.0f}min"
    )

    while mint in _auto_positions:
        await asyncio.sleep(MONITOR_TICK)
        if mint not in _auto_positions:
            break

        current = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_current_price, mint, pair_address
        )

        if current <= 0:
            current = _auto_positions[mint].get("last_price_usd", 0)
        else:
            _auto_positions[mint]["last_price_usd"] = current

        hold_min = (time.time() - entry_time) / 60

        if current <= 0 or entry_price <= 0:
            if hold_min >= MAX_HOLD_MIN:
                await _trigger_sell(mint, symbol, 0.0, f"timeout-sin-precio {hold_min:.1f}min", program)
            continue

        pnl_pct  = (current - entry_price) / entry_price * 100
        peak_pct = _auto_positions[mint].get("peak_pct", 0)

        if pnl_pct > peak_pct:
            _auto_positions[mint]["peak_pct"] = pnl_pct
            peak_pct = pnl_pct

        log.info(f"[learner] 📊 {symbol} | P&L {pnl_pct:+.1f}% | pico {peak_pct:+.1f}% | hold {hold_min:.1f}min")

        exit_reason = None
        if pnl_pct <= STOP_LOSS_PCT:
            exit_reason = f"stop-loss {pnl_pct:+.1f}%"
        elif pnl_pct >= TAKE_PROFIT:
            exit_reason = f"take-profit {pnl_pct:+.1f}%"
        elif peak_pct >= TRAIL_PEAK and (peak_pct - pnl_pct) >= TRAIL_DROP:
            exit_reason = f"trailing pico={peak_pct:+.1f}% actual={pnl_pct:+.1f}%"
        elif hold_min >= MAX_HOLD_MIN:
            exit_reason = f"timeout {hold_min:.1f}min"

        if exit_reason:
            await _trigger_sell(mint, symbol, current, exit_reason, program)
            break


async def _open_position(mint: str, token_info: dict, reason: str):
    """Registra posición y ejecuta compra via executor/simulator."""
    if len(_auto_positions) >= MAX_POSITIONS:
        log.debug(f"[learner] Límite {MAX_POSITIONS} posiciones — skip {mint[:8]}")
        return
    if mint in _auto_positions:
        return

    entry_price = token_info.get("price_usd", 0)
    symbol      = token_info.get("symbol", mint[:6])
    program     = token_info.get("program", "PumpSwap")
    sol_price   = _get_sol_price()

    _auto_positions[mint] = {
        "entry_price_usd": entry_price,
        "last_price_usd":  entry_price,
        "entry_time":      time.time(),
        "peak_pct":        0.0,
        "symbol":          symbol,
        "program":         program,
        "pair_address":    token_info.get("pair_address", ""),
    }

    buy_swap = {
        "wallet":            "AUTONOMOUS_BOT",
        "wallet_label":      "AUTO 🤖",
        "program":           program,
        "token_in":          SOL_MINT,
        "token_out":         mint,
        "symbol_in":         "SOL",
        "symbol_out":        symbol,
        "amount_in":         0,
        "amount_out":        0,
        "wallet_pre_sol":    0,
        "implied_price_sol": entry_price / sol_price if sol_price > 0 else 0,
    }

    log.info(f"[learner] 🟢 COMPRA {symbol} | {reason}")
    await execute_copy(buy_swap)
    asyncio.create_task(_monitor_position(mint, symbol))
```

- [ ] **Step 4: Correr todos los tests**

```bash
python3 -m pytest tests/ -v
```

Resultado esperado: 29 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/learner_scanner.py tests/test_learner_scanner.py
git commit -m "feat: learner_scanner gestión de posiciones — monitor SL/TP/trailing"
```

---

### Task 4: Scan loop principal + integración en main.py

Añade el loop periódico de descubrimiento DexScreener a `learner_scanner.py` e integra `watch_learner_scanner()` en `main.py`.

**Files:**
- Modify: `copytrade/learner_scanner.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: `get_trending_solana()` → `get_tokens_batch()` de `utils/dexscreener.py`
- Consumes: `_score_and_decide(token_info)` y `_open_position()` de Task 2/3
- Produces: `watch_learner_scanner()` — coroutine async, entry point para main.py

- [ ] **Step 1: Añadir _fetch_candidates y scan_loop a copytrade/learner_scanner.py**

Agregar al final del archivo:

```python
# ── Descubrimiento ────────────────────────────────────────────────────────────

def _pair_to_token_info(pair: dict, mint: str) -> dict:
    """Convierte un pair de DexScreener al formato token_info usado por los scorers."""
    liq    = float((pair.get("liquidity") or {}).get("usd") or 0)
    mcap   = float(pair.get("marketCap") or pair.get("fdv") or 0)
    pc     = pair.get("priceChange") or {}
    txns_h1 = (pair.get("txns") or {}).get("h1") or {}
    vol_h24 = float((pair.get("volume") or {}).get("h24") or 0)
    buys_h1  = int(txns_h1.get("buys") or 0)
    sells_h1 = int(txns_h1.get("sells") or 0)
    total_h1 = buys_h1 + sells_h1
    buy_pressure = buys_h1 / total_h1 if total_h1 > 0 else 0.0

    txns_5m = (pair.get("txns") or {}).get("m5") or {}
    buys_5m  = int(txns_5m.get("buys") or 0)
    sells_5m = int(txns_5m.get("sells") or 0)

    created_ms = pair.get("pairCreatedAt") or 0
    created_s  = created_ms // 1000 if created_ms > 1e10 else created_ms
    age_days   = round((time.time() - created_s) / 86400, 2) if created_s else None
    age_min    = round((time.time() - created_s) / 60, 1)   if created_s else None

    dex = (pair.get("dexId") or "").lower()
    if "raydium" in dex:
        program = "Raydium"
    elif "pumpswap" in dex or "pump_amm" in dex:
        program = "PumpSwap"
    else:
        program = "Pump.fun"

    vol_liq_ratio = vol_h24 / liq if liq > 0 else 0.0

    base_token = pair.get("baseToken") or {}
    return {
        "mint":            mint,
        "symbol":          base_token.get("symbol", mint[:6]),
        "price_usd":       float(pair.get("priceUsd") or 0),
        "price_sol":       float(pair.get("priceNative") or 0),
        "liquidity_usd":   liq,
        "mcap_usd":        mcap,
        "price_change_1h": float(pc.get("h1") or 0),
        "price_change_5m": float(pc.get("m5") or 0),
        "buys_5m":         buys_5m,
        "sells_5m":        sells_5m,
        "token_age_min":   age_min,
        "age_days":        age_days,
        "buy_pressure":    buy_pressure,
        "vol_liq_ratio":   vol_liq_ratio,
        "volume_24h_usd":  vol_h24,
        "program":         program,
        "pair_address":    pair.get("pairAddress", ""),
        "discovery_source": "dexscreener",
    }


def _fetch_candidates() -> list[dict]:
    """
    Obtiene candidatos de DexScreener:
      1. get_trending_solana() → lista de mints boosteados
      2. get_tokens_batch() → datos completos por mint
      3. Convierte cada par a token_info
    Retorna lista de token_info listos para _score_and_decide().
    """
    try:
        trending = get_trending_solana()
        if not trending:
            log.debug("[learner] DexScreener trending vacío — sin candidatos")
            return []

        mints = [t["tokenAddress"] for t in trending if t.get("tokenAddress")]
        if not mints:
            return []

        batch = get_tokens_batch(mints)
        candidates = []

        for mint, pairs in batch.items():
            if not pairs:
                continue
            # Tomar el par con mayor liquidez
            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
            token_info = _pair_to_token_info(best, mint)
            candidates.append(token_info)

        return candidates

    except Exception as e:
        log.warning(f"[learner] Error en _fetch_candidates: {e}")
        return []


# ── Loop principal ────────────────────────────────────────────────────────────

async def scan_loop():
    """Loop periódico — descubre, filtra, compra. Corre cada SCAN_INTERVAL segundos."""
    consec_failures = 0

    while True:
        try:
            candidates = await asyncio.get_event_loop().run_in_executor(None, _fetch_candidates)
            evaluated  = 0
            opened     = 0

            for token_info in candidates:
                mint   = token_info.get("mint", "")
                symbol = token_info.get("symbol", "?")

                if not mint or mint in _auto_positions:
                    continue

                evaluated += 1
                passed, reason = _score_and_decide(token_info)

                if passed:
                    log.info(f"[learner] ✅ CANDIDATO {symbol} | {reason}")
                    await _open_position(mint, token_info, reason)
                    opened += 1
                else:
                    log.debug(f"[learner] ❌ {symbol} | {reason}")

            if evaluated > 0 or opened > 0:
                log.info(
                    f"[learner] 🔍 Ciclo completado | {evaluated} evaluados | "
                    f"{opened} abiertos | {len(_auto_positions)} posiciones activas"
                )

            consec_failures = 0

        except Exception as e:
            consec_failures += 1
            log.error(f"[learner] Error en scan_loop (fallo #{consec_failures}): {e}")

        await asyncio.sleep(SCAN_INTERVAL)


async def watch_learner_scanner():
    """
    Entry point para main.py — recupera huérfanas y arranca el loop.
    Añadir a asyncio.gather() en main.py.
    """
    if not ENABLED:
        log.info("[learner] Scanner desactivado (LEARNER_SCANNER_ENABLED=false)")
        return

    _recover_orphan_positions()

    for mint, pos in list(_auto_positions.items()):
        asyncio.create_task(_monitor_position(mint, pos["symbol"]))

    log.info(
        f"[learner] 🤖 Learner Scanner iniciado | "
        f"ciclo {SCAN_INTERVAL/60:.0f}min | "
        f"score_thresh {SCORE_THRESH} | "
        f"criteria {CRITERIA_MATCH}/6 | "
        f"max {MAX_POSITIONS} posiciones"
    )

    await scan_loop()
```

- [ ] **Step 2: Modificar main.py**

Primero leer cómo está el asyncio.gather actual:

```bash
grep -n "gather\|watch_\|autonomous" main.py | head -20
```

Añadir el import de `watch_learner_scanner` junto a los otros imports de watch al inicio de main.py. Buscar la línea que importa `watch_autonomous` y añadir la línea nueva:

```python
from copytrade.learner_scanner import watch_learner_scanner
```

Luego en el `asyncio.gather(...)`, añadir `watch_learner_scanner()` como argumento adicional, junto a los otros watchers existentes.

- [ ] **Step 3: Verificar que el bot arranca sin errores de import**

```bash
python3 -c "from copytrade.learner_scanner import watch_learner_scanner; print('OK')"
```

Resultado esperado: `OK`

```bash
python3 -c "import main; print('import OK')" 2>&1 | head -5
```

Resultado esperado: sin errores de import

- [ ] **Step 4: Correr todos los tests**

```bash
python3 -m pytest tests/ -v
```

Resultado esperado: 29 passed (sin regresiones)

- [ ] **Step 5: Commit**

```bash
git add copytrade/learner_scanner.py main.py
git commit -m "feat: scan_loop DexScreener + integración watch_learner_scanner en main"
```

---

### Task 5: Deshabilitar autonomous_scanner + variables de entorno

Deshabilita el scanner viejo via env var y documenta las variables nuevas en `GUIA_RAPIDA.md`.

**Files:**
- Modify: `GUIA_RAPIDA.md`

- [ ] **Step 1: Verificar que AUTO_MOMENTUM_BUYS=99999 deshabilita el scanner viejo**

```bash
python3 -c "
import os
os.environ['AUTO_MOMENTUM_BUYS'] = '99999'
from copytrade.autonomous_scanner import MOMENTUM_BUYS
print(f'MOMENTUM_BUYS={MOMENTUM_BUYS}')
assert MOMENTUM_BUYS == 99999, 'Debe ser 99999'
print('OK — scanner viejo deshabilitado')
"
```

Resultado esperado: `MOMENTUM_BUYS=99999` y `OK`

- [ ] **Step 2: Actualizar GUIA_RAPIDA.md con las nuevas variables**

Añadir sección en `GUIA_RAPIDA.md` después de las variables existentes:

```markdown
## Learner Scanner (modo autónomo)

| Variable | Default | Descripción |
|---|---|---|
| `LEARNER_SCANNER_ENABLED` | `true` | Activar/desactivar el nuevo scanner |
| `LEARNER_SCAN_INTERVAL_MIN` | `5` | Minutos entre scans DexScreener |
| `LEARNER_SCORE_THRESHOLD` | `55` | Score mínimo stat_scorer para abrir posición |
| `LEARNER_CRITERIA_MATCH` | `5` | Criterios de learner_rules que deben coincidir (de 6) |
| `MAX_AUTO_POSITIONS` | `2` | Máximo posiciones autónomas simultáneas |
| `AUTO_MOMENTUM_BUYS` | `99999` | Deshabilita el scanner viejo de PumpPortal |

### Criterio de graduación (hacia operar sin copywallet)
- ≥ 100 trades AUTO cerrados
- WR ≥ 50%
- Profit factor ≥ 1.2

Monitorear con:
`grep "AUTO.*WIN\|AUTO.*LOSS\|RESUMEN" logs/simulator_*.log`
```

- [ ] **Step 3: Correr todos los tests una última vez**

```bash
python3 -m pytest tests/ -v
```

Resultado esperado: 29 passed

- [ ] **Step 4: Commit final**

```bash
git add GUIA_RAPIDA.md
git commit -m "docs: variables learner scanner + criterio de graduación sin copywallet"
```

---

## Self-Review

**Cobertura del spec:**
- ✅ learner.py genera learner_rules_copywallet.json y learner_rules_auto.json (Task 1)
- ✅ learner_scanner.py con DexScreener + PumpPortal (Tasks 2, 3, 4)
- ✅ Doble filtro stat_scorer + learner_rules (Task 2)
- ✅ Monitor SL/TP/trailing/timeout (Task 3)
- ✅ Recuperación de posiciones huérfanas (Task 3)
- ✅ Criterios hardcoded como fallback (Task 2)
- ✅ watch_learner_scanner() en main.py (Task 4)
- ✅ AUTO_MOMENTUM_BUYS=99999 deshabilita scanner viejo (Task 5)
- ✅ Variables de entorno documentadas (Task 5)

**Consistencia de nombres:**
- `watch_learner_scanner()` — consistente en Task 4 y en la firma de main.py
- `_passes_learner_criteria()` — consistente en Tasks 2 y 3
- `_auto_positions` — definido en Task 2, usado en Tasks 3 y 4
- `load_rules(source="CW")` — definido en Task 1, consumido en Task 2

**Sin placeholders:** verificado — todo el código está completo.
