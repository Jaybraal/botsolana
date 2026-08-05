# ROOT Decisor Evolutivo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el filtro de reglas (`score_token`/Groq-patterns) como decisor real de copytrade por ROOT, que aprende sin reglas fijas vía un ciclo evolutivo (generar variantes → backtestear → validar en vivo → promover) hasta converger en una configuración rentable.

**Architecture:** `scorer.should_copy()` delega en `root_decider.decide()`, que usa la config "campeón" persistida en `data/root_champion.json`. En paralelo, `root_evolution.py` corre en un thread daemon: muta una población de `Config` (pesos del scorer + stop-loss + max-hold + confianza por wallet), la backtestea contra el historial acumulado (1,219 trades semilla + trades propios + trayectorias de precio grabadas), y manda a los mejores candidatos a `root_validation_pool.py` para confirmar en paper-trading real antes de promoverlos.

**Tech Stack:** Python 3.10, pytest, sin dependencias nuevas (stdlib: `dataclasses`, `random`, `math`, `json`, `threading`).

## Global Constraints

- Todo corre con `LIVE_MODE=false` — nada de esto toca dinero real.
- Ningún módulo nuevo puede bloquear ni tumbar el camino de trading real: todo en threads daemon, cualquier excepción se loguea (`log.warning`/`log.debug`) y se descarta ahí mismo — nunca se propaga.
- Nada de mocks de mercado en vivo en tests — fixtures basados en datos reales ya grabados en `data/`.
- Los 1,219 trades semilla de `data/sim_history.json` solo se usan cuando tienen `entry_context` no vacío (evita fuga de datos futuros vía `exit_context`).
- Cada archivo nuevo tiene una sola responsabilidad — ver spec `docs/superpowers/specs/2026-08-05-root-evolutivo-decisor-design.md`.

---

## Task 1: `root_config.py` — Config mutable + decisión pura

**Files:**
- Create: `copytrade/root_config.py`
- Test: `tests/test_root_config.py`

**Interfaces:**
- Produces: `FEATURES: list[str]`, `Config` (dataclass: `config_id: str, weights: dict[str,float], bias: float, wallet_trust: dict[str,float], stop_loss_pct: float, max_hold_min: float, trade_usd: float`), `neutral_config(config_id="champion-seed") -> Config`, `score_entry_context(config: Config, wallet_label: str, entry_context: dict) -> float`, `decide(config: Config, wallet_label: str, entry_context: dict) -> str` ("COPIAR"|"SKIP"), `config_to_dict(config: Config) -> dict`, `config_from_dict(d: dict) -> Config`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_config.py
import pytest

from copytrade.root_config import (
    FEATURES, Config, neutral_config, score_entry_context, decide,
    config_to_dict, config_from_dict,
)


def test_neutral_config_pesos_en_cero():
    cfg = neutral_config("test-seed")
    assert cfg.config_id == "test-seed"
    assert all(cfg.weights[f] == 0.0 for f in FEATURES)
    assert cfg.bias == 0.0
    assert cfg.wallet_trust == {}


def test_score_con_pesos_en_cero_es_solo_bias_mas_trust():
    cfg = neutral_config()
    cfg.bias = 5.0
    cfg.wallet_trust = {"Cupsey": 2.0}
    score = score_entry_context(cfg, "Cupsey", {"mcap_usd": 100000, "buy_pressure": 0.9})
    assert score == pytest.approx(7.0)


def test_score_usa_log1p_para_mcap():
    cfg = neutral_config()
    cfg.weights["mcap_usd"] = 1.0
    import math
    score = score_entry_context(cfg, "X", {"mcap_usd": 999})
    assert score == pytest.approx(math.log1p(999))


def test_score_ignora_features_faltantes_sin_reventar():
    cfg = neutral_config()
    cfg.weights["change_1h_pct"] = 1.0
    score = score_entry_context(cfg, "X", {})
    assert score == 0.0


def test_decide_copiar_con_score_positivo():
    cfg = neutral_config()
    cfg.bias = 1.0
    assert decide(cfg, "X", {}) == "COPIAR"


def test_decide_skip_con_score_negativo():
    cfg = neutral_config()
    cfg.bias = -1.0
    assert decide(cfg, "X", {}) == "SKIP"


def test_decide_score_cero_copia_por_defecto():
    cfg = neutral_config()
    assert decide(cfg, "X", {}) == "COPIAR"


