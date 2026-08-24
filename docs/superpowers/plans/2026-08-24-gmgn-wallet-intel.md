# GMGN Wallet Intel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar a botsolana un cliente GMGN de solo-lectura (winrate/PnL real por wallet + descubrimiento de smart money) que sirve como inteligencia offline — sin tocar el pipeline de detección/decisión/ejecución en tiempo real.

**Architecture:** Un cliente HTTP delgado (`copytrade/gmgn_client.py`, fail-open, cacheado en disco) es la única pieza que habla con GMGN. Dos consumidores lo usan: una función pura de blending en `root_wallet_miner.py` (mejora el prior de wallet_trust) y un script standalone en la raíz del repo (`gmgn_wallet_scout.py`, se corre a mano) que descubre candidatas nuevas. Ninguno de los dos se importa desde `watcher.py`, `root_decider.py` ni `executor.py`.

**Tech Stack:** Python 3.10, `httpx` (ya en requirements.txt), `pytest` con `monkeypatch`/`tmp_path` (convención ya usada en `tests/test_root_wallet_miner.py`, `tests/test_root_seed_loader.py`).

## Global Constraints

- No instalar ni invocar `gmgn-swap` ni `gmgn-cooking` — solo lectura de datos.
- `gmgn_client.py` es **fail-open**: sin `GMGN_API_KEY`, timeout, o error HTTP → devuelve `None`/`[]` y loguea un warning, nunca lanza excepción.
- No modificar `watcher.py`, `executor.py`, `root_decider.py`.
- No escribir automáticamente en `config.py` (WALLET_LABELS/ELITE_WALLETS/WALLET_WEIGHTS) — toda graduación de wallet sigue siendo decisión manual del usuario.
- Cache en disco con TTL, en `data/gmgn_cache/` — patrón indexing+caching del proyecto.
- Scripts standalone viven en la raíz del repo (convención existente: `report_root_evolution_status.py`, `analyze_drift.py`), no en un subdirectorio `scripts/`. *(Nota: esto difiere de la ruta `scripts/gmgn_wallet_scout.py` mencionada en el spec — se ajusta aquí a la convención real del repo; el resto del diseño del spec no cambia.)*

---

### Task 1: `copytrade/gmgn_client.py` — cliente HTTP cacheado, fail-open

**Files:**
- Create: `copytrade/gmgn_client.py`
- Test: `tests/test_gmgn_client.py`

**Interfaces:**
- Consumes: `utils.logger.get_logger` (ya existe, firma `get_logger(name: str) -> logging.Logger`).
- Produces (usado por Task 2 y Task 3):
  - `get_wallet_stats(address: str, chain: str = "sol") -> dict | None`
  - `get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list[dict]`
  - Módulo expone también `_request`, `GMGN_API_KEY`, `CACHE_DIR`, `CACHE_TTL_SECONDS` como atributos monkeypatcheables en tests.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_gmgn_client.py`:

```python
import time

import httpx
import pytest

import copytrade.gmgn_client as gc


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(gc, "CACHE_DIR", str(tmp_path))


def test_request_sin_api_key_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "")
    assert gc._request("/wallet/stats", {"address": "ABC"}) is None


def test_request_ok_devuelve_json(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "testkey")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"win_rate": 61.5}

    def fake_get(url, params, headers, timeout):
        assert headers["Authorization"] == "Bearer testkey"
        return FakeResponse()

    monkeypatch.setattr(gc.httpx, "get", fake_get)
    assert gc._request("/wallet/stats", {"address": "ABC"}) == {"win_rate": 61.5}


def test_request_error_de_red_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "GMGN_API_KEY", "testkey")

    def fake_get(*a, **kw):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(gc.httpx, "get", fake_get)
    assert gc._request("/wallet/stats", {"address": "ABC"}) is None


def test_get_wallet_stats_usa_cache_si_esta_fresca(monkeypatch):
    calls = []

    def fake_request(path, params):
        calls.append(1)
        return {"win_rate": 70.0}

    monkeypatch.setattr(gc, "_request", fake_request)
    r1 = gc.get_wallet_stats("ABC")
    r2 = gc.get_wallet_stats("ABC")
    assert r1 == {"win_rate": 70.0}
    assert r2 == {"win_rate": 70.0}
    assert len(calls) == 1


