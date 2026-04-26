"""
Ejecuta el copy trade usando Jupiter API.
Modo proporcional: invierte el mismo % del capital que la wallet objetivo.
"""

import base64
import json
import os
import time
import httpx
from datetime import datetime
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.message import MessageV0, Message
from solana.rpc.api import Client
from solana.rpc.types import TokenAccountOpts, TxOpts

from config import (
    RPC_HTTP, WALLET_PUBKEY, WALLET_PRIVKEY, SLIPPAGE_BPS,
    PROPORTIONAL_MODE, MAX_TRADE_PCT, MIN_TRADE_SOL, MAX_OPEN_COPIES,
    STOP_LOSS_PCT, MIN_RESERVE_SOL, MAX_PRICE_IMPACT, SCALING_TIERS,
    TOKENS,
)
from utils.jupiter import get_quote, get_swap_transaction, calc_price_impact, out_amount
from utils.pumpfun import get_pump_buy_tx, get_pump_sell_tx
from utils.logger import get_logger
from copytrade import simulator

log    = get_logger("executor")
client = Client(RPC_HTTP)

os.makedirs("data", exist_ok=True)
COPYTRADES_FILE  = "data/copytrades.json"
DEAD_TOKENS_FILE = "data/dead_tokens.json"

SOL_MINT     = TOKENS["SOL"]
LAMPORTS_PER_SOL = 1_000_000_000

# Tracking en memoria de posiciones abiertas: {token_mint: {"symbol": str, "opened": float}}
_open_copies: dict[str, dict] = {}

# Balance inicial de SOL (lamports) — se registra en el primer trade en vivo
_initial_balance: int = 0

# Tokens confirmados como irrecuperables (rugged, sin liquidez) — persistido en disco
_dead_tokens: set[str] = set()


def _load_dead_tokens():
    global _dead_tokens
    if os.path.exists(DEAD_TOKENS_FILE):
        try:
            with open(DEAD_TOKENS_FILE) as f:
                _dead_tokens = set(json.load(f))
        except Exception:
            _dead_tokens = set()

def _save_dead_token(mint: str, symbol: str):
    _dead_tokens.add(mint)
    try:
        with open(DEAD_TOKENS_FILE, "w") as f:
            json.dump(list(_dead_tokens), f)
        log.warning(f"[DEAD] {symbol} ({mint[:8]}...) marcado como irrecuperable — se ignorará en próximos reinicios")
    except Exception:
        pass

def _active_positions_count() -> int:
    """Cuenta solo posiciones abiertas activas (excluye recuperadas pendientes de señal)."""
    return sum(1 for v in _open_copies.values() if not v.get("recovered"))

_load_dead_tokens()