def test_config_roundtrip_dict():
    cfg = neutral_config("roundtrip")
    cfg.weights["buy_pressure"] = 0.42
    cfg.wallet_trust = {"Theo": 0.1}
    cfg.stop_loss_pct = 12.0
    restored = config_from_dict(config_to_dict(cfg))
    assert restored == cfg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'copytrade.root_config'`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_config.py
"""
Config mutable que ROOT usa para decidir — nada fijo: pesos por feature,
bias, confianza por wallet, y los parámetros de riesgo (stop-loss, max-hold,
tamaño de posición) son todos genes que root_evolution.py puede mutar. Este
módulo solo tiene funciones puras: cómo se calcula un score y cómo se
serializa una config — sin I/O, sin red, fácil de testear y de razonar.
"""
import math
from dataclasses import dataclass, field

FEATURES = [
    "mcap_usd", "liquidity_usd", "volume_24h_usd", "vol_liq_ratio",
    "buy_pressure", "change_1h_pct", "buys_1h", "sells_1h", "age_days",
]


@dataclass
class Config:
    config_id: str
    weights: dict[str, float] = field(default_factory=dict)
    bias: float = 0.0
    wallet_trust: dict[str, float] = field(default_factory=dict)
    stop_loss_pct: float = 15.0
    max_hold_min: float = 30.0
    trade_usd: float = 50.0


def neutral_config(config_id: str = "champion-seed") -> Config:
    """Punto de partida sin sesgo: pesos y bias en cero → score siempre 0 →
    decide() copia todo (igual que el fallback 'sin patrón, dejar pasar' que
    ya existía). De ahí en más, root_evolution.py es quien aprende qué pesar."""
    return Config(
        config_id=config_id,
        weights={f: 0.0 for f in FEATURES},
        bias=0.0,
        wallet_trust={},
        stop_loss_pct=15.0,
        max_hold_min=30.0,
        trade_usd=50.0,
    )


def _feature_vector(entry_context: dict) -> dict[str, float]:
    """Normaliza entry_context (features crudas de DexScreener) a un vector
    en escalas comparables: log1p para magnitudes (mcap/liquidez/volumen/
    conteos de compras-ventas, que varían en órdenes de magnitud), raw para
    ratios y porcentajes ya acotados. Un feature ausente o no numérico
    aporta 0 — nunca inventa un valor."""
    ctx = entry_context or {}

    def _num(key: str) -> float:
        v = ctx.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "mcap_usd": math.log1p(max(0.0, _num("mcap_usd"))),
        "liquidity_usd": math.log1p(max(0.0, _num("liquidity_usd"))),
        "volume_24h_usd": math.log1p(max(0.0, _num("volume_24h_usd"))),
        "vol_liq_ratio": _num("vol_liq_ratio"),
        "buy_pressure": _num("buy_pressure"),
        "change_1h_pct": _num("change_1h_pct") / 100.0,
        "buys_1h": math.log1p(max(0.0, _num("buys_1h"))),
        "sells_1h": math.log1p(max(0.0, _num("sells_1h"))),
        "age_days": _num("age_days"),
    }


def score_entry_context(config: Config, wallet_label: str, entry_context: dict) -> float:
    feats = _feature_vector(entry_context)
    score = config.bias + config.wallet_trust.get(wallet_label, 0.0)
    for name in FEATURES:
        score += config.weights.get(name, 0.0) * feats[name]
    return score


def decide(config: Config, wallet_label: str, entry_context: dict) -> str:
    """score >= 0 → COPIAR. El umbral vive implícito en bias (un gen menos
    para mutar por separado, mismo poder expresivo)."""
    return "COPIAR" if score_entry_context(config, wallet_label, entry_context) >= 0.0 else "SKIP"


def config_to_dict(config: Config) -> dict:
    return {
        "config_id": config.config_id,
        "weights": dict(config.weights),
        "bias": config.bias,
        "wallet_trust": dict(config.wallet_trust),
        "stop_loss_pct": config.stop_loss_pct,
        "max_hold_min": config.max_hold_min,
        "trade_usd": config.trade_usd,
    }


def config_from_dict(d: dict) -> Config:
    return Config(
        config_id=d["config_id"],
        weights=dict(d.get("weights", {})),
        bias=d.get("bias", 0.0),
        wallet_trust=dict(d.get("wallet_trust", {})),
        stop_loss_pct=d.get("stop_loss_pct", 15.0),
        max_hold_min=d.get("max_hold_min", 30.0),
        trade_usd=d.get("trade_usd", 50.0),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_config.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_config.py tests/test_root_config.py
git commit -m "feat(root): Config mutable + decisión pura sin reglas fijas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `root_seed_loader.py` — normalizar historial existente

**Files:**
- Create: `copytrade/root_seed_loader.py`
- Test: `tests/test_root_seed_loader.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `load_rule_based_seed(path: str) -> list[dict]`, `load_root_sim_seed(path: str) -> list[dict]`, `load_seed_dataset(rule_history_path: str, root_sim_history_path: str) -> list[dict]`. Cada record: `{"wallet": str, "token_mint": str|None, "entry_context": dict, "entry_price": float|None, "pnl_pct": float|None, "won": bool, "trajectory": None}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_seed_loader.py
import json

from copytrade.root_seed_loader import (
    load_rule_based_seed, load_root_sim_seed, load_seed_dataset,
)


def test_load_rule_based_seed_filtra_entry_context_vacio(tmp_path):
    path = tmp_path / "sim_history.json"
    path.write_text(json.dumps([
        {"wallet_label": "Theo", "token": "MINTA", "entry_context": {"mcap_usd": 5000},
         "pnl_pct": 12.0, "won": True},
        {"wallet_label": "Decu", "token": "MINTB", "entry_context": {},
         "exit_context": {"mcap_usd": 9000}, "pnl_pct": -5.0, "won": False},
    ]))
    records = load_rule_based_seed(str(path))
    assert len(records) == 1
    assert records[0]["wallet"] == "Theo"
    assert records[0]["token_mint"] == "MINTA"
    assert records[0]["entry_context"] == {"mcap_usd": 5000}
    assert records[0]["trajectory"] is None


def test_load_rule_based_seed_archivo_inexistente(tmp_path):
    assert load_rule_based_seed(str(tmp_path / "nope.json")) == []


def test_load_root_sim_seed_jsonl(tmp_path):
    path = tmp_path / "root_sim_history.json"
    lines = [
        json.dumps({"wallet": "Cupsey", "token_mint": "MINTC",
                    "entry_context": {"buy_pressure": 0.9}, "pnl_pct": 30.0, "won": True}),
        json.dumps({"wallet": "Yenni", "token_mint": "MINTD",
                    "entry_context": {}, "pnl_pct": -10.0, "won": False}),
    ]
    path.write_text("\n".join(lines) + "\n")
    records = load_root_sim_seed(str(path))
    assert len(records) == 1
    assert records[0]["wallet"] == "Cupsey"
    assert records[0]["token_mint"] == "MINTC"


def test_load_seed_dataset_junta_ambas_fuentes(tmp_path):
    rules_path = tmp_path / "sim_history.json"
    rules_path.write_text(json.dumps([
        {"wallet_label": "Theo", "token": "MINTA", "entry_context": {"mcap_usd": 1},
         "pnl_pct": 1.0, "won": True},
    ]))
    root_path = tmp_path / "root_sim_history.json"
    root_path.write_text(json.dumps(
        {"wallet": "Cupsey", "token_mint": "MINTC", "entry_context": {"mcap_usd": 2},
         "pnl_pct": 2.0, "won": True}
    ) + "\n")

    dataset = load_seed_dataset(str(rules_path), str(root_path))
    assert len(dataset) == 2
    assert {r["wallet"] for r in dataset} == {"Theo", "Cupsey"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_seed_loader.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_seed_loader.py
"""
Normaliza los historiales de trades existentes (el del bot de reglas viejo
y el propio de ROOT) a una forma común para que root_backtest.py y
root_wallet_miner.py no tengan que conocer los formatos de archivo
originales.

Por qué se usa el historial de reglas como semilla: son 1,219 resultados
reales de mercado y de wallets (ganó/perdió, contexto), no "lógica de
reglas" en sí — la lógica de reglas solo decidió CUÁLES de esos trades se
tomaron, lo cual sesga la muestra pero no invalida los resultados. Se
descartan los que no tienen entry_context: usar exit_context como si fuera
entry_context sería fuga de datos futuros (el bot no sabía eso al decidir).
"""
import json
import os


def _load_json_array(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_rule_based_seed(path: str) -> list[dict]:
    """Normaliza data/sim_history.json (formato del bot de reglas: JSON
    array, wallet_label/token/entry_context/exit_context)."""
    out = []
    for t in _load_json_array(path):
        entry_context = t.get("entry_context") or {}
        if not entry_context:
            continue
        out.append({
            "wallet": t.get("wallet_label", "?"),
            "token_mint": t.get("token"),
            "entry_context": entry_context,
            "entry_price": t.get("entry_price"),
            "pnl_pct": t.get("pnl_pct"),
            "won": bool(t.get("won")),
            "trajectory": None,
        })
    return out


def load_root_sim_seed(path: str) -> list[dict]:
    """Normaliza data/root_sim_history.json (formato propio de ROOT: JSON
    Lines, wallet/token_mint/entry_context)."""
    out = []
    for t in _load_jsonl(path):
        entry_context = t.get("entry_context") or {}
        if not entry_context:
            continue
        out.append({
            "wallet": t.get("wallet", "?"),
            "token_mint": t.get("token_mint"),
            "entry_context": entry_context,
            "entry_price": t.get("entry_price"),
            "pnl_pct": t.get("pnl_pct"),
            "won": bool(t.get("won")),
            "trajectory": None,
        })
    return out


def load_seed_dataset(rule_history_path: str, root_sim_history_path: str) -> list[dict]:
    return load_rule_based_seed(rule_history_path) + load_root_sim_seed(root_sim_history_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_seed_loader.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_seed_loader.py tests/test_root_seed_loader.py
git commit -m "feat(root): normaliza historial de reglas + root_sim como dataset semilla

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `root_trajectory.py` — trayectoria completa de precio

**Files:**
- Create: `copytrade/root_trajectory.py`
- Test: `tests/test_root_trajectory.py`

**Interfaces:**
- Produces: `start_trajectory(token_mint: str, entry_price: float) -> None`, `record_snapshot(token_mint: str, elapsed_s: float, price: float) -> None`, `close_trajectory(token_mint: str, wallet: str, config_id: str) -> None`, `load_trajectories(path: str) -> list[dict]`, module-level `TRAJECTORIES_PATH: str` (monkeypatchable).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_trajectory.py
import json

import pytest

import copytrade.root_trajectory as traj


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(traj, "TRAJECTORIES_PATH", str(tmp_path / "root_trajectories.jsonl"))
    traj._open_trajectories.clear()
    yield
    traj._open_trajectories.clear()


def test_record_snapshot_sin_start_no_hace_nada():
    traj.record_snapshot("MINT1", 15.0, 1.0)
    assert "MINT1" not in traj._open_trajectories


def test_start_record_close_persiste_snapshots(tmp_path):
    traj.start_trajectory("MINT1", entry_price=1.0)
    traj.record_snapshot("MINT1", 15.0, 1.05)
    traj.record_snapshot("MINT1", 30.0, 0.98)
    traj.close_trajectory("MINT1", wallet="Theo", config_id="champion")

    records = traj.load_trajectories(traj.TRAJECTORIES_PATH)
    assert len(records) == 1
    assert records[0]["token_mint"] == "MINT1"
    assert records[0]["wallet"] == "Theo"
    assert records[0]["config_id"] == "champion"
    assert records[0]["snapshots"] == [[0.0, 1.0], [15.0, 1.05], [30.0, 0.98]]
    assert "MINT1" not in traj._open_trajectories  # se limpia de memoria


def test_close_sin_start_no_escribe_nada():
    traj.close_trajectory("NUNCA_ABIERTO", wallet="X", config_id="champion")
    import os
    assert not os.path.exists(traj.TRAJECTORIES_PATH)


def test_load_trajectories_archivo_inexistente(tmp_path):
    assert traj.load_trajectories(str(tmp_path / "nope.jsonl")) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_trajectory.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_trajectory.py
"""
Persiste la trayectoria de precio completa de una posición mientras está
abierta (no solo entrada/salida). Sin esto, root_backtest.py no puede
simular qué hubiera pasado con otro stop-loss/max-hold — solo puede
reevaluar si una config hubiera dicho COPIAR, usando el pnl ya registrado.

Corre llamado desde root_sim.py en el mismo thread de monitoreo — no tiene
su propio thread, solo maneja el estado en memoria de trayectorias abiertas
y las escribe a disco al cerrar.
"""
import json
import os
import threading

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TRAJECTORIES_PATH = os.path.join(_DATA_DIR, "root_trajectories.jsonl")

_lock = threading.Lock()
_open_trajectories: dict[str, list[list[float]]] = {}


def start_trajectory(token_mint: str, entry_price: float) -> None:
    with _lock:
        _open_trajectories[token_mint] = [[0.0, entry_price]]


def record_snapshot(token_mint: str, elapsed_s: float, price: float) -> None:
    """No hace nada si no hay trayectoria arrancada para ese mint — nunca
    inventa un punto de partida que no se registró."""
    with _lock:
        if token_mint not in _open_trajectories:
            return
        _open_trajectories[token_mint].append([elapsed_s, price])


def close_trajectory(token_mint: str, wallet: str, config_id: str) -> None:
    with _lock:
        snapshots = _open_trajectories.pop(token_mint, None)
    if not snapshots:
        return
    os.makedirs(os.path.dirname(TRAJECTORIES_PATH), exist_ok=True)
    record = {"token_mint": token_mint, "wallet": wallet, "config_id": config_id, "snapshots": snapshots}
    with open(TRAJECTORIES_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_trajectories(path: str = None) -> list[dict]:
    path = path or TRAJECTORIES_PATH
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_trajectory.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_trajectory.py tests/test_root_trajectory.py
git commit -m "feat(root): graba trayectoria completa de precio por posición

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `root_backtest.py` — backtest puro

**Files:**
- Create: `copytrade/root_backtest.py`
- Test: `tests/test_root_backtest.py`

**Interfaces:**
- Consumes: `copytrade.root_config.Config`, `copytrade.root_config.decide`.
- Produces: `simulate_exit(entry_price: float, snapshots: list[list[float]], stop_loss_pct: float, max_hold_min: float) -> tuple[float, str]`, `backtest_config(config: Config, dataset: list[dict]) -> dict` con claves `config_id, n_taken, n_seen, pnl_usd_total, win_rate, pnl_usd_excluding_best`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_backtest.py
import pytest

from copytrade.root_config import neutral_config
from copytrade.root_backtest import simulate_exit, backtest_config


def test_simulate_exit_stop_loss():
    snapshots = [[0.0, 1.0], [15.0, 0.90], [30.0, 0.80], [45.0, 0.70]]
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "stop_loss"
    assert pnl_pct == pytest.approx(-20.0)  # primer punto que cruza -15%: precio 0.80


def test_simulate_exit_max_hold():
    snapshots = [[0.0, 1.0], [900.0, 1.05], [1800.0, 1.08]]  # 15min, 30min
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "max_hold"
    assert pnl_pct == pytest.approx(8.0)


def test_simulate_exit_sin_datos():
    pnl_pct, reason = simulate_exit(1.0, [], stop_loss_pct=15.0, max_hold_min=30)
    assert reason == "sin_datos"
    assert pnl_pct == 0.0


def test_simulate_exit_end_of_data_sin_cruzar_ningun_umbral():
    snapshots = [[0.0, 1.0], [60.0, 1.02]]
    pnl_pct, reason = simulate_exit(1.0, snapshots, stop_loss_pct=50.0, max_hold_min=999)
    assert reason == "end_of_data"
    assert pnl_pct == pytest.approx(2.0)


def test_backtest_config_solo_cuenta_trades_que_hubiera_copiado():
    cfg = neutral_config()
    cfg.bias = -1.0  # nunca copia (score siempre negativo con pesos en 0)
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 50.0, "won": True, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    assert result["n_taken"] == 0
    assert result["pnl_usd_total"] == 0.0


def test_backtest_config_usa_pnl_registrado_sin_trayectoria():
    cfg = neutral_config()  # bias=0 → copia todo
    cfg.trade_usd = 50.0
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 20.0, "won": True, "trajectory": None},
        {"wallet": "Y", "entry_context": {}, "entry_price": 1.0, "pnl_pct": -10.0, "won": False, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    assert result["n_taken"] == 2
    assert result["pnl_usd_total"] == pytest.approx(50 * 0.20 + 50 * -0.10)
    assert result["win_rate"] == pytest.approx(50.0)


def test_backtest_config_usa_trayectoria_cuando_existe():
    cfg = neutral_config()
    cfg.stop_loss_pct = 15.0
    cfg.max_hold_min = 30.0
    cfg.trade_usd = 50.0
    dataset = [
        {
            "wallet": "X", "entry_context": {}, "entry_price": 1.0,
            "pnl_pct": 999.0,  # si esto se usara en vez de la trayectoria, el test fallaría
            "won": True,
            "trajectory": [[0.0, 1.0], [15.0, 0.80]],  # -20% → dispara stop_loss antes
        },
    ]
    result = backtest_config(cfg, dataset)
    assert result["pnl_usd_total"] == pytest.approx(50 * -0.20)


def test_backtest_config_excluding_best_resta_el_mejor_trade():
    cfg = neutral_config()
    cfg.trade_usd = 50.0
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 200.0, "won": True, "trajectory": None},
        {"wallet": "Y", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 10.0, "won": True, "trajectory": None},
    ]
    result = backtest_config(cfg, dataset)
    best = 50 * 2.0
    assert result["pnl_usd_excluding_best"] == pytest.approx(result["pnl_usd_total"] - best)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_backtest.py
"""
Backtest puro: dado un vector de config y un dataset de trades históricos
(formato de root_seed_loader / root_evolution), calcula qué pnl hubiera
dado esa config. Sobre trades con trayectoria completa puede re-simular
exits alternativos de verdad; sobre trades sin trayectoria (el historial
semilla) solo puede reevaluar si la config hubiera dicho COPIAR, usando el
pnl ya registrado — no inventa un exit que los datos no permiten calcular.
Sin I/O, sin red — todo en memoria, corre en milisegundos.
"""
from copytrade.root_config import Config, decide


def simulate_exit(
    entry_price: float, snapshots: list[list[float]], stop_loss_pct: float, max_hold_min: float,
) -> tuple[float, str]:
    """Recorre la trayectoria (elapsed_s, price) en orden y devuelve
    (pnl_pct, exit_reason) según el primer stop-loss o max-hold que se
    cumpla. Si ninguno se cumple durante toda la trayectoria conocida, sale
    al último precio con motivo 'end_of_data' — no inventa un precio futuro
    que no está en los datos."""
    if not snapshots:
        return 0.0, "sin_datos"
    last_pnl_pct = 0.0
    for elapsed_s, price in snapshots:
        if not price or price <= 0:
            continue
        pnl_pct = (price - entry_price) / entry_price * 100
        last_pnl_pct = pnl_pct
        if pnl_pct <= -stop_loss_pct:
            return pnl_pct, "stop_loss"
        if elapsed_s / 60.0 >= max_hold_min:
            return pnl_pct, "max_hold"
    return last_pnl_pct, "end_of_data"


def backtest_config(config: Config, dataset: list[dict]) -> dict:
    taken_pnls_usd: list[float] = []

    for trade in dataset:
        wallet = trade["wallet"]
        entry_context = trade["entry_context"]
        if decide(config, wallet, entry_context) != "COPIAR":
            continue

        if trade.get("trajectory"):
            pnl_pct, _reason = simulate_exit(
                trade["entry_price"], trade["trajectory"], config.stop_loss_pct, config.max_hold_min,
            )
        else:
            pnl_pct = trade.get("pnl_pct") or 0.0

        taken_pnls_usd.append(config.trade_usd * pnl_pct / 100.0)

    n_taken = len(taken_pnls_usd)
    pnl_total = sum(taken_pnls_usd)
    wins = sum(1 for p in taken_pnls_usd if p > 0)
    pnl_excluding_best = pnl_total - max(taken_pnls_usd) if taken_pnls_usd else 0.0

    return {
        "config_id": config.config_id,
        "n_taken": n_taken,
        "n_seen": len(dataset),
        "pnl_usd_total": round(pnl_total, 4),
        "win_rate": round(wins / n_taken * 100, 2) if n_taken else 0.0,
        "pnl_usd_excluding_best": round(pnl_excluding_best, 4),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_backtest.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_backtest.py tests/test_root_backtest.py
git commit -m "feat(root): backtest puro de configs contra historial acumulado

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: `root_wallet_miner.py` — comportamiento por wallet

**Files:**
- Create: `copytrade/root_wallet_miner.py`
- Test: `tests/test_root_wallet_miner.py`

**Interfaces:**
- Produces: `wallet_features(dataset: list[dict]) -> dict[str, dict]`, `initial_wallet_trust(dataset: list[dict], scale: float = 0.02) -> dict[str, float]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_wallet_miner.py
import pytest

