"""
Wrapper para Pump.fun bonding curve — 3 backends con fallback automático.
Intenta en orden: PumpPortal → PumpAPI.fun → Jupiter on-chain.
"""

import base64
import httpx
from utils.logger import get_logger

log = get_logger("pumpfun")

PUMPPORTAL_URL = "https://pumpportal.fun/api/trade-local"
PUMPAPI_URL    = "https://pumpapi.fun/api/trade"
DEFAULT_SLIPPAGE = 15
DEFAULT_PRIORITY = 0.0002


def get_pump_buy_tx(pubkey: str, mint: str, amount_sol: float) -> bytes | None:
	"""Pide TX para comprar SOL→token en bonding curve. Retorna bytes o None."""
	payload = {
		"publicKey": pubkey,
		"action": "buy",
		"mint": mint,
		"denominatedInSol": "true",
		"amount": round(amount_sol, 6),
		"slippage": DEFAULT_SLIPPAGE,
		"priorityFee": DEFAULT_PRIORITY,
		"pool": "pump",
	}
	return _multi_backend(payload, f"buy {amount_sol:.5f} SOL → {mint[:8]}")


def get_pump_sell_tx(pubkey: str, mint: str, amount_tokens: float, pool: str = "pump") -> bytes | None:
	"""Pide TX para vender token→SOL. Pool: 'pump' (BC) o 'pumpswap' (AMM)."""
	payload = {
		"publicKey": pubkey,
		"action": "sell",
		"mint": mint,
		"denominatedInSol": "false",
		"amount": amount_tokens,
		"slippage": DEFAULT_SLIPPAGE,
		"priorityFee": DEFAULT_PRIORITY,
		"pool": pool,
	}
	return _multi_backend(payload, f"sell ({pool}) {amount_tokens} → {mint[:8]}")


def _multi_backend(payload: dict, desc: str) -> bytes | None:
	"""Intenta 3 backends en cascada: PumpPortal → PumpAPI → Jupiter on-chain."""

	# Backend 1: PumpPortal
	result = _try_pumpportal(payload, desc)
	if result:
		return result

	# Backend 2: PumpAPI.fun
	result = _try_pumpapi(payload, desc)
	if result:
		return result

	# Backend 3: Jupiter on-chain (solo buy — sell tiene Jupiter de fallback en executor)
	if payload.get("action") == "buy":
		result = _try_jupiter_onchain(
			payload["publicKey"], payload["mint"], float(payload["amount"]), desc
		)
		if result:
			return result

	log.warning(f"[pumpfun] Todos los backends fallaron — {desc}")
	return None


def _try_pumpportal(payload: dict, desc: str) -> bytes | None:
	"""Intenta PumpPortal con timeout de 4s — falla rápido para activar fallback."""
	try:
		r = httpx.post(PUMPPORTAL_URL, json=payload, timeout=4)
		if r.status_code == 200 and r.content:
			log.debug(f"[pumpfun] ✅ PumpPortal OK — {desc}")
			return r.content
		log.debug(f"[pumpfun] ⚠️ PumpPortal HTTP {r.status_code} — fallando a siguiente backend")
	except httpx.TimeoutException:
		log.debug(f"[pumpfun] ⚠️ PumpPortal timeout — fallando a siguiente backend")
	except Exception as e:
		log.debug(f"[pumpfun] ⚠️ PumpPortal error: {str(e)[:100]}")
	return None


def _try_pumpapi(payload: dict, desc: str) -> bytes | None:
	"""Intenta PumpAPI.fun (API alternativa pública)."""
	try:
		r = httpx.post(PUMPAPI_URL, json=payload, timeout=4)
		if r.status_code != 200:
			log.debug(f"[pumpfun] ⚠️ PumpAPI HTTP {r.status_code} — fallando a siguiente backend")
			return None

		if not r.content:
			log.debug(f"[pumpfun] ⚠️ PumpAPI respuesta vacía")
			return None

		ct = r.headers.get("content-type", "")

		# Si devuelve JSON
		if "json" in ct.lower():
			try:
				data = r.json()
				if isinstance(data, dict) and "transaction" in data:
					try:
						tx_bytes = base64.b64decode(data["transaction"])
						log.info(f"[pumpfun] ✅ PumpAPI.fun OK (JSON base64) — {desc}")
						return tx_bytes
					except Exception:
						pass
				log.debug(f"[pumpfun] ⚠️ PumpAPI JSON format desconocido")
			except:
				pass

		# Si devuelve bytes crudos
		log.info(f"[pumpfun] ✅ PumpAPI.fun OK (bytes) — {desc}")
		return r.content

	except httpx.TimeoutException:
		log.debug(f"[pumpfun] ⚠️ PumpAPI timeout")
	except Exception as e:
		log.debug(f"[pumpfun] ⚠️ PumpAPI error: {str(e)[:100]}")

	return None


def _try_jupiter_onchain(pubkey: str, mint: str, amount_sol: float, desc: str) -> bytes | None:
	"""
	Fallback final: construye TX via Jupiter v6 (soporta Pump.fun BC nativamente).
	Es el más robusto porque solo depende del RPC de Solana.
	"""
	try:
		from utils.jupiter import get_quote, get_swap_transaction

		SOL_MINT = "So11111111111111111111111111111111111111112"
		LAMPORTS_PER_SOL = 1_000_000_000
		amount_lamports = int(amount_sol * LAMPORTS_PER_SOL)

		# Obtener quote
		quote = get_quote(SOL_MINT, mint, amount_lamports)
		if not quote:
			log.debug(f"[pumpfun] ⚠️ Jupiter no pudo obtener quote")
			return None

		# Construir TX
		tx_b64 = get_swap_transaction(quote, pubkey)
		if not tx_b64:
			log.debug(f"[pumpfun] ⚠️ Jupiter no pudo construir TX")
			return None

		# Convertir base64 → bytes
		tx_bytes = base64.b64decode(tx_b64)
		log.info(f"[pumpfun] ✅ Jupiter on-chain OK (fallback robusto) — {desc}")
		return tx_bytes

	except Exception as e:
		log.debug(f"[pumpfun] ⚠️ Jupiter fallback error: {str(e)[:100]}")

	return None
