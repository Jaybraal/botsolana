"""
Wrapper para la API pública de DexScreener.
Rate limit público: ~300 req/min (~5 req/s).
Usamos un rate limiter GLOBAL (no por path) de 0.35s entre requests para
no superar nunca el límite aunque el scout consulte 50 tokens seguidos.
"""

import asyncio
import time
import httpx
from utils.logger import get_logger

log = get_logger("dexscreener")
BASE = "https://api.dexscreener.com"

# Clientes compartidos con keep-alive — síncronos para código legado, async para hot path
_client       = httpx.Client(timeout=10, headers={"Accept": "application/json"})
_async_client = httpx.AsyncClient(timeout=8, headers={"Accept": "application/json"})

# Rate limiter async — 1 req cada 0.35s (~170 req/min, bajo el límite público)
_async_last_request: float = 0.0
_async_lock = asyncio.Lock()

# Rate limiter GLOBAL: 1 request cada 0.35s máximo (~170 req/min, bien bajo el límite)
_last_request: float = 0.0
_MIN_INTERVAL = 0.35


def _get(path: str, params: dict | None = None) -> dict | list | None:
    """GET con rate limiting global y reintentos con backoff."""
    global _last_request
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

    for attempt in range(3):
        try:
            r = _client.get(f"{BASE}{path}", params=params)
            if r.status_code == 429:
                backoff = 5 * (attempt + 1)
                log.warning(f"DexScreener rate limit — esperando {backoff}s...")
                time.sleep(backoff)
                _last_request = time.monotonic()
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            log.debug(f"DexScreener HTTP {e.response.status_code} en {path}")
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                log.debug(f"DexScreener error {path}: {e}")
                return None
    return None


# ── Endpoints ──────────────────────────────────────────────────────────

def get_trending_solana() -> list[dict]:
    """
    Tokens más boosteados en Solana ahora mismo.
    Endpoint: /token-boosts/top/v1
    Devuelve lista con {chainId, tokenAddress, amount, totalAmount, ...}
    """
    data = _get("/token-boosts/top/v1")
    if not isinstance(data, list):
        return []
    return [t for t in data if t.get("chainId") == "solana"]


def get_new_solana_tokens() -> list[dict]:
    """
    Tokens recién creados con perfil en Solana.
    Endpoint: /token-profiles/latest/v1
    """
    data = _get("/token-profiles/latest/v1")
    if not isinstance(data, list):
        return []
    return [t for t in data if t.get("chainId") == "solana"]


def get_token_pairs(token_address: str) -> list[dict]:
    """Pares de un token en Solana (individual)."""
    data = _get(f"/latest/dex/tokens/{token_address}")
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if p.get("chainId") == "solana"]


def get_tokens_batch(addresses: list[str]) -> dict[str, list[dict]]:
    """
    Obtiene pares de múltiples tokens en una sola llamada (hasta 30 por batch).
    Endpoint: /latest/dex/tokens/{addr1},{addr2},...
    Devuelve: {token_address: [pairs...]}
    Reduce N llamadas individuales a ceil(N/30) llamadas batch.
    """
    result: dict[str, list[dict]] = {}
    batch_size = 30
    for i in range(0, len(addresses), batch_size):
        chunk = addresses[i : i + batch_size]
        joined = ",".join(chunk)
        data = _get(f"/latest/dex/tokens/{joined}")
        if not isinstance(data, dict):
            continue
        pairs = data.get("pairs") or []
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            addr = (pair.get("baseToken") or {}).get("address", "")
            if addr:
                result.setdefault(addr, []).append(pair)
    return result


def get_pair_price(pair_address: str) -> float | None:
    """
    Precio actual de un par específico en USD.
    Usado para monitorear posiciones abiertas.
    """
    data = _get(f"/latest/dex/pairs/solana/{pair_address}")
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    try:
        return float(pairs[0].get("priceUsd") or 0)
    except (ValueError, TypeError):
        return None


def get_pair_full(pair_address: str) -> dict | None:
    """
    Datos completos del par: precio, priceChange (5m/1h/6h), txns (buys/sells), volumen.
    Usado para monitorear momentum en vivo de posiciones abiertas.
    """
    data = _get(f"/latest/dex/pairs/solana/{pair_address}")
    if not isinstance(data, dict):
        return None
    pairs = data.get("pairs") or []
    return pairs[0] if pairs else None


def get_best_pair(token_address: str) -> dict | None:
    """Par con mayor liquidez de un token."""
    pairs = get_token_pairs(token_address)
    if not pairs:
        return None
    return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))


# ── Versiones async (hot path — no bloquean el event loop) ────────────────

async def _async_get(path: str, params: dict | None = None) -> dict | list | None:
    """GET async con rate limiting y reintentos sin time.sleep (usa asyncio.sleep)."""
    global _async_last_request
    async with _async_lock:
        now = asyncio.get_event_loop().time()
        wait = 0.35 - (now - _async_last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _async_last_request = asyncio.get_event_loop().time()

    for attempt in range(3):
        try:
            r = await _async_client.get(f"{BASE}{path}", params=params)
            if r.status_code == 429:
                backoff = 5 * (attempt + 1)
                log.warning(f"DexScreener rate limit — esperando {backoff}s...")
                await asyncio.sleep(backoff)
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            log.debug(f"DexScreener HTTP {e.response.status_code} en {path}")
            return None
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                log.debug(f"DexScreener error {path}: {e}")
                return None
    return None


async def get_best_pair_async(token_address: str) -> dict | None:
    """Par con mayor liquidez — versión async para el hot path del executor."""
    data = await _async_get(f"/latest/dex/tokens/{token_address}")
    if not isinstance(data, dict):
        return None
    pairs = [p for p in (data.get("pairs") or []) if p.get("chainId") == "solana"]
    if not pairs:
        return None
    return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