from copytrade.root_wallet_miner import wallet_features, initial_wallet_trust

DATASET = [
    {"wallet": "Theo", "entry_context": {"change_1h_pct": 100.0}, "pnl_pct": 20.0, "won": True},
    {"wallet": "Theo", "entry_context": {"change_1h_pct": 50.0}, "pnl_pct": -10.0, "won": False},
    {"wallet": "Decu", "entry_context": {}, "pnl_pct": -5.0, "won": False},
]


def test_wallet_features_agrupa_y_calcula_winrate():
    feats = wallet_features(DATASET)
    assert feats["Theo"]["n_trades"] == 2
    assert feats["Theo"]["win_rate"] == pytest.approx(50.0)
    assert feats["Theo"]["avg_pnl_pct"] == pytest.approx(5.0)
    assert feats["Theo"]["avg_change_1h_pct_al_entrar"] == pytest.approx(75.0)
    assert feats["Decu"]["win_rate"] == pytest.approx(0.0)
    assert feats["Decu"]["avg_change_1h_pct_al_entrar"] is None


def test_wallet_features_dataset_vacio():
    assert wallet_features([]) == {}


def test_initial_wallet_trust_centra_en_50_por_ciento():
    trust = initial_wallet_trust(DATASET, scale=0.02)
    # Theo: winrate 50% → (50-50)*0.02 = 0.0
    assert trust["Theo"] == pytest.approx(0.0)
    # Decu: winrate 0% → (0-50)*0.02 = -1.0
    assert trust["Decu"] == pytest.approx(-1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_wallet_miner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_wallet_miner.py
"""
Analiza el historial de trades por wallet para extraer features de
comportamiento (winrate, pnl promedio, timing de entrada respecto al pump)
que alimentan wallet_trust en root_config.Config como punto de partida —
no son reglas fijas, root_evolution.py sigue mutándolas libremente.
"""


def wallet_features(dataset: list[dict]) -> dict[str, dict]:
    by_wallet: dict[str, list[dict]] = {}
    for trade in dataset:
        by_wallet.setdefault(trade["wallet"], []).append(trade)

    out = {}
    for wallet, trades in by_wallet.items():
        n = len(trades)
        wins = sum(1 for t in trades if t.get("won"))
        pnl_values = [t.get("pnl_pct") or 0.0 for t in trades]
        change_values = [
            t["entry_context"].get("change_1h_pct")
            for t in trades
            if t.get("entry_context", {}).get("change_1h_pct") is not None
        ]
        out[wallet] = {
            "n_trades": n,
            "win_rate": round(wins / n * 100, 2) if n else 0.0,
            "avg_pnl_pct": round(sum(pnl_values) / n, 2) if n else 0.0,
            "avg_change_1h_pct_al_entrar": (
                round(sum(change_values) / len(change_values), 2) if change_values else None
            ),
        }
    return out


def initial_wallet_trust(dataset: list[dict], scale: float = 0.02) -> dict[str, float]:
    """(win_rate - 50) * scale — una wallet con el winrate promedio real
    medido (43.8%) arranca casi neutra; una mejor arranca con empujón
    positivo. Sigue siendo un punto de partida mutable, no un valor fijo."""
    features = wallet_features(dataset)
    return {w: round((f["win_rate"] - 50.0) * scale, 4) for w, f in features.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_wallet_miner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_wallet_miner.py tests/test_root_wallet_miner.py
git commit -m "feat(root): mina comportamiento por wallet para sembrar wallet_trust

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: extender `root_sim.py` para multi-config + trayectoria

**Files:**
- Modify: `copytrade/root_sim.py` (completo, ver abajo)
- Modify: `tests/test_root_sim.py` (actualiza tests que asumían clave `_positions[token_mint]` y firma vieja de `open_position`)

**Interfaces:**
- Consumes: `copytrade.root_trajectory.start_trajectory/record_snapshot/close_trajectory` (Task 3).
- Produces: `open_position(wallet_label, token_mint, entry_context, root_score, root_prob, config_id="champion", stop_loss_pct=None, max_hold_min=None, trade_usd=None, balance_path=None, history_path=None) -> None`. **Cambio de comportamiento:** `_positions` ahora se indexa por `f"{config_id}:{token_mint}"`, no solo por `token_mint` — así el campeón y varios candidatos de validación pueden seguir el mismo token en paralelo sin pisarse.

- [ ] **Step 1: Actualizar los tests existentes a la nueva firma (rojo primero)**

Reemplazar `tests/test_root_sim.py` completo:

```python
# tests/test_root_sim.py
import json
import threading

import pytest

import copytrade.root_sim as rsim
import copytrade.root_trajectory as traj

ENTRY_CONTEXT = {"price_usd": 1.0e-05, "mcap_usd": 12000, "liquidity_usd": 5000}


@pytest.fixture(autouse=True)
def isolate_root_sim(tmp_path, monkeypatch):
    monkeypatch.setattr(rsim, "BALANCE_PATH", str(tmp_path / "root_sim_balance.json"))
    monkeypatch.setattr(rsim, "HISTORY_PATH", str(tmp_path / "root_sim_history.json"))
    monkeypatch.setattr(traj, "TRAJECTORIES_PATH", str(tmp_path / "root_trajectories.jsonl"))
    rsim._positions.clear()
    traj._open_trajectories.clear()
    yield
    rsim._positions.clear()
    traj._open_trajectories.clear()


# ── _decide_exit: función pura (sin cambios) ────────────────────────────────

def test_decide_exit_sin_precio_no_decide():
    assert rsim._decide_exit(1.0, None, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None
    assert rsim._decide_exit(1.0, 0, elapsed_min=1, stop_loss_pct=15, max_hold_min=30) is None


def test_decide_exit_stop_loss():
    out = rsim._decide_exit(1.0, 0.80, elapsed_min=2, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "stop_loss"
    assert out["pnl_pct"] == pytest.approx(-20.0)


def test_decide_exit_max_hold():
    out = rsim._decide_exit(1.0, 1.05, elapsed_min=31, stop_loss_pct=15, max_hold_min=30)
    assert out["reason"] == "max_hold"
    assert out["pnl_pct"] == pytest.approx(5.0)


def test_decide_exit_sigue_abierta():
    out = rsim._decide_exit(1.0, 1.02, elapsed_min=5, stop_loss_pct=15, max_hold_min=30)
    assert out is None


# ── _close_position: persistencia parametrizada por path ───────────────────

def test_close_position_actualiza_balance_y_history(tmp_path):
    balance_path = str(tmp_path / "root_sim_balance.json")
    history_path = str(tmp_path / "root_sim_history.json")
    position = {
        "wallet": "Cented", "token_mint": "ABC123", "config_id": "champion", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 72, "root_prob": 0.72,
        "entry_context": ENTRY_CONTEXT, "balance_path": balance_path, "history_path": history_path,
    }
    exit_info = {"reason": "max_hold", "pnl_pct": 10.0}

    rsim._close_position(position, exit_info, current_price=1.10)

    balance = json.loads(open(balance_path).read())
    assert balance["balance"] == pytest.approx(rsim.INITIAL_BALANCE + 5.0)

    history = [json.loads(l) for l in open(history_path).read().strip().splitlines()]
    assert len(history) == 1
    assert history[0]["wallet"] == "Cented"
    assert history[0]["config_id"] == "champion"
    assert history[0]["pnl_pct"] == 10.0
    assert history[0]["pnl_usd"] == pytest.approx(5.0)
    assert history[0]["exit_reason"] == "max_hold"
    assert history[0]["won"] is True
    assert history[0]["entry_context"] == ENTRY_CONTEXT


def test_close_position_perdida_marca_won_false(tmp_path):
    balance_path = str(tmp_path / "b.json")
    history_path = str(tmp_path / "h.json")
    position = {
        "wallet": "Theo", "token_mint": "XYZ", "config_id": "champion", "entry_price": 1.0,
        "amount_usd": 50.0, "opened_at": 1000.0, "root_score": 60, "root_prob": 0.6,
        "entry_context": None, "balance_path": balance_path, "history_path": history_path,
    }
    exit_info = {"reason": "stop_loss", "pnl_pct": -15.0}

    rsim._close_position(position, exit_info, current_price=0.85)

    history = [json.loads(l) for l in open(history_path).read().strip().splitlines()]
    assert history[0]["won"] is False
    assert history[0]["pnl_usd"] == pytest.approx(-7.5)


def test_close_position_dos_candidatos_no_comparten_balance(tmp_path):
    """Distintos config_id → distintos balance_path → no se mezclan."""
    b1, h1 = str(tmp_path / "b1.json"), str(tmp_path / "h1.json")
    b2, h2 = str(tmp_path / "b2.json"), str(tmp_path / "h2.json")
    pos1 = {"wallet": "A", "token_mint": "T1", "config_id": "gen0-0", "entry_price": 1.0,
            "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5,
            "entry_context": None, "balance_path": b1, "history_path": h1}
    pos2 = {"wallet": "B", "token_mint": "T1", "config_id": "gen0-1", "entry_price": 1.0,
            "amount_usd": 50.0, "opened_at": 0, "root_score": 1, "root_prob": 0.5,
            "entry_context": None, "balance_path": b2, "history_path": h2}

    rsim._close_position(pos1, {"reason": "max_hold", "pnl_pct": 10.0}, 1.1)
    rsim._close_position(pos2, {"reason": "max_hold", "pnl_pct": -10.0}, 0.9)

    bal1 = json.loads(open(b1).read())
    bal2 = json.loads(open(b2).read())
    assert bal1["balance"] == pytest.approx(rsim.INITIAL_BALANCE + 5.0)
    assert bal2["balance"] == pytest.approx(rsim.INITIAL_BALANCE - 5.0)


# ── open_position: gating + multi-config (sin lanzar hilos reales) ─────────

def test_open_position_no_abre_sin_entry_price(monkeypatch):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", {}, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_no_abre_sin_pair_address(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: None)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_no_abre_dos_veces_la_misma_config_y_token(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    calls = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: calls.append(1) or _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")

    assert len(calls) == 1


def test_open_position_mismo_token_distinta_config_no_choca(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="champion")
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8, config_id="gen0-0")

    assert "champion:MINT1" in rsim._positions
    assert "gen0-0:MINT1" in rsim._positions


def test_open_position_no_abre_si_balance_agotado(tmp_path, monkeypatch):
    open(str(tmp_path / "root_sim_balance.json"), "w").write(
        json.dumps({"balance": 0, "initial": rsim.INITIAL_BALANCE})
    )
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)
    assert not rsim._positions


def test_open_position_abre_y_registra_posicion_con_defaults(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position("Cented", "MINT1", ENTRY_CONTEXT, root_score=80, root_prob=0.8)

    pos = rsim._positions["champion:MINT1"]
    assert pos["wallet"] == "Cented"
    assert pos["entry_price"] == ENTRY_CONTEXT["price_usd"]
    assert pos["pair_address"] == "PAIR1"
    assert pos["amount_usd"] == rsim.TRADE_USD
    assert pos["stop_loss_pct"] == rsim.STOP_LOSS_PCT
    assert pos["max_hold_min"] == rsim.MAX_HOLD_MIN
    assert pos["balance_path"] == rsim.BALANCE_PATH


def test_open_position_respeta_overrides_de_candidato(monkeypatch):
    monkeypatch.setattr(rsim, "get_best_pair", lambda mint: {"pairAddress": "PAIR1"})
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())

    rsim.open_position(
        "Cented", "MINT1", ENTRY_CONTEXT, root_score=0, root_prob=0.0,
        config_id="gen0-0", stop_loss_pct=20.0, max_hold_min=10.0, trade_usd=75.0,
        balance_path="/tmp/custom_balance.json", history_path="/tmp/custom_history.json",
    )

    pos = rsim._positions["gen0-0:MINT1"]
    assert pos["stop_loss_pct"] == 20.0
    assert pos["max_hold_min"] == 10.0
    assert pos["amount_usd"] == 75.0
    assert pos["balance_path"] == "/tmp/custom_balance.json"


class _FakeThread:
    def start(self):
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_sim.py -v`
Expected: FAIL — `_close_position`/`open_position` todavía usan la firma vieja (sin `config_id`, sin `balance_path`/`history_path` en el dict de posición, `_positions` indexado solo por `token_mint`).

- [ ] **Step 3: Reescribir `copytrade/root_sim.py` completo**

```python
# copytrade/root_sim.py
"""
Paper-trading propio de las decisiones de ROOT — independiente del simulador
de reglas que ya corre en simulator.py, al que este módulo NUNCA toca.

Sirve a dos consumidores: root_decider.py (el campeón vigente, que decide
de verdad qué se copia) y root_validation_pool.py (candidatos en prueba,
cada uno con su propio balance/historial separado por config_id). Por eso
balance/historial/parámetros de riesgo ya no son globals fijos — se pasan
por parámetro, con los globals de siempre como default.

Tamaño de posición fijo en dólares (no % del balance): la investigación del
propio balance de las reglas (`sim_balance.json`, $20→$40k en 6 semanas)
mostró que sizing como % de un balance compuesto produce números que no se
podrían ejecutar en la realidad. Acá cada trade arriesga el monto fijo de
su config — el balance resultante es interpretable como ganancia/pérdida
real acumulada, no un artefacto de compounding.

Corre en threads daemon aparte — no puede bloquear ni afectar el trading
real. Cualquier error se loguea y se descarta ahí.
"""
import json
import os
import threading
import time

from config import HARD_STOP_LOSS_PCT
from copytrade import root_trajectory
from utils.dexscreener import get_best_pair, get_pair_price
from utils.logger import get_logger

log = get_logger("root_sim")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BALANCE_PATH = os.path.join(_DATA_DIR, "root_sim_balance.json")
HISTORY_PATH = os.path.join(_DATA_DIR, "root_sim_history.json")

ENABLED = os.getenv("ROOT_SIM_ENABLED", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("ROOT_SIM_INITIAL_BALANCE", "1000"))
TRADE_USD = float(os.getenv("ROOT_SIM_TRADE_USD", "50"))
STOP_LOSS_PCT = HARD_STOP_LOSS_PCT
MAX_HOLD_MIN = float(os.getenv("ROOT_SIM_MAX_HOLD_MIN", "30"))
POLL_INTERVAL_S = float(os.getenv("ROOT_SIM_POLL_INTERVAL_S", "15"))

_lock = threading.Lock()
_positions: dict[str, dict] = {}


def _decide_exit(entry_price, current_price, elapsed_min, stop_loss_pct, max_hold_min):
    """Función pura: decide si hay que cerrar la posición ahora. None = seguir
    esperando. No inventa una decisión si no hay precio disponible."""
    if not current_price or current_price <= 0:
        return None
    pnl_pct = (current_price - entry_price) / entry_price * 100
    if pnl_pct <= -stop_loss_pct:
        return {"reason": "stop_loss", "pnl_pct": pnl_pct}
    if elapsed_min >= max_hold_min:
        return {"reason": "max_hold", "pnl_pct": pnl_pct}
    return None


def _load_balance(balance_path: str) -> dict:
    if not os.path.exists(balance_path):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}
    try:
        return json.loads(open(balance_path).read())
    except (json.JSONDecodeError, OSError):
        return {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE}


def _save_balance(balance_path: str, balance: dict) -> None:
    os.makedirs(os.path.dirname(balance_path), exist_ok=True)
    balance["updated_at"] = time.strftime("%H:%M:%S %d/%m/%Y")
    with open(balance_path, "w") as f:
        json.dump(balance, f, indent=2)


def _append_history(history_path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(history_path), exist_ok=True)
    with open(history_path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _close_position(position: dict, exit_info: dict, current_price: float) -> dict:
    """Registra el resultado y actualiza el balance de papel del config_id
    de esta posición. Toda la persistencia pasa por acá, bajo `_lock`."""
    pnl_pct = exit_info["pnl_pct"]
    pnl_usd = position["amount_usd"] * pnl_pct / 100

    with _lock:
        balance = _load_balance(position["balance_path"])
        balance["balance"] = balance.get("balance", INITIAL_BALANCE) + pnl_usd
        _save_balance(position["balance_path"], balance)

        record = {
            "ts": time.time(),
            "wallet": position["wallet"],
            "token_mint": position["token_mint"],
            "config_id": position["config_id"],
            "entry_price": position["entry_price"],
            "exit_price": current_price,
            "amount_usd": position["amount_usd"],
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 4),
            "exit_reason": exit_info["reason"],
            "won": pnl_pct > 0,
            "hold_min": round((time.time() - position["opened_at"]) / 60, 2),
            "root_score": position["root_score"],
            "root_prob": position["root_prob"],
            "balance_after": balance["balance"],
            "entry_context": position.get("entry_context"),
        }
        _append_history(position["history_path"], record)

    root_trajectory.close_trajectory(position["token_mint"], position["wallet"], position["config_id"])

    log.info(
        f"[root_sim] {position['wallet']} ({position['config_id']}) → cierra {position['token_mint'][:8]}... "
        f"{'✅' if record['won'] else '❌'} {pnl_pct:+.1f}% (${pnl_usd:+.2f}) — {exit_info['reason']} "
        f"| balance=${balance['balance']:.2f}"
    )
    return record


def _monitor(position: dict) -> None:
    """Sondea el precio cada POLL_INTERVAL_S hasta que toque salir, grabando
    cada punto en root_trajectory para que el backtest pueda re-simular
    exits alternativos más adelante."""
    key = f"{position['config_id']}:{position['token_mint']}"
    while True:
        time.sleep(POLL_INTERVAL_S)
        try:
            price = get_pair_price(position["pair_address"])
        except Exception as e:
            log.debug(f"[root_sim] {position['wallet']}: error leyendo precio — {e}")
            continue

        elapsed_min = (time.time() - position["opened_at"]) / 60
        if price and price > 0:
            root_trajectory.record_snapshot(position["token_mint"], elapsed_min * 60, price)

        exit_info = _decide_exit(
            position["entry_price"], price, elapsed_min,
            position["stop_loss_pct"], position["max_hold_min"],
        )
        if exit_info:
            _close_position(position, exit_info, price)
            with _lock:
                _positions.pop(key, None)
            return


def open_position(
    wallet_label: str,
    token_mint: str,
    entry_context: dict | None,
    root_score: int,
    root_prob: float,
    config_id: str = "champion",
    stop_loss_pct: float | None = None,
    max_hold_min: float | None = None,
    trade_usd: float | None = None,
    balance_path: str | None = None,
    history_path: str | None = None,
) -> None:
    """Punto de entrada llamado desde root_decider.py (campeón) y
    root_validation_pool.py (candidatos). No bloquea: valida lo mínimo,
    registra la posición y lanza el monitoreo en un hilo daemon aparte.
    `_positions` se indexa por `config_id:token_mint` — el campeón y varios
    candidatos pueden seguir el mismo token en paralelo sin pisarse."""
    if not ENABLED:
        return

    stop_loss_pct = STOP_LOSS_PCT if stop_loss_pct is None else stop_loss_pct
    max_hold_min = MAX_HOLD_MIN if max_hold_min is None else max_hold_min
    trade_usd = TRADE_USD if trade_usd is None else trade_usd
    balance_path = balance_path or BALANCE_PATH
    history_path = history_path or HISTORY_PATH

    position_key = f"{config_id}:{token_mint}"
    with _lock:
        if position_key in _positions:
            return
        balance = _load_balance(balance_path)
        if balance.get("balance", INITIAL_BALANCE) <= 0:
            log.warning(f"[root_sim] balance de papel agotado ({config_id}) — no se abren posiciones nuevas")
            return

    entry_price = (entry_context or {}).get("price_usd")
    if not entry_price:
        log.debug(f"[root_sim] {wallet_label}: sin price_usd en entry_context — no se puede simular")
        return

    pair = get_best_pair(token_mint)
    pair_address = (pair or {}).get("pairAddress")
    if not pair_address:
        log.debug(f"[root_sim] {wallet_label}: sin par en DexScreener para {token_mint[:8]}... — no se puede simular")
        return

    position = {
        "wallet": wallet_label,
        "token_mint": token_mint,
        "config_id": config_id,
        "entry_price": entry_price,
        "pair_address": pair_address,
        "amount_usd": trade_usd,
        "opened_at": time.time(),
        "root_score": root_score,
        "root_prob": root_prob,
        "entry_context": entry_context,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_min": max_hold_min,
        "balance_path": balance_path,
        "history_path": history_path,
    }
    with _lock:
        if position_key in _positions:
            return
        _positions[position_key] = position

    root_trajectory.start_trajectory(token_mint, entry_price)
    log.info(f"[root_sim] {wallet_label} ({config_id}) → abre {token_mint[:8]}... a ${entry_price} (${trade_usd:.0f})")
    threading.Thread(target=_monitor, args=(position,), daemon=True).start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_sim.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_sim.py tests/test_root_sim.py
git commit -m "feat(root_sim): multi-config (campeón + candidatos) + trayectoria de precio

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: `root_validation_pool.py` — validación en vivo + promoción

**Files:**
- Create: `copytrade/root_validation_pool.py`
- Test: `tests/test_root_validation_pool.py`

**Interfaces:**
- Consumes: `copytrade.root_sim.open_position` (Task 6), `copytrade.root_config.Config, config_to_dict, config_from_dict` (Task 1).
- Produces: `register_candidates(candidates: list[Config]) -> None`, `active_candidates() -> list[Config]`, `open_paper_position(config: Config, wallet_label: str, token_mint: str, entry_context: dict) -> None`, `promote_if_ready(candidate: Config, champion_history: list[dict]) -> bool`, `promote_candidates(candidates: list[Config], dataset: list[dict]) -> None`, module-level `CHAMPION_PATH: str`, `PROMOTION_MIN_TRADES: int` (monkeypatchable).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_validation_pool.py
import json

import pytest

import copytrade.root_validation_pool as pool
from copytrade.root_config import neutral_config, config_to_dict


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pool, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pool, "CHAMPION_PATH", str(tmp_path / "root_champion.json"))
    pool._active_candidates.clear()
    yield
    pool._active_candidates.clear()


def _write_history(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_register_and_active_candidates():
    cfg = neutral_config("gen0-0")
    pool.register_candidates([cfg])
    assert [c.config_id for c in pool.active_candidates()] == ["gen0-0"]


def test_promote_if_ready_falla_con_muestra_insuficiente(tmp_path):
    candidate = neutral_config("gen0-0")
    _write_history(pool._history_path("gen0-0"), [{"pnl_usd": 10.0}] * 5)  # menos de 20
    champion_history = [{"pnl_usd": 5.0}] * 25
    promoted = pool.promote_if_ready(candidate, champion_history)
    assert promoted is False
    assert not tmp_path.joinpath("root_champion.json").exists()


def test_promote_if_ready_promueve_con_ventaja_robusta(tmp_path):
    candidate = neutral_config("gen0-0")
    cand_history = [{"pnl_usd": 10.0}] * 25  # excl. mejor: 24*10=240
    _write_history(pool._history_path("gen0-0"), cand_history)
    champion_history = [{"pnl_usd": 1.0}] * 25  # excl. mejor: 24*1=24

    promoted = pool.promote_if_ready(candidate, champion_history)

    assert promoted is True
    saved = json.loads(tmp_path.joinpath("root_champion.json").read_text())
    assert saved["config_id"] == "gen0-0"


def test_promote_if_ready_no_promueve_por_un_solo_outlier(tmp_path):
    """El candidato gana en total solo por un trade gigante — excluyendo el
    mejor de cada lado, pierde. No debe promover."""
    candidate = neutral_config("gen0-0")
    cand_history = [{"pnl_usd": 500.0}] + [{"pnl_usd": -5.0}] * 24  # excl. mejor: -120
    _write_history(pool._history_path("gen0-0"), cand_history)
    champion_history = [{"pnl_usd": 2.0}] * 25  # excl. mejor: 24*2=48

    promoted = pool.promote_if_ready(candidate, champion_history)

    assert promoted is False


def test_promote_candidates_primer_arranque_guarda_el_primero_sin_campeon(tmp_path):
    candidates = [neutral_config("gen0-0"), neutral_config("gen0-1")]
    pool.promote_candidates(candidates, dataset=[])
    saved = json.loads(tmp_path.joinpath("root_champion.json").read_text())
    assert saved["config_id"] == "gen0-0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_validation_pool.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_validation_pool.py
"""
Corre los top-K candidatos de cada generación en paper-trading real, cada
uno con su propio balance de papel, para confirmar contra la realidad
antes de que el evolutivo los promueva a campeón. Reutiliza la mecánica de
apertura/monitoreo de root_sim.py, parametrizada por config y con
persistencia separada por config_id.
"""
import json
import os
import threading

from copytrade import root_sim
from copytrade.root_config import Config, config_from_dict, config_to_dict
from utils.logger import get_logger

log = get_logger("root_validation_pool")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHAMPION_PATH = os.path.join(_DATA_DIR, "root_champion.json")
PROMOTION_MIN_TRADES = int(os.getenv("ROOT_PROMOTION_MIN_TRADES", "20"))

_active_candidates: dict[str, Config] = {}
_lock = threading.Lock()


def _balance_path(config_id: str) -> str:
    return os.path.join(_DATA_DIR, f"root_validation_{config_id}_balance.json")


def _history_path(config_id: str) -> str:
    return os.path.join(_DATA_DIR, f"root_validation_{config_id}_history.json")


def register_candidates(candidates: list[Config]) -> None:
    """root_decider consulta esto para saber a qué candidatos, además del
    campeón, ofrecerles cada wallet-buy detectado — así acumulan muestra
    sin esperar su propio tráfico."""
    with _lock:
        for cfg in candidates:
            _active_candidates[cfg.config_id] = cfg


def active_candidates() -> list[Config]:
    with _lock:
        return list(_active_candidates.values())


def open_paper_position(config: Config, wallet_label: str, token_mint: str, entry_context: dict) -> None:
    root_sim.open_position(
        wallet_label, token_mint, entry_context,
        root_score=0, root_prob=0.0,
        config_id=config.config_id,
        stop_loss_pct=config.stop_loss_pct,
        max_hold_min=config.max_hold_min,
        trade_usd=config.trade_usd,
        balance_path=_balance_path(config.config_id),
        history_path=_history_path(config.config_id),
    )


def _read_history(config_id: str) -> list[dict]:
    path = _history_path(config_id)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_champion() -> Config | None:
    if not os.path.exists(CHAMPION_PATH):
        return None
    try:
        return config_from_dict(json.loads(open(CHAMPION_PATH).read()))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _save_champion(config: Config) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(CHAMPION_PATH, "w") as f:
        json.dump(config_to_dict(config), f, indent=2)


def promote_if_ready(candidate: Config, champion_history: list[dict]) -> bool:
    """Criterio de promoción: muestra mínima (PROMOTION_MIN_TRADES) en
    ambos lados, y el candidato tiene que ganarle al campeón incluso
    excluyendo el mejor trade de cada uno — así no se promueve por un solo
    outlier de suerte. Devuelve True si promovió."""
    cand_history = _read_history(candidate.config_id)
    if len(cand_history) < PROMOTION_MIN_TRADES or len(champion_history) < PROMOTION_MIN_TRADES:
        return False

    cand_pnls = [t["pnl_usd"] for t in cand_history]
    champ_pnls = [t["pnl_usd"] for t in champion_history]
    cand_excl = sum(cand_pnls) - max(cand_pnls)
    champ_excl = sum(champ_pnls) - max(champ_pnls)

    if cand_excl <= champ_excl:
        return False

    _save_champion(candidate)
    log.info(
        f"[root_validation_pool] {candidate.config_id} promovido a campeón "
        f"(pnl_excl=${cand_excl:.2f} vs ${champ_excl:.2f} del anterior, {len(cand_history)} trades)"
    )
    return True


def promote_candidates(candidates: list[Config], dataset: list[dict]) -> None:
    """Llamado desde el ciclo evolutivo: registra los candidatos para que
    root_decider empiece a ofrecerles trades, y revisa si alguno de los que
    ya estaban activos juntó muestra para promover. Si todavía no hay
    campeón (primer arranque), guarda el primer candidato tal cual."""
    register_candidates(candidates)
    champion = _load_champion()
    if champion is None:
        if candidates:
            _save_champion(candidates[0])
        return

    champion_history = _read_history(champion.config_id)
    for cfg in candidates:
        if cfg.config_id == champion.config_id:
            continue
        promote_if_ready(cfg, champion_history)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_validation_pool.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_validation_pool.py tests/test_root_validation_pool.py
git commit -m "feat(root): pool de validación en vivo + criterio de promoción robusto

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: `root_evolution.py` — ciclo evolutivo

**Files:**
- Create: `copytrade/root_evolution.py`
- Test: `tests/test_root_evolution.py`

**Interfaces:**
- Consumes: `copytrade.root_config.{Config, FEATURES, neutral_config, config_to_dict, config_from_dict}` (Task 1), `copytrade.root_backtest.backtest_config` (Task 4), `copytrade.root_seed_loader.load_seed_dataset` (Task 2), `copytrade.root_trajectory.load_trajectories` (Task 3), `copytrade.root_validation_pool.promote_candidates` (Task 7).
- Produces: `mutate(config: Config, config_id: str, std: float = MUTATION_STD) -> Config`, `initial_population(seed_config: Config, size: int = POPULATION_SIZE) -> list[Config]`, `run_generation(population: list[Config], dataset: list[dict], generation: int, top_k: int = TOP_K) -> list[Config]`, `build_dataset() -> list[dict]`, `evolution_loop() -> None`, `start() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_evolution.py
import random

import pytest

from copytrade.root_config import FEATURES, neutral_config
from copytrade.root_evolution import mutate, initial_population, run_generation


def test_mutate_cambia_config_id():
    cfg = neutral_config("origen")
    child = mutate(cfg, config_id="hijo")
    assert child.config_id == "hijo"
    assert cfg.config_id == "origen"  # no muta el original


def test_mutate_no_modifica_el_padre():
    cfg = neutral_config("origen")
    mutate(cfg, config_id="hijo", std=0.5)
    assert all(cfg.weights[f] == 0.0 for f in FEATURES)  # el padre queda intacto


def test_mutate_produce_variacion(monkeypatch):
    random.seed(42)
    cfg = neutral_config("origen")
    child = mutate(cfg, config_id="hijo", std=1.0)
    assert any(child.weights[f] != 0.0 for f in FEATURES) or child.bias != 0.0


def test_initial_population_tiene_el_tamano_pedido():
    seed = neutral_config("seed")
    population = initial_population(seed, size=6)
    assert len(population) == 6
    assert population[0] is seed  # la semilla siempre está, sin mutar


def test_run_generation_devuelve_mismo_tamano_que_entrada():
    seed = neutral_config("seed")
    population = initial_population(seed, size=8)
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 5.0, "won": True, "trajectory": None},
    ]
    next_gen = run_generation(population, dataset, generation=1, top_k=3)
    assert len(next_gen) == 8