def recover_open_positions():
    """
    Al arrancar, escanea la wallet para detectar tokens no-SOL que ya tengamos.
    Intenta venderlos inmediatamente — si las wallets ya salieron, no llegará señal natural.
    Tokens que fallan TODAS las rutas de venta se marcan como irrecuperables en disco
    y se ignorarán en futuros reinicios (evita bloquear slots indefinidamente).
    """
    if not WALLET_PUBKEY:
        return
    keypair = load_keypair()
    try:
        from solana.rpc.types import TokenAccountOpts
        opts = TokenAccountOpts(program_id=Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"))
        resp = client.get_token_accounts_by_owner_json_parsed(Pubkey.from_string(WALLET_PUBKEY), opts)
        DUST_THRESHOLD = 1_000_000
        recovered = 0
        for acc in resp.value:
            info      = acc.account.data.parsed["info"]
            mint      = info["mint"]
            tok       = info["tokenAmount"]
            raw_amt   = int(tok["amount"])
            ui_amt    = float(tok.get("uiAmount") or 0)
            if raw_amt < DUST_THRESHOLD:
                continue
            symbol = mint[:6]

            # Token ya marcado como irrecuperable en sesiones anteriores — ignorar
            if mint in _dead_tokens:
                log.debug(f"[RECOVER] {symbol} ya marcado como irrecuperable — ignorando")
                continue

            log.info(f"[RECOVER] Token encontrado: {symbol} ({mint[:8]}...) — {ui_amt:.4f} tokens — intentando vender...")

            # Intentar vender inmediatamente (wallets ya pudieron haber salido)
            sig = None
            if keypair:
                sig = _send_pumpfun_sell(mint, ui_amt, keypair, pool="pump")
                if not sig:
                    sig = _send_pumpfun_sell(mint, ui_amt, keypair, pool="pumpswap")
                if not sig:
                    sig = _send_swap(mint, SOL_MINT, raw_amt, keypair)

            if sig:
                log.info(f"[RECOVER] ✅ Vendido {symbol} al arrancar | TX: {sig[:20]}...")
            else:
                # Todas las rutas fallaron — muy probablemente token rugged/sin liquidez
                # Marcarlo como muerto para no volver a intentar en próximos reinicios
                _save_dead_token(mint, symbol)
                # Registrarlo en _open_copies como recuperado (sin contar en slots activos)
                # por si acaso llegara señal de venta natural
                _open_copies[mint] = {"symbol": symbol, "opened": time.time(), "recovered": True}
                log.warning(f"[RECOVER] ⚠️  No se pudo vender {symbol} — marcado como irrecuperable")
                recovered += 1

        if recovered:
            log.info(f"[RECOVER] {recovered} token(s) irrecuperables — NO bloquean slots de trading")
    except Exception as e:
        log.warning(f"[RECOVER] Error escaneando posiciones: {e}")


def _ensure_initial_balance():
    """Registra el balance inicial una sola vez, al primer trade en vivo."""
    global _initial_balance
    if _initial_balance == 0 and WALLET_PUBKEY:
        bal = get_our_sol_balance()
        if bal > 0:
            _initial_balance = bal
            log.info(
                f"[CAPITAL] Balance inicial registrado: "
                f"[bold white]{bal / LAMPORTS_PER_SOL:.4f} SOL[/] | "
                f"Stop-loss activo si cae bajo "
                f"[bold red]{bal * STOP_LOSS_PCT / LAMPORTS_PER_SOL:.4f} SOL[/] "
                f"({STOP_LOSS_PCT*100:.0f}%)"
            )


def _is_stop_loss_triggered(current_balance: int) -> bool:
    """Retorna True si el balance cayó por debajo del umbral de stop-loss."""
    if _initial_balance == 0:
        return False
    threshold = int(_initial_balance * STOP_LOSS_PCT)
    if current_balance < threshold:
        log.warning(
            f"[bold red][STOP-LOSS ACTIVO][/] Balance "
            f"{current_balance / LAMPORTS_PER_SOL:.4f} SOL < "
            f"umbral {threshold / LAMPORTS_PER_SOL:.4f} SOL — "
            f"trading pausado para proteger capital"
        )
        return True
    return False


def _get_dynamic_trade_pct(current_balance: int) -> float:
    """
    Retorna el % máximo por trade según la ganancia acumulada sobre el capital inicial.
    Cuanto más ganás, el techo por trade sube — pero siempre usando las ganancias,
    no el capital base de $20.
    """
    if _initial_balance == 0:
        return MAX_TRADE_PCT
    profit_pct = (current_balance - _initial_balance) / _initial_balance
    # Recorrer tiers de mayor a menor y devolver el primero que aplique
    for min_profit, trade_pct in reversed(SCALING_TIERS):
        if profit_pct >= min_profit:
            if trade_pct > MAX_TRADE_PCT:
                log.info(
                    f"[ESCALADO] Ganancia acumulada: [green]{profit_pct*100:+.1f}%[/] → "
                    f"techo por trade: [bold white]{trade_pct*100:.0f}%[/] "
                    f"(base era {MAX_TRADE_PCT*100:.0f}%)"
                )
            return trade_pct
    return MAX_TRADE_PCT


# ── Persistencia ─────────────────────────────────────────────────────────────

def _append_copytrade(entry: dict):
    data = []
    if os.path.exists(COPYTRADES_FILE):
        try:
            with open(COPYTRADES_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data.append(entry)
    with open(COPYTRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Wallet / balance ──────────────────────────────────────────────────────────

def load_keypair() -> Keypair | None:
    if not WALLET_PRIVKEY:
        return None
    try:
        import base58
        return Keypair.from_bytes(base58.b58decode(WALLET_PRIVKEY))
    except Exception as e:
        log.error(f"Error cargando keypair: {e}")
        return None


def get_our_sol_balance() -> int:
    """Retorna nuestro balance de SOL en lamports, o 0 si falla."""
    if not WALLET_PUBKEY:
        return 0
    try:
        resp = client.get_balance(Pubkey.from_string(WALLET_PUBKEY))
        return resp.value
    except Exception as e:
        log.error(f"Error obteniendo balance SOL: {e}")
        return 0


def get_our_token_balance(mint: str) -> tuple[int, float]:
    """
    Retorna (raw_amount, ui_amount) de un token en nuestra wallet.
    raw_amount: unidades mínimas (para Jupiter)
    ui_amount:  amount con decimales aplicados (para PumpPortal)
    Retorna (0, 0.0) si no tenemos el token.
    """
    if not WALLET_PUBKEY:
        return 0, 0.0
    try:
        opts = TokenAccountOpts(mint=Pubkey.from_string(mint))
        resp = client.get_token_accounts_by_owner_json_parsed(Pubkey.from_string(WALLET_PUBKEY), opts)
        accounts = resp.value
        if not accounts:
            return 0, 0.0
        raw_total = 0
        ui_total  = 0.0
        for acc in accounts:
            info = acc.account.data.parsed["info"]["tokenAmount"]
            raw_total += int(info["amount"])
            ui_total  += float(info.get("uiAmount") or 0)
        return raw_total, ui_total
    except Exception as e:
        log.error(f"Error obteniendo balance token {mint[:8]}...: {e}")
        return 0, 0.0


# ── Cálculo de monto proporcional ────────────────────────────────────────────

def calc_proportional_amount(swap: dict, our_balance_lamports: int) -> int | None:
    """
    Calcula cuántos lamports de SOL invertir usando % fijo del balance propio.
    El % sube progresivamente con las ganancias acumuladas (SCALING_TIERS).
    No usa el capital de la wallet copiada para evitar trades microscópicos.
    """
    dynamic_pct = _get_dynamic_trade_pct(our_balance_lamports)
    our_amount  = int(our_balance_lamports * dynamic_pct)

    min_lamports = int(MIN_TRADE_SOL * LAMPORTS_PER_SOL)
    if our_amount < min_lamports:
        log.warning(
            f"Monto calculado ({our_amount / LAMPORTS_PER_SOL:.6f} SOL) "
            f"por debajo del mínimo ({MIN_TRADE_SOL} SOL) — ignorando"
        )
        return None

    return our_amount


# ── Execute ───────────────────────────────────────────────────────────────────

def execute_copy(swap: dict) -> bool:
    """
    Ejecuta un copy del swap detectado en modo proporcional.

    - COMPRA (SOL→token): proporcional al % que metió la wallet.
    - VENTA  (token→SOL): vende todo el balance que tengamos de ese token.
    """
    keypair = load_keypair()
    label   = swap.get("wallet_label", f"{swap['wallet'][:8]}...")
    is_buy  = swap["token_in"] == SOL_MINT
    is_sell = swap["token_out"] == SOL_MINT

    # ── Modo simulación ──────────────────────────────────────────────────────
    if not keypair:
        _simulate(swap, label, is_buy)
        return True

    if not WALLET_PUBKEY:
        log.warning("WALLET_PUBKEY no configurado.")
        return False

    # ── Compra ───────────────────────────────────────────────────────────────
    if is_buy:
        token_out = swap["token_out"]

        # No abrir más posiciones si ya estamos al límite (tokens recuperados no cuentan)
        active_count = _active_positions_count()
        if active_count >= MAX_OPEN_COPIES:
            log.warning(
                f"[{label}] Límite de {MAX_OPEN_COPIES} posiciones activas alcanzado — "
                f"ignorando compra de {swap['symbol_out']}"
            )
            return False

        # No comprar el mismo token dos veces
        if token_out in _open_copies:
            log.warning(f"[{label}] Ya tenemos {swap['symbol_out']} abierto — ignorando entrada adicional")
            return False

        our_balance = get_our_sol_balance()
        if our_balance == 0:
            log.error("No se pudo obtener balance SOL propio.")
            return False

        # Registrar capital inicial (sólo la primera vez)
        _ensure_initial_balance()

        # Stop-loss global: parar si perdimos demasiado
        if _is_stop_loss_triggered(our_balance):
            return False

        amount_lamports = calc_proportional_amount(swap, our_balance)
        if amount_lamports is None:
            return False

        # Reserva mínima: nunca dejar el balance por debajo de MIN_RESERVE_SOL
        min_reserve_lamports = int(MIN_RESERVE_SOL * LAMPORTS_PER_SOL)
        available_lamports = our_balance - min_reserve_lamports
        if available_lamports <= 0:
            log.warning(
                f"[{label}] Balance ({our_balance / LAMPORTS_PER_SOL:.4f} SOL) "
                f"no supera la reserva mínima ({MIN_RESERVE_SOL} SOL) — ignorando"
            )
            return False
        if amount_lamports > available_lamports:
            log.info(
                f"[{label}] Monto reducido de {amount_lamports / LAMPORTS_PER_SOL:.4f} "
                f"a {available_lamports / LAMPORTS_PER_SOL:.4f} SOL para respetar reserva mínima"
            )
            amount_lamports = available_lamports

        proportion_pct = amount_lamports / our_balance * 100
        log.info(
            f"[COPY BUY] [bold cyan]{label}[/] | {swap['symbol_out']} | "
            f"Proporción: {proportion_pct:.1f}% | "
            f"Monto: {amount_lamports / LAMPORTS_PER_SOL:.4f} SOL | "
            f"Balance: {our_balance / LAMPORTS_PER_SOL:.3f} SOL"
        )

        is_pumpfun_bc  = swap.get("program") == "Pump.fun"
        is_pumpswap    = swap.get("program") == "PumpSwap"
        sig = None
        if is_pumpfun_bc:
            # Bonding curve: PumpPortal directo
            log.info(f"[{label}] Bonding curve — usando PumpPortal (pump) para {swap['symbol_out']}")
            sig = _send_pumpfun_buy(token_out, amount_lamports, keypair)
        elif is_pumpswap:
            # PumpSwap AMM: Jupiter primero, luego PumpPortal pumpswap como fallback
            sig = _send_swap(swap["token_in"], token_out, amount_lamports, keypair)
            if not sig:
                log.info(f"[{label}] Jupiter falló — intentando PumpPortal (pumpswap) para {swap['symbol_out']}")
                sig = _send_pumpfun_buy_pumpswap(token_out, amount_lamports, keypair)
        else:
            # Jupiter/Raydium/Orca
            sig = _send_swap(swap["token_in"], token_out, amount_lamports, keypair)

        if not sig:
            log.warning(f"[{label}] No se pudo ejecutar buy — {swap['symbol_out']} (programa: {swap.get('program','')})")
            return False

        _open_copies[token_out] = {"symbol": swap["symbol_out"], "opened": time.time(), "program": swap.get("program", "")}
        _append_copytrade({
            "timestamp":    time.time(),
            "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":         "buy",
            "wallet":       swap["wallet"],
            "wallet_label": label,
            "program":      swap["program"],
            "symbol_in":    swap["symbol_in"],
            "symbol_out":   swap["symbol_out"],
            "token_in":     swap["token_in"],
            "token_out":    token_out,
            "amount_sol":   amount_lamports / LAMPORTS_PER_SOL,
            "proportion_pct": round(proportion_pct, 2),
            "tx_sig":       sig,
            "simulated":    False,
        })
        log.info(f"[bold green]COPY BUY OK[/] — {swap['symbol_out']} | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    # ── Venta ────────────────────────────────────────────────────────────────
    elif is_sell:
        token_in = swap["token_in"]

        # Solo vendemos si tenemos ese token en posición abierta
        if token_in not in _open_copies:
            log.debug(f"[{label}] Venta de {swap['symbol_in']} ignorada — no tenemos posición abierta")
            return False

        raw_balance, ui_balance = get_our_token_balance(token_in)
        if raw_balance == 0:
            # El nodo RPC puede tardar varios segundos en reflejar una cuenta recién creada.
            # Reintentar hasta 5 veces con 3s de pausa si la posición lleva < 60s abierta.
            opened_at = _open_copies[token_in].get("opened", 0)
            if time.time() - opened_at < 60:
                for _attempt in range(5):
                    time.sleep(3)
                    raw_balance, ui_balance = get_our_token_balance(token_in)
                    if raw_balance > 0:
                        log.info(f"[{label}] Balance visible tras {(_attempt+1)*3}s de espera — {ui_balance:.4f} tokens")
                        break
        if raw_balance == 0:
            log.warning(f"[{label}] Venta de {swap['symbol_in']} — balance propio es 0, nada que vender")
            _open_copies.pop(token_in, None)
            return False

        log.info(
            f"[COPY SELL] [bold cyan]{label}[/] | {swap['symbol_in']}→SOL | "
            f"Vendiendo todo: {ui_balance:.4f} tokens ({raw_balance} raw)"
        )

        # Usar el programa con el que NOSOTROS compramos (no el del target al vender)
        # para decidir la ruta de venta más adecuada.
        buy_program   = _open_copies[token_in].get("program", swap.get("program", ""))
        is_pumpfun_bc = buy_program == "Pump.fun"
        sig = None
        if is_pumpfun_bc:
            # Token de Pump.fun: probar BC → PumpSwap AMM → Jupiter
            log.info(f"[{label}] Intentando PumpPortal (pump) — {ui_balance:.4f} tokens")
            sig = _send_pumpfun_sell(token_in, ui_balance, keypair, pool="pump")
            if not sig:
                log.info(f"[{label}] Intentando PumpPortal (pumpswap) — token graduado?")
                sig = _send_pumpfun_sell(token_in, ui_balance, keypair, pool="pumpswap")
            if not sig:
                log.info(f"[{label}] Intentando Jupiter como último recurso...")
                sig = _send_swap(token_in, SOL_MINT, raw_balance, keypair)
        else:
            # Token de Jupiter/Raydium/Orca: Jupiter → PumpSwap como fallback
            sig = _send_swap(token_in, SOL_MINT, raw_balance, keypair)
            if not sig:
                log.info(f"[{label}] Jupiter falló — intentando PumpPortal (pumpswap)...")
                sig = _send_pumpfun_sell(token_in, ui_balance, keypair, pool="pumpswap")

        if not sig:
            log.warning(f"[{label}] Sell falló (pump + pumpswap + Jupiter) — {swap['symbol_in']} — posición queda abierta")
            return False

        pos = _open_copies.pop(token_in, {})
        hold_min = (time.time() - pos.get("opened", time.time())) / 60
        _append_copytrade({
            "timestamp":    time.time(),
            "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
            "type":         "sell",
            "wallet":       swap["wallet"],
            "wallet_label": label,
            "program":      swap["program"],
            "symbol_in":    swap["symbol_in"],
            "symbol_out":   swap["symbol_out"],
            "token_in":     token_in,
            "token_out":    SOL_MINT,
            "hold_min":     round(hold_min, 1),
            "tx_sig":       sig,
            "simulated":    False,
        })
        log.info(f"[bold green]COPY SELL OK[/] — {swap['symbol_in']} | Hold: {hold_min:.1f} min | TX: {sig}")
        # Alimentar el simulador en vivo para acumular datos de aprendizaje
        simulator.process(swap)
        return True

    else:
        # Token→token: ignorar, demasiado complejo de proporcionar sin precio
        log.debug(f"[{label}] Swap token→token ignorado ({swap['symbol_in']}→{swap['symbol_out']})")
        return False


# ── Enviar swap via Jupiter ───────────────────────────────────────────────────

def _send_swap(token_in: str, token_out: str, amount: int, keypair: Keypair) -> str | None:
    """Pide quote a Jupiter, firma y envía. Retorna la signature o None."""
    quote = get_quote(token_in, token_out, amount)
    if not quote:
        log.error("No se pudo obtener quote de Jupiter.")
        return None

    impact = calc_price_impact(quote)
    if impact > MAX_PRICE_IMPACT:
        log.warning(f"Price impact muy alto ({impact:.2f}% > {MAX_PRICE_IMPACT}%) — abortando.")
        return None

    swap_tx_b64 = get_swap_transaction(quote, WALLET_PUBKEY)
    if not swap_tx_b64:
        log.error("No se pudo obtener swap transaction de Jupiter.")
        return None

    try:
        raw_bytes = base64.b64decode(swap_tx_b64)
        tx        = VersionedTransaction.from_bytes(raw_bytes)
        tx_signed = VersionedTransaction(tx.message, [keypair])
        resp      = client.send_raw_transaction(
            bytes(tx_signed),
            opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
        )
        sig  = str(resp.value)
        conf = client.confirm_transaction(resp.value, commitment="confirmed")
        return sig if conf.value else None
    except Exception as e:
        log.error(f"Error enviando TX Jupiter: {e}")
        return None


# ── Enviar swap via PumpPortal (bonding curve) ────────────────────────────────

def _send_pumpfun_buy(mint: str, amount_lamports: int, keypair: Keypair) -> str | None:
    """Compra via PumpPortal en la bonding curve. Retorna signature o None."""
    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    tx_bytes = get_pump_buy_tx(WALLET_PUBKEY, mint, amount_sol)
    if not tx_bytes:
        return None
    return _sign_and_send(tx_bytes, keypair, f"PumpPortal buy {mint[:8]}...")


def _send_pumpfun_buy_pumpswap(mint: str, amount_lamports: int, keypair: Keypair) -> str | None:
    """Compra via PumpPortal en PumpSwap AMM (token graduado). Retorna signature o None."""
    from utils.pumpfun import get_pump_buy_tx as _buy_tx
    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    payload = {
        "publicKey":        WALLET_PUBKEY,
        "action":           "buy",
        "mint":             mint,
        "denominatedInSol": "true",
        "amount":           round(amount_sol, 6),
        "slippage":         20,
        "priorityFee":      0.0005,
        "pool":             "pumpswap",
    }
    import httpx
    try:
        r = httpx.post("https://pumpportal.fun/api/trade-local", json=payload, timeout=15)
        if r.status_code != 200 or not r.content:
            log.warning(f"PumpPortal buy pumpswap HTTP {r.status_code}: {r.text[:150]}")
            return None
        return _sign_and_send(r.content, keypair, f"PumpPortal buy [pumpswap] {mint[:8]}...")
    except Exception as e:
        log.warning(f"PumpPortal buy pumpswap error: {e}")
        return None


def _send_pumpfun_sell(mint: str, ui_amount: float, keypair: Keypair, pool: str = "pump") -> str | None:
    """Vende via PumpPortal. ui_amount en tokens con decimales (ej: 1234.56, NO raw units)."""
    tx_bytes = get_pump_sell_tx(WALLET_PUBKEY, mint, ui_amount, pool=pool)
    if not tx_bytes:
        return None
    return _sign_and_send(tx_bytes, keypair, f"PumpPortal sell [{pool}] {mint[:8]}...")


def _sign_and_send(tx_bytes: bytes, keypair: Keypair, desc: str) -> str | None:
    """Deserializa, reemplaza blockhash, firma y envía. Retorna signature o None."""
    try:
        tx = VersionedTransaction.from_bytes(tx_bytes)

        # Obtener blockhash fresco — el de PumpPortal puede haber expirado (~60-90s de vida)
        bh_resp    = client.get_latest_blockhash(commitment="confirmed")
        fresh_bh   = bh_resp.value.blockhash

        # Reconstruir mensaje con blockhash fresco (soporta V0 y legacy)
        msg = tx.message
        if isinstance(msg, MessageV0):
            new_msg = MessageV0(
                header=msg.header,
                account_keys=list(msg.account_keys),
                recent_blockhash=fresh_bh,
                instructions=list(msg.instructions),
                address_table_lookups=list(msg.address_table_lookups),
            )
        else:
            new_msg = Message.new_with_blockhash(
                msg.instructions,
                keypair.pubkey(),
                fresh_bh,
            )

        tx_signed = VersionedTransaction(new_msg, [keypair])
        resp = client.send_raw_transaction(
            bytes(tx_signed),
            opts=TxOpts(skip_preflight=False, preflight_commitment="confirmed"),
        )
        sig  = str(resp.value)
        conf = client.confirm_transaction(resp.value, commitment="confirmed")
        if conf.value:
            return sig
        log.warning(f"[{desc}] TX enviada pero no confirmada: {sig[:16]}...")
        return None
    except Exception as e:
        log.error(f"[{desc}] Error firmando/enviando TX: {e}")
        return None


# ── Simulación ────────────────────────────────────────────────────────────────

def _simulate(swap: dict, label: str, is_buy: bool):
    """Registra el swap en modo simulación y calcula la proporción teórica."""
    wallet_pre_sol = swap.get("wallet_pre_sol", 0)

    if is_buy and wallet_pre_sol > 0:
        proportion = min(swap["amount_in"] / wallet_pre_sol, MAX_TRADE_PCT)
        prop_str   = f"{proportion * 100:.1f}%"
    else:
        proportion = None
        prop_str   = "—"

    entry = {
        "timestamp":    time.time(),
        "time_str":     datetime.now().strftime("%H:%M:%S %d/%m"),
        "type":         "buy" if is_buy else ("sell" if swap["token_out"] == SOL_MINT else "token-token"),
        "wallet":       swap["wallet"],
        "wallet_label": label,
        "program":      swap["program"],
        "symbol_in":    swap["symbol_in"],
        "symbol_out":   swap["symbol_out"],
        "token_in":     swap["token_in"],
        "token_out":    swap["token_out"],
        "amount_in":    swap["amount_in"],
        "proportion":   prop_str,
        "simulated":    True,
    }
    _append_copytrade(entry)
    log.info(
        f"[SIM] [bold cyan]{label}[/] | "
        f"[yellow]{swap['symbol_in']}[/] → [green]{swap['symbol_out']}[/] "
        f"via [white]{swap['program']}[/] | "
        f"Proporción: [white]{prop_str}[/]"
    )
    simulator.process(swap)
