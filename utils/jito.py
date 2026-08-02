"""
Cliente de Jito Block Engine — propinas (tip) y envío de bundles/transacciones
para proteger las compras/ventas contra sándwich (MEV) y priorizar su inclusión
en bloque, en vez de mandarlas "a pelo" al RPC público.

Referencia: https://docs.jito.wtf/lowlatencytxnsend/
Formato de sendBundle/sendTransaction verificado en vivo contra
mainnet.block-engine.jito.wtf antes de integrarlo (2026-07-31).
"""

import base64
import os
import random
import time

import httpx
from solders.instruction import Instruction
from solders.pubkey import Pubkey
import solders.system_program as sp

from utils.logger import get_logger

log = get_logger("jito")

# Cuentas de propina (tip) oficiales de Jito Mainnet — snapshot verificado contra
# el propio Block Engine (getTipAccounts) el 2026-07-31. Cualquiera de las 8 es válida;
# Jito recomienda rotar entre ellas para repartir la carga.
JITO_TIP_ACCOUNTS: list[str] = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]

# Endpoints de Block Engine por región. Se intentan en orden con fallback automático
# (mismo patrón que _QUOTE_URLS/_SWAP_URLS en utils/jupiter.py).
JITO_BLOCK_ENGINE_URLS: list[str] = [
    "https://mainnet.block-engine.jito.wtf",
    "https://frankfurt.mainnet.block-engine.jito.wtf",
    "https://amsterdam.mainnet.block-engine.jito.wtf",
    "https://ny.mainnet.block-engine.jito.wtf",
    "https://tokyo.mainnet.block-engine.jito.wtf",
]

DEFAULT_TIP_LAMPORTS = int(os.getenv("JITO_TIP_LAMPORTS", "100000"))  # 0.0001 SOL

_async_http = httpx.AsyncClient(timeout=5)

# Caché en memoria de la lista de tip accounts "en vivo" — se refresca best-effort
# vía getTipAccounts; si falla, se usa el snapshot hardcodeado de arriba.
_tip_accounts_cache: list[str] = list(JITO_TIP_ACCOUNTS)
_tip_accounts_cache_ts: float = 0.0
_TIP_ACCOUNTS_TTL_S = 600  # 10 min


def _random_tip_account(accounts: list[str] | None = None) -> Pubkey:
    pool = accounts or _tip_accounts_cache or JITO_TIP_ACCOUNTS
    return Pubkey.from_string(random.choice(pool))


async def refresh_tip_accounts() -> list[str]:
    """
    Refresca la lista de tip accounts vía getTipAccounts (autoridad en vivo).
    Si falla, conserva el caché actual (que arranca con el snapshot hardcodeado).
    No bloquea el hot path: se llama en background, nunca en la ruta de compra/venta.
    """
    global _tip_accounts_cache, _tip_accounts_cache_ts
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTipAccounts", "params": []}
    for base_url in JITO_BLOCK_ENGINE_URLS:
        try:
            r = await _async_http.post(f"{base_url}/api/v1/bundles", json=payload)
            if r.status_code != 200:
                continue
            result = r.json().get("result")
            if isinstance(result, list) and result:
                _tip_accounts_cache = result
                _tip_accounts_cache_ts = time.time()
                log.debug(f"[jito] tip accounts refrescadas ({len(result)}) desde {base_url}")
                return _tip_accounts_cache
        except Exception as e:
            log.debug(f"[jito] getTipAccounts falló en {base_url}: {str(e)[:100]}")
            continue
    return _tip_accounts_cache


def get_jito_tip_instruction(payer: Pubkey, tip_lamports: int = DEFAULT_TIP_LAMPORTS) -> Instruction:
    """
    Construye la instrucción de transferencia de propina a una cuenta de tip de Jito.
    Es una transferencia SOL nativa (system_program.transfer) — sin cuentas exóticas,
    pensada para ir en su propia transacción liviana dentro de un bundle
    (ver send_jito_bundle) en vez de inyectarse a mano dentro de la tx del swap.
    """
    tip_account = _random_tip_account()
    return sp.transfer(sp.TransferParams(
        from_pubkey=payer,
        to_pubkey=tip_account,
        lamports=tip_lamports,
    ))


async def send_jito_transaction(signed_tx_bytes: bytes) -> str | None:
    """
    Envía una transacción firmada (bytes) directamente al Block Engine de Jito,
    vía su endpoint compatible con sendTransaction. Prueba cada región en orden.

    NOTA: una tx individual enviada sola NO garantiza prioridad — sin propina en
    el mismo bundle, Jito no tiene incentivo para incluirla antes que otras.
    Para compras/ventas usa send_jito_bundle() con [tx_swap, tx_tip].
    """
    tx_b64 = base64.b64encode(signed_tx_bytes).decode()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [tx_b64, {"encoding": "base64"}],
    }
    for base_url in JITO_BLOCK_ENGINE_URLS:
        try:
            r = await _async_http.post(f"{base_url}/api/v1/transactions", json=payload)
            if r.status_code != 200:
                log.debug(f"[jito] {base_url} HTTP {r.status_code}: {r.text[:150]}")
                continue
            data = r.json()
            sig = data.get("result")
            if sig:
                return sig
            log.debug(f"[jito] {base_url} error: {data.get('error')}")
        except httpx.TimeoutException:
            log.debug(f"[jito] timeout en {base_url}")
        except Exception as e:
            log.debug(f"[jito] error en {base_url}: {str(e)[:100]}")
    log.warning("[jito] Todos los Block Engines fallaron al enviar la transacción")
    return None


async def send_jito_bundle(signed_txs: list[bytes]) -> str | None:
    """
    Envía un bundle (hasta 5 tx, ejecutan atómico y en orden) al Block Engine
    de Jito vía sendBundle. Uso esperado: [tx_swap, tx_tip] — la propina va en
    una transacción separada y liviana en vez de inyectada dentro de la tx del
    swap, para no tener que reordenar a mano las cuentas de una tx ajena
    (Jupiter/PumpPortal pueden traer Address Lookup Tables).
    """
    if not signed_txs:
        return None
    txs_b64 = [base64.b64encode(t).decode() for t in signed_txs]
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendBundle",
        "params": [txs_b64],
    }
    for base_url in JITO_BLOCK_ENGINE_URLS:
        try:
            r = await _async_http.post(f"{base_url}/api/v1/bundles", json=payload)
            if r.status_code != 200:
                log.debug(f"[jito] bundle {base_url} HTTP {r.status_code}: {r.text[:150]}")
                continue
            data = r.json()
            bundle_id = data.get("result")
            if bundle_id:
                log.info(f"[jito] ✅ Bundle enviado — id: {bundle_id[:16]}...")
                return bundle_id
            log.debug(f"[jito] bundle {base_url} error: {data.get('error')}")
        except httpx.TimeoutException:
            log.debug(f"[jito] bundle timeout en {base_url}")
        except Exception as e:
            log.debug(f"[jito] bundle error en {base_url}: {str(e)[:100]}")
    log.warning("[jito] Todos los Block Engines fallaron al enviar el bundle")
    return None