def test_run_generation_conserva_a_los_sobrevivientes():
    """Una config con bias muy negativo nunca copia nada → pnl 0. Otra con
    bias positivo copia el único trade ganador del dataset → pnl > 0. La
    segunda debe sobrevivir a la siguiente generación."""
    loser = neutral_config("loser")
    loser.bias = -100.0
    winner = neutral_config("winner")
    winner.bias = 100.0
    population = [loser, winner]
    dataset = [
        {"wallet": "X", "entry_context": {}, "entry_price": 1.0, "pnl_pct": 20.0, "won": True, "trajectory": None},
    ]
    next_gen = run_generation(population, dataset, generation=1, top_k=1)
    assert next_gen[0].config_id == "winner"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_evolution.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# copytrade/root_evolution.py
"""
Ciclo evolutivo: mantiene una población de Config, la evalúa contra el
dataset acumulado (semilla + trades propios + trayectorias), se queda con
las mejores y muta para la siguiente generación. Corre en un thread daemon
aparte — si un ciclo falla, la config campeón sigue operando sin cambios.
"""
import copy
import json
import os
import random
import threading
import time

from copytrade.root_backtest import backtest_config
from copytrade.root_config import Config, FEATURES, config_from_dict, config_to_dict, neutral_config
from copytrade.root_seed_loader import load_seed_dataset
from copytrade.root_trajectory import load_trajectories
from utils.logger import get_logger

