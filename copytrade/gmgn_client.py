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