def test_get_wallet_stats_cache_expirada_reconsulta(monkeypatch):
    monkeypatch.setattr(gc, "CACHE_TTL_SECONDS", 0)
    calls = []

    def fake_request(path, params):
        calls.append(1)
        return {"win_rate": 70.0}

    monkeypatch.setattr(gc, "_request", fake_request)
    gc.get_wallet_stats("ABC")
    time.sleep(0.01)
    gc.get_wallet_stats("ABC")
    assert len(calls) == 2


def test_get_wallet_stats_fallback_a_cache_expirada_si_falla_red(monkeypatch):
    monkeypatch.setattr(gc, "CACHE_TTL_SECONDS", 0)
    responses = [{"win_rate": 70.0}, None]

    def fake_request(path, params):
        return responses.pop(0)

    monkeypatch.setattr(gc, "_request", fake_request)
    r1 = gc.get_wallet_stats("ABC")
    time.sleep(0.01)
    r2 = gc.get_wallet_stats("ABC")
    assert r1 == {"win_rate": 70.0}
    assert r2 == {"win_rate": 70.0}


def test_get_wallet_stats_sin_cache_ni_red_devuelve_none(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: None)
    assert gc.get_wallet_stats("NUEVA") is None


def test_get_smart_money_wallets_devuelve_lista(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: [{"address": "X", "win_rate": 80.0}])
    result = gc.get_smart_money_wallets(limit=10)
    assert result == [{"address": "X", "win_rate": 80.0}]


def test_get_smart_money_wallets_sin_cache_ni_red_devuelve_lista_vacia(monkeypatch):
    monkeypatch.setattr(gc, "_request", lambda path, params: None)
    assert gc.get_smart_money_wallets() == []
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `pytest tests/test_gmgn_client.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'copytrade.gmgn_client'`

- [ ] **Step 3: Implementar `copytrade/gmgn_client.py`**

```python
"""
Cliente HTTP delgado a la GMGN OpenAPI REST — inteligencia offline de
wallets (winrate/PnL real, smart money). Fail-open: cualquier fallo de
red, de la API, o ausencia de GMGN_API_KEY devuelve None/[] y loguea un
warning, nunca rompe al caller. Deliberadamente no se usa en el camino de
decisión en tiempo real (watcher.py → root_decider.py → executor.py) —
ver docs/superpowers/specs/2026-08-24-gmgn-wallet-intel-design.md.
"""
import hashlib
import json
import os
import time

import httpx

from utils.logger import get_logger

log = get_logger("gmgn_client")

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_BASE_URL = os.getenv("GMGN_BASE_URL", "https://api.gmgn.ai")
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "gmgn_cache")
CACHE_TTL_SECONDS = int(os.getenv("GMGN_CACHE_TTL_SECONDS", str(6 * 3600)))


def _cache_path(cache_key: str) -> str:
    digest = hashlib.sha256(cache_key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.json")


def _cache_read(cache_key: str, allow_expired: bool = False):
    path = _cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            entry = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    fresh = (time.time() - entry["cached_at"]) < CACHE_TTL_SECONDS
    if fresh or allow_expired:
        return entry["data"]
    return None


def _cache_write(cache_key: str, data) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(cache_key), "w") as f:
        json.dump({"cached_at": time.time(), "data": data}, f)


def _request(path: str, params: dict):
    """Única función que toca la red — se monkeypatchea en tests."""
    if not GMGN_API_KEY:
        log.warning("GMGN_API_KEY no configurada — gmgn_client en modo no-op")
        return None
    try:
        r = httpx.get(
            f"{GMGN_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {GMGN_API_KEY}"},
            timeout=5,
        )
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning(f"[gmgn_client] error consultando {path}: {e}")
        return None


def get_wallet_stats(address: str, chain: str = "sol") -> dict | None:
    """PnL/winrate/holdings reales de una wallet vía GMGN. None si falla,
    no hay API key, y no hay cache (ni expirada) disponible como fallback."""
    cache_key = f"wallet_stats:{chain}:{address}"
    cached = _cache_read(cache_key)
    if cached is not None:
        return cached

    data = _request("/wallet/stats", {"chain": chain, "address": address})
    if data is not None:
        _cache_write(cache_key, data)
        return data

    return _cache_read(cache_key, allow_expired=True)


def get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list[dict]:
    """Wallets clasificadas como smart money por GMGN. [] si falla, no hay
    API key, y no hay cache (ni expirada) disponible como fallback."""
    cache_key = f"smart_money:{chain}:{limit}"
    cached = _cache_read(cache_key)
    if cached is not None:
        return cached

    data = _request("/wallet/smart_money", {"chain": chain, "limit": limit})
    if data is not None:
        _cache_write(cache_key, data)
        return data

    fallback = _cache_read(cache_key, allow_expired=True)
    return fallback if fallback is not None else []
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `pytest tests/test_gmgn_client.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add copytrade/gmgn_client.py tests/test_gmgn_client.py
git commit -m "feat(gmgn): cliente HTTP cacheado y fail-open a GMGN OpenAPI"
```

---

### Task 2: `root_wallet_miner.blend_with_gmgn()` — prior mezclado con datos reales

**Files:**
- Modify: `copytrade/root_wallet_miner.py`
- Test: `tests/test_root_wallet_miner.py`

**Interfaces:**
- Consumes: `copytrade.gmgn_client.get_wallet_stats(address: str, chain: str = "sol") -> dict | None` (Task 1).
- Produces: `blend_with_gmgn(wallet_trust: dict[str, float], addresses: list[str], gmgn_weight: float = 0.5) -> dict[str, float]` — no muta `wallet_trust`, devuelve un dict nuevo.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `tests/test_root_wallet_miner.py`:

```python
import copytrade.root_wallet_miner as rwm