log = get_logger("root_evolution")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
POPULATION_PATH = os.path.join(_DATA_DIR, "root_population.json")

ENABLED = os.getenv("ROOT_EVOLUTION_ENABLED", "true").lower() == "true"
INTERVAL_MIN = float(os.getenv("ROOT_EVOLUTION_INTERVAL_MIN", "30"))
POPULATION_SIZE = int(os.getenv("ROOT_EVOLUTION_POPULATION_SIZE", "12"))
TOP_K = int(os.getenv("ROOT_EVOLUTION_TOP_K", "4"))
MUTATION_STD = float(os.getenv("ROOT_EVOLUTION_MUTATION_STD", "0.15"))


def mutate(config: Config, config_id: str, std: float = MUTATION_STD) -> Config:
    """Variante de `config`: cada peso y el bias se mueven con ruido
    gaussiano; stop_loss/max_hold también mutan dentro de rangos
    razonables. Nada queda fijo — todo lo que decide es mutable. No toca
    al padre (deepcopy primero)."""
    new = copy.deepcopy(config)
    new.config_id = config_id
    for f in FEATURES:
        new.weights[f] = round(new.weights.get(f, 0.0) + random.gauss(0, std), 4)
    new.bias = round(new.bias + random.gauss(0, std), 4)
    new.stop_loss_pct = max(3.0, round(new.stop_loss_pct + random.gauss(0, 2.0), 2))
    new.max_hold_min = max(5.0, round(new.max_hold_min + random.gauss(0, 5.0), 2))
    return new


def initial_population(seed_config: Config, size: int = POPULATION_SIZE) -> list[Config]:
    """La primera generación: la config semilla (neutra) tal cual + el
    resto mutado desde ahí — para no arrancar de un solo punto ciego."""
    population = [seed_config]
    for i in range(size - 1):
        population.append(mutate(seed_config, config_id=f"gen0-{i}"))
    return population


def run_generation(population: list[Config], dataset: list[dict], generation: int, top_k: int = TOP_K) -> list[Config]:
    """Backtestea toda la población, se queda con las top_k por
    pnl_usd_excluding_best (para no seleccionar por un outlier), y genera
    la siguiente generación mutando esas. Devuelve una población del mismo
    tamaño que la de entrada."""
    scored = [(cfg, backtest_config(cfg, dataset)) for cfg in population]
    scored.sort(key=lambda cs: cs[1]["pnl_usd_excluding_best"], reverse=True)
    survivors = [cfg for cfg, _ in scored[:top_k]]

    next_population = list(survivors)
    i = 0
    while len(next_population) < len(population):
        parent = survivors[i % len(survivors)]
        next_population.append(mutate(parent, config_id=f"gen{generation}-{i}"))
        i += 1
    return next_population


def _load_population(seed_config: Config) -> list[Config]:
    if not os.path.exists(POPULATION_PATH):
        return initial_population(seed_config)
    try:
        raw = json.loads(open(POPULATION_PATH).read())
        return [config_from_dict(c) for c in raw]
    except (json.JSONDecodeError, OSError, KeyError):
        return initial_population(seed_config)