def test_blend_with_gmgn_mezcla_prior_propio_y_datos_reales(monkeypatch):
    monkeypatch.setattr(
        rwm, "get_wallet_stats",
        lambda address, chain="sol": {"win_rate": 80.0} if address == "WALLET1" else None,
    )
    trust = {"WALLET1": 0.0}
    blended = rwm.blend_with_gmgn(trust, ["WALLET1"], gmgn_weight=0.5)
    # gmgn_trust = (80-50)*0.02 = 0.6 ; own=0.0 ; blend = 0*0.5 + 0.6*0.5 = 0.3
    assert blended["WALLET1"] == pytest.approx(0.3)


def test_blend_with_gmgn_sin_datos_gmgn_deja_valor_original(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": None)
    trust = {"WALLET1": 0.42}
    blended = rwm.blend_with_gmgn(trust, ["WALLET1"])
    assert blended["WALLET1"] == pytest.approx(0.42)


def test_blend_with_gmgn_wallet_nueva_sin_prior_previo(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": {"win_rate": 90.0})
    blended = rwm.blend_with_gmgn({}, ["NUEVA"], gmgn_weight=1.0)
    # sin prior propio (default 0.0), peso 1.0 → puro gmgn_trust = (90-50)*0.02 = 0.8
    assert blended["NUEVA"] == pytest.approx(0.8)


def test_blend_with_gmgn_no_muta_el_dict_original(monkeypatch):
    monkeypatch.setattr(rwm, "get_wallet_stats", lambda address, chain="sol": {"win_rate": 80.0})
    original = {"WALLET1": 0.0}
    rwm.blend_with_gmgn(original, ["WALLET1"])
    assert original["WALLET1"] == 0.0
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `pytest tests/test_root_wallet_miner.py -v`
Expected: FAIL con `AttributeError: module 'copytrade.root_wallet_miner' has no attribute 'blend_with_gmgn'`

- [ ] **Step 3: Implementar `blend_with_gmgn` en `copytrade/root_wallet_miner.py`**

Agregar al final del archivo (después de `initial_wallet_trust`), y agregar el import al inicio del archivo:

```python
from copytrade.gmgn_client import get_wallet_stats
```

```python
def blend_with_gmgn(
    wallet_trust: dict[str, float], addresses: list[str], gmgn_weight: float = 0.5,
) -> dict[str, float]:
    """Mezcla el prior propio (wallet_trust, típicamente salido de
    initial_wallet_trust) con el winrate real de GMGN cuando hay datos
    disponibles para esa dirección. Si GMGN no tiene datos (sin API key,
    error de red, wallet desconocida), esa wallet queda con su valor
    original sin cambios — no se llama a la red desde initial_wallet_trust
    ni desde ningún otro punto del hot path, solo desde donde se arma el
    seed inicial. gmgn_weight pondera cuánto pesa GMGN frente al prior
    propio (0.5 = mitad y mitad; 1.0 = solo GMGN)."""
    out = dict(wallet_trust)
    for address in addresses:
        stats = get_wallet_stats(address)
        if not stats or stats.get("win_rate") is None:
            continue
        gmgn_trust = round((stats["win_rate"] - 50.0) * 0.02, 4)
        own_trust = out.get(address, 0.0)
        out[address] = round(own_trust * (1 - gmgn_weight) + gmgn_trust * gmgn_weight, 4)
    return out
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `pytest tests/test_root_wallet_miner.py -v`
Expected: PASS (7 tests: 3 originales + 4 nuevos)

- [ ] **Step 5: Commit**

```bash
git add copytrade/root_wallet_miner.py tests/test_root_wallet_miner.py
git commit -m "feat(gmgn): blend_with_gmgn mezcla prior propio con winrate real GMGN"
```

---

### Task 3: `gmgn_wallet_scout.py` — descubrimiento manual de candidatas

**Files:**
- Create: `gmgn_wallet_scout.py` (raíz del repo, mismo patrón que `report_root_evolution_status.py`)
- Test: `tests/test_gmgn_wallet_scout.py`

**Interfaces:**
- Consumes:
  - `copytrade.gmgn_client.get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list[dict]` (Task 1).
  - `config.WALLET_LABELS: dict[str, str]` (ya existe en `config.py:35`).
- Produces: `find_new_candidates(smart_money: list[dict], known_addresses: set[str]) -> list[dict]` — pura, sin red, usada por el test y por `main()`.

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_gmgn_wallet_scout.py`:

```python
from gmgn_wallet_scout import find_new_candidates


def test_find_new_candidates_excluye_conocidas():
    smart_money = [
        {"address": "A", "win_rate": 80.0},
        {"address": "B", "win_rate": 70.0},
    ]
    result = find_new_candidates(smart_money, known_addresses={"A"})
    assert result == [{"address": "B", "win_rate": 70.0}]


def test_find_new_candidates_sin_conocidas_devuelve_todas():
    smart_money = [{"address": "A", "win_rate": 80.0}]
    result = find_new_candidates(smart_money, known_addresses=set())
    assert result == smart_money


def test_find_new_candidates_lista_vacia():
    assert find_new_candidates([], known_addresses={"A"}) == []
```

- [ ] **Step 2: Correr los tests y confirmar que fallan**

Run: `pytest tests/test_gmgn_wallet_scout.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'gmgn_wallet_scout'`

- [ ] **Step 3: Implementar `gmgn_wallet_scout.py`**

```python
#!/usr/bin/env python3
"""
Script manual (no corre en el loop del bot ni en launchd) para descubrir
wallets candidatas nuevas vía el smart money de GMGN, cruzando contra las
ya conocidas en config.WALLET_LABELS. No escribe en config.py — solo
imprime un reporte para que el usuario decida a mano si las agrega en
fase SIM (mismo flujo manual de siempre).

Uso: python gmgn_wallet_scout.py [--chain sol] [--limit 50]
"""
import argparse

from copytrade.gmgn_client import get_smart_money_wallets
from utils.logger import get_logger

log = get_logger("gmgn_wallet_scout")


def find_new_candidates(smart_money: list[dict], known_addresses: set[str]) -> list[dict]:
    """Filtra smart_money (lista de dicts con al menos 'address') excluyendo
    las que ya están en known_addresses. Preserva el orden de entrada."""
    return [w for w in smart_money if w.get("address") not in known_addresses]


def _print_report(candidates: list[dict]) -> None:
    if not candidates:
        print("Sin candidatas nuevas.")
        return
    print(f"{'Dirección':<46} {'Win rate':>10} {'PnL':>12} {'Clasificación':<15}")
    for c in candidates:
        addr = c.get("address", "?")
        wr = c.get("win_rate")
        pnl = c.get("pnl_usd")
        tag = c.get("tag", "?")
        wr_s = f"{wr:.1f}%" if wr is not None else "?"
        pnl_s = f"${pnl:,.0f}" if pnl is not None else "?"
        print(f"{addr:<46} {wr_s:>10} {pnl_s:>12} {tag:<15}")


def main() -> None:
    import config

    parser = argparse.ArgumentParser(description="Descubre wallets candidatas nuevas vía GMGN smart money.")
    parser.add_argument("--chain", default="sol")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    smart_money = get_smart_money_wallets(chain=args.chain, limit=args.limit)
    known = set(config.WALLET_LABELS.keys())
    candidates = find_new_candidates(smart_money, known)
    log.info(f"[gmgn_wallet_scout] {len(smart_money)} wallets smart money, {len(candidates)} nuevas")
    _print_report(candidates)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr los tests y confirmar que pasan**

Run: `pytest tests/test_gmgn_wallet_scout.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add gmgn_wallet_scout.py tests/test_gmgn_wallet_scout.py
git commit -m "feat(gmgn): script manual de descubrimiento de wallets smart money"
```

---

### Task 4: config y `.env.example` — `GMGN_API_KEY`

**Files:**
- Modify: `.env.example`
- Test: n/a (config declarativa; se verifica con un smoke test de import)

**Interfaces:**
- Consumes: nada nuevo.
- Produces: variable de entorno `GMGN_API_KEY` leída directamente por `copytrade/gmgn_client.py` (Task 1) vía `os.getenv`. No requiere tocar `config.py` — `gmgn_client.py` ya lee la env var por sí mismo, siguiendo el mismo patrón que `ETHERSCAN_API_KEY`/`ALCHEMY_API_KEY` en `config.py:15-16` (esas sí están en `config.py` porque otros módulos las importan desde ahí; `GMGN_API_KEY` no lo necesita porque solo la usa `gmgn_client.py`).

- [ ] **Step 1: Agregar la entrada a `.env.example`**

Insertar después del bloque de Alchemy Webhooks (tras la línea `ETH_POLL_INTERVAL=3  # fallback si no hay webhooks`):

```
# GMGN — inteligencia offline de wallets (winrate/PnL real, smart money).
# Crea una API key gratis en: https://gmgn.ai/ai
# Sin esta key, gmgn_client.py opera en modo no-op (no rompe el bot).
GMGN_API_KEY=
GMGN_CACHE_TTL_SECONDS=21600
```

- [ ] **Step 2: Smoke test — confirmar que el bot sigue arrancando sin la key**

Run: `python -c "import copytrade.gmgn_client as gc; print(gc.get_wallet_stats('cualquiera'))"`
Expected: imprime `None` (sin excepción), con un log `WARNING` de "GMGN_API_KEY no configurada — gmgn_client en modo no-op"

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(gmgn): documenta GMGN_API_KEY en .env.example"
```

---

## Self-Review Notes

- **Cobertura del spec:** cliente cacheado/fail-open (Task 1) ✓, descubrimiento de candidatas (Task 3) ✓, prior mejorado en `root_wallet_miner` (Task 2) ✓, `GMGN_API_KEY` en `.env` (Task 4) ✓, ningún cambio en `watcher.py`/`executor.py`/`root_decider.py` (ninguna task los toca) ✓, exclusión de `gmgn-swap`/`gmgn-cooking` (ya resuelto en la instalación de skills, sin tarea de código) ✓.
- **Placeholders:** ninguno — todo el código de cada step está completo.
- **Consistencia de tipos:** `get_wallet_stats(address: str, chain: str = "sol") -> dict | None` se usa igual en Task 2 y Task 3; `get_smart_money_wallets(chain: str = "sol", limit: int = 50) -> list[dict]` igual en Task 1 y Task 3.
- **Ruta ajustada respecto al spec:** el spec dice `scripts/gmgn_wallet_scout.py`; este plan usa `gmgn_wallet_scout.py` en la raíz para seguir la convención real del repo (ver Global Constraints).