def _save_population(population: list[Config]) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(POPULATION_PATH, "w") as f:
        json.dump([config_to_dict(c) for c in population], f, indent=2)


def build_dataset() -> list[dict]:
    """Junta el historial semilla con las trayectorias propias grabadas.
    Si el mismo mint se tradeó más de una vez, usa la primera trayectoria
    encontrada para ese mint — no hay id de posición compartido entre el
    historial semilla y root_trajectory para desambiguar mejor. Limitación
    conocida, documentada acá en vez de adivinar cuál corresponde."""
    seed = load_seed_dataset(
        os.path.join(_DATA_DIR, "sim_history.json"),
        os.path.join(_DATA_DIR, "root_sim_history.json"),
    )
    trajectories_by_mint: dict[str, list] = {}
    for traj in load_trajectories():
        trajectories_by_mint.setdefault(traj["token_mint"], []).append(traj["snapshots"])
    for trade in seed:
        candidates = trajectories_by_mint.get(trade.get("token_mint"))
        if candidates:
            trade["trajectory"] = candidates[0]
    return seed


def evolution_loop() -> None:
    """Corre para siempre en un thread daemon: cada INTERVAL_MIN minutos,
    evoluciona la población y manda a los mejores a validación en vivo.
    Cualquier excepción se loguea y se descarta — nunca tumba el bot ni deja
    de decidir con el campeón actual."""
    from copytrade.root_validation_pool import promote_candidates

    generation = 0
    seed_config = neutral_config()
    population = _load_population(seed_config)
    while True:
        try:
            dataset = build_dataset()
            population = run_generation(population, dataset, generation)
            _save_population(population)
            promote_candidates(population[:TOP_K], dataset)
            generation += 1
        except Exception as e:
            log.warning(f"[root_evolution] ciclo falló, sigue con el campeón actual — {e}")
        time.sleep(INTERVAL_MIN * 60)


def start() -> None:
    if not ENABLED:
        log.info("[root_evolution] deshabilitado (ROOT_EVOLUTION_ENABLED=false)")
        return
    threading.Thread(target=evolution_loop, daemon=True).start()
    log.info(f"[root_evolution] arrancado — cada {INTERVAL_MIN}min, población={POPULATION_SIZE}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_root_evolution.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_evolution.py tests/test_root_evolution.py
git commit -m "feat(root): ciclo evolutivo — mutación, selección, siguiente generación

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: `root_decider.py` + reconectar `scorer.py` + retirar `root_shadow.py`

**Files:**
- Create: `copytrade/root_decider.py`
- Test: `tests/test_root_decider.py`
- Modify: `copytrade/scorer.py:172-192` (función `should_copy`)
- Delete: `copytrade/root_shadow.py`, `tests/test_root_shadow.py` (su rol queda 100% subsumido por `root_decider.py`; su único invocador era `scorer.should_copy`, que se reescribe en este task)

**Interfaces:**
- Consumes: `copytrade.root_config.{Config, decide, neutral_config, config_from_dict}` (Task 1), `copytrade.root_validation_pool.{active_candidates, open_paper_position}` (Task 7), `copytrade.root_sim.open_position` (Task 6).
- Produces: `load_champion() -> Config`, `decide(wallet_label: str, entry_context: dict | None, token_mint: str | None = None) -> str` ("COPIAR"|"SKIP").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_root_decider.py
import json
import threading

import pytest

import copytrade.root_decider as decider

ENTRY_CONTEXT = {"price_usd": 1.0e-05, "mcap_usd": 12000}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(decider, "CHAMPION_PATH", str(tmp_path / "root_champion.json"))
    yield


def test_load_champion_sin_archivo_devuelve_config_neutra():
    cfg = decider.load_champion()
    assert cfg.config_id == "champion-seed"


def test_load_champion_lee_archivo_existente(tmp_path):
    from copytrade.root_config import neutral_config, config_to_dict
    cfg = neutral_config("mi-campeon")
    cfg.bias = 5.0
    open(decider.CHAMPION_PATH, "w").write(json.dumps(config_to_dict(cfg)))

    loaded = decider.load_champion()
    assert loaded.config_id == "mi-campeon"
    assert loaded.bias == 5.0


def test_decide_deshabilitado_devuelve_skip(monkeypatch):
    monkeypatch.setattr(decider, "ENABLED", False)
    assert decider.decide("Theo", ENTRY_CONTEXT, token_mint="MINT1") == "SKIP"


def test_decide_sin_entry_context_devuelve_skip():
    assert decider.decide("Theo", None, token_mint="MINT1") == "SKIP"


def test_decide_usa_config_neutra_y_copia_por_defecto(monkeypatch):
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _FakeThread())
    decision = decider.decide("Theo", ENTRY_CONTEXT, token_mint="MINT1")
    assert decision == "COPIAR"


def test_decide_lanza_thread_daemon_sin_bloquear(monkeypatch):
    started = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: _RecordingThread(started, *a, **k))
    decider.decide("Theo", ENTRY_CONTEXT, token_mint="MINT1")
    assert len(started) == 1


class _FakeThread:
    def start(self):
        pass


class _RecordingThread:
    def __init__(self, log, target=None, args=(), daemon=None):
        self.target, self.args = target, args
        log.append(self)

    def start(self):
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_root_decider.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3a: Write `copytrade/root_decider.py`**

```python
# copytrade/root_decider.py
"""
Punto de entrada real de decisión para copytrade — reemplaza el rol de
root_shadow.py (que solo observaba). Cuando scorer.should_copy() detecta un
wallet-buy, esto decide COPIAR/SKIP usando la config campeón vigente (o la
neutra si todavía no hay campeón guardado) y, si decide copiar, abre la
posición de papel real. Además ofrece el mismo trade a los candidatos
activos en validación (root_validation_pool) para que acumulen muestra sin
esperar tráfico propio.

La decisión en sí (score_entry_context) es una cuenta liviana en memoria,
así que se calcula de forma síncrona y se devuelve ya mismo — el trabajo
pesado (abrir posiciones, tocar red vía DexScreener) se lanza en un thread
daemon aparte y nunca puede bloquear ni tumbar el camino real de trading.
"""
import json
import os
import threading

from copytrade import root_validation_pool
from copytrade.root_config import Config, config_from_dict, decide as config_decide, neutral_config
from utils.logger import get_logger

log = get_logger("root_decider")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHAMPION_PATH = os.path.join(_DATA_DIR, "root_champion.json")

ENABLED = os.getenv("ROOT_DECIDER_ENABLED", "true").lower() == "true"


def load_champion() -> Config:
    if not os.path.exists(CHAMPION_PATH):
        return neutral_config()
    try:
        return config_from_dict(json.loads(open(CHAMPION_PATH).read()))
    except (json.JSONDecodeError, OSError, KeyError):
        return neutral_config()


def _run(wallet_label: str, entry_context: dict, token_mint: str | None, champion: Config, decision: str) -> None:
    log.info(f"[root_decider] {wallet_label} → {decision} (config={champion.config_id})")

    if decision == "COPIAR" and token_mint:
        from copytrade.root_sim import open_position
        try:
            open_position(
                wallet_label, token_mint, entry_context,
                root_score=0, root_prob=0.0,
                config_id=champion.config_id,
                stop_loss_pct=champion.stop_loss_pct,
                max_hold_min=champion.max_hold_min,
                trade_usd=champion.trade_usd,
            )
        except Exception as e:
            log.warning(f"[root_decider] {wallet_label}: no se pudo abrir posición del campeón — {e}")

    if not token_mint:
        return
    for candidate in root_validation_pool.active_candidates():
        try:
            if config_decide(candidate, wallet_label, entry_context) == "COPIAR":
                root_validation_pool.open_paper_position(candidate, wallet_label, token_mint, entry_context)
        except Exception as e:
            log.warning(f"[root_decider] candidato {candidate.config_id} falló — {e}")


def decide(wallet_label: str, entry_context: dict | None, token_mint: str | None = None) -> str:
    """Punto de entrada llamado desde scorer.should_copy()."""
    if not ENABLED or not entry_context:
        return "SKIP"

    champion = load_champion()
    decision = config_decide(champion, wallet_label, entry_context)
    threading.Thread(
        target=_run, args=(wallet_label, entry_context, token_mint, champion, decision), daemon=True,
    ).start()
    return decision
```

- [ ] **Step 3b: Run tests to verify they pass**

Run: `pytest tests/test_root_decider.py -v`
Expected: 6 passed

- [ ] **Step 3c: Reconectar `copytrade/scorer.py`**

Reemplazar la función `should_copy` (líneas 172-192 actuales) por:

```python
def should_copy(
    wallet_label: str,
    token_info: dict,
    entry_context: dict | None = None,
    token_mint: str | None = None,
) -> tuple[bool, str]:
    """ROOT decide de verdad acá (root_decider.decide()) — la lógica de
    reglas/Groq-patterns de score_token() ya no gatea la decisión (queda
    definida más arriba en este archivo pero sin usarse desde acá; se deja
    intacta por si hace falta como referencia, no se borra en este cambio
    para no arrastrar el borrado a hold_predictor.py y sus dependientes sin
    auditarlos aparte)."""
    from copytrade.root_decider import decide as root_decide
    decision = root_decide(wallet_label, entry_context, token_mint=token_mint)
    passed = decision == "COPIAR"
    log.info(f"[scorer] {wallet_label} → ROOT decide {'✅ COPIAR' if passed else '❌ SKIP'}")
    return passed, f"root_decision={decision}"
```

- [ ] **Step 3d: Borrar `root_shadow.py` y su test (subsumidos por `root_decider.py`)**

```bash
git rm copytrade/root_shadow.py tests/test_root_shadow.py
```

- [ ] **Step 4: Run full test suite to verify nothing broke**

Run: `pytest tests/ -v`
Expected: todos los tests pasan, sin referencias rotas a `root_shadow`. Si `simulator.py` o `executor.py` tienen tests que mockeaban `shadow_score`, ajustarlos para no referenciar `root_shadow` (buscar con `grep -rn "root_shadow" tests/` antes de correr — no debería quedar ninguna).

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_decider.py copytrade/scorer.py tests/test_root_decider.py
git commit -m "feat(root): ROOT decide de verdad en scorer.should_copy(), retira root_shadow

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: arrancar el ciclo evolutivo al boot + variables de entorno

**Files:**
- Modify: `main.py:227-230` (justo antes de `asyncio.run(watch_all())`)
- Modify: `.env.example` (documentar variables nuevas)
- Test: manual, ver Step 3 (arrancar el ciclo real de background no es algo que un test unitario deba ejecutar en tiempo real — se verifica con una corrida corta manual)

**Interfaces:**
- Consumes: `copytrade.root_evolution.start` (Task 8).

- [ ] **Step 1: Modificar `main.py`**

Ubicar el bloque (alrededor de la línea 227):

```python
    console.print(Rule("[dim]Conectando WebSocket...[/]", style="bright_black"))
    console.print()

    try:
        asyncio.run(watch_all())
```

Reemplazar por:

```python
    from copytrade.root_evolution import start as start_root_evolution
    start_root_evolution()

    console.print(Rule("[dim]Conectando WebSocket...[/]", style="bright_black"))
    console.print()

    try:
        asyncio.run(watch_all())
```

- [ ] **Step 2: Documentar variables nuevas en `.env.example`**

Agregar al final del archivo:

```bash
# --- ROOT: decisor evolutivo (Proyecto A, 2026-08-05) ---
ROOT_DECIDER_ENABLED=true
ROOT_SIM_ENABLED=true
ROOT_SIM_INITIAL_BALANCE=1000
ROOT_SIM_TRADE_USD=50
ROOT_SIM_MAX_HOLD_MIN=30
ROOT_SIM_POLL_INTERVAL_S=15
ROOT_EVOLUTION_ENABLED=true
ROOT_EVOLUTION_INTERVAL_MIN=30
ROOT_EVOLUTION_POPULATION_SIZE=12
ROOT_EVOLUTION_TOP_K=4
ROOT_EVOLUTION_MUTATION_STD=0.15
ROOT_PROMOTION_MIN_TRADES=20
```

- [ ] **Step 3: Verificación manual de arranque (no bloquea, no truena)**

Run:
```bash
cd ~/Proyectos/botsolana && ROOT_EVOLUTION_INTERVAL_MIN=0.1 python3 -c "
from copytrade.root_evolution import start, build_dataset
start()
import time; time.sleep(15)
print('dataset size:', len(build_dataset()))
import os
print('champion existe:', os.path.exists('data/root_champion.json'))
"
```
Expected: no excepciones sin capturar, imprime un `dataset size` > 0 (los 1,219 + 13 semilla) y `champion existe: True` tras al menos un ciclo.

- [ ] **Step 4: Correr toda la suite una vez más**

Run: `pytest tests/ -v`
Expected: todos los tests pasan (los nuevos + los preexistentes, sin regressions).

- [ ] **Step 5: Commit**

```bash
git add main.py .env.example
git commit -m "feat(root): arranca el ciclo evolutivo en boot + env vars documentadas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review Notes

- **Cobertura del spec:** trayectoria de precio (Task 3), backtest con y sin trayectoria (Task 4), wallet behavior mining (Task 5), evolutivo generar→backtestear→mutar (Task 8), validación en vivo + promoción robusta anti-outlier (Task 7), ROOT como decisor real reemplazando reglas (Task 9), fail-safe si el ciclo falla (Task 8 `evolution_loop`), semilla de 1,219 + 13 trades (Task 2) — todas las secciones del spec tienen task.
- **Placeholders:** ninguno — cada step tiene código completo, sin "TODO"/"similar a".
- **Consistencia de tipos:** `Config`, `config_to_dict`/`config_from_dict`, `decide`, `score_entry_context` se usan con la misma firma en Tasks 4, 6, 7, 8, 9. `open_position(..., config_id=, stop_loss_pct=, max_hold_min=, trade_usd=, balance_path=, history_path=)` se llama igual desde `root_decider.py` (Task 9) y `root_validation_pool.py` (Task 7), coincide con la firma definida en Task 6.
- **Deuda documentada, no oculta:** `score_token`/`hold_predictor.py` quedan sin borrar (Task 9) con razón explícita; el emparejamiento trayectoria↔trade histórico por mint (Task 8, `build_dataset`) es una simplificación conocida, comentada en el código.
