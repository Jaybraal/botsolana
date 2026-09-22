from solders.compute_budget import set_compute_unit_limit
from solders.hash import Hash
from solders.instruction import CompiledInstruction
from solders.keypair import Keypair
from solders.message import MessageHeader, MessageV0
from solders.transaction import VersionedTransaction

import copytrade.executor as executor
import config


# ── _check_stop_loss (función pura) ─────────────────────────────────────────

def test_stop_loss_no_dispara_con_ganancia():
    assert executor._check_stop_loss(entry_price=1.0, current_price=1.5, threshold_pct=15.0) is False


def test_stop_loss_no_dispara_justo_debajo_del_umbral():
    # -14% no llega al umbral de -15%
    assert executor._check_stop_loss(entry_price=1.0, current_price=0.86, threshold_pct=15.0) is False


def test_stop_loss_dispara_al_llegar_al_umbral():
    # exactamente -15%
    assert executor._check_stop_loss(entry_price=1.0, current_price=0.85, threshold_pct=15.0) is True


def test_stop_loss_dispara_con_caida_mayor():
    assert executor._check_stop_loss(entry_price=1.0, current_price=0.5, threshold_pct=15.0) is True


def test_stop_loss_ignora_precios_invalidos():
    assert executor._check_stop_loss(entry_price=0, current_price=0.5, threshold_pct=15.0) is False
    assert executor._check_stop_loss(entry_price=1.0, current_price=0, threshold_pct=15.0) is False


def test_risk_cap_no_permite_superar_dos_por_ciento(monkeypatch):
    """Una variable de entorno antigua no debe reabrir el riesgo de 5-25%."""
    monkeypatch.setenv("MAX_TRADE_PCT", "0.25")
    assert config.get_max_trade_pct_by_balance(100.0) == 0.02


async def _no_pair(_token: str):
    return None


async def _no_quote(*_args):
    return None


def test_pretrade_falla_cerrado_si_no_hay_liquidez_verificable(monkeypatch):
    monkeypatch.setattr(executor, "get_best_pair_async", _no_pair)
    passed, pair, liquidity = __import__("asyncio").run(executor._pre_trade_checks("mint"))
    assert (passed, pair, liquidity) == (False, None, 0.0)


def test_pretrade_falla_cerrado_si_no_hay_ruta_de_salida(monkeypatch):
    monkeypatch.setattr(executor, "get_quote_async", _no_quote)
    passed, impact = __import__("asyncio").run(executor._check_price_impact("SOL", "mint", 1))
    assert (passed, impact) == (False, None)


# ── _prepend_compute_budget ──────────────────────────────────────────────────

def _fake_message_v0() -> MessageV0:
    payer = Keypair().pubkey()
    other_program = Keypair().pubkey()
    # [payer(signer,writable), other_program(nonsigner,readonly)]
    account_keys = [payer, other_program]
    header = MessageHeader(num_required_signatures=1, num_readonly_signed_accounts=0, num_readonly_unsigned_accounts=1)
    existing_ix = CompiledInstruction(program_id_index=1, accounts=bytes([0]), data=bytes([9, 9, 9]))
    return MessageV0(
        header=header,
        account_keys=account_keys,
        recent_blockhash=Hash.default(),
        instructions=[existing_ix],
        address_table_lookups=[],
    )


def test_prepend_compute_budget_agrega_dos_instrucciones_al_frente():
    msg = _fake_message_v0()
    new_msg = executor._prepend_compute_budget(msg)

    assert len(new_msg.instructions) == len(msg.instructions) + 2
    assert str(new_msg.instructions[0].program_id_index) == str(len(new_msg.account_keys) - 1)
    assert bytes(new_msg.instructions[0].accounts) == b""
    assert bytes(new_msg.instructions[1].accounts) == b""


def test_prepend_compute_budget_no_altera_instrucciones_existentes():
    msg = _fake_message_v0()
    new_msg = executor._prepend_compute_budget(msg)

    original = new_msg.instructions[-1]
    assert original.program_id_index == 1
    assert list(original.accounts) == [0]
    assert bytes(original.data) == bytes([9, 9, 9])


def test_prepend_compute_budget_actualiza_header_readonly_unsigned():
    msg = _fake_message_v0()
    new_msg = executor._prepend_compute_budget(msg)

    # se anadio 1 cuenta nueva (ComputeBudget program), readonly-no-signer
    assert new_msg.header.num_readonly_unsigned_accounts == msg.header.num_readonly_unsigned_accounts + 1
    assert new_msg.header.num_required_signatures == msg.header.num_required_signatures
    assert len(new_msg.account_keys) == len(msg.account_keys) + 1


def test_prepend_compute_budget_tx_firma_y_reparsea_ok():
    msg = _fake_message_v0()
    new_msg = executor._prepend_compute_budget(msg)
    payer_kp = Keypair()  # no coincide con la pubkey del mensaje, pero solders no valida eso al firmar bytes

    # Reconstruimos con un payer real para poder firmar de verdad
    new_msg2 = MessageV0(
        header=new_msg.header,
        account_keys=[payer_kp.pubkey()] + list(new_msg.account_keys)[1:],
        recent_blockhash=new_msg.recent_blockhash,
        instructions=new_msg.instructions,
        address_table_lookups=new_msg.address_table_lookups,
    )
    tx = VersionedTransaction(new_msg2, [payer_kp])
    raw = bytes(tx)
    tx2 = VersionedTransaction.from_bytes(raw)
    assert len(tx2.message.instructions) == len(msg.instructions) + 2


def test_prepend_compute_budget_no_duplica_programa_si_ya_esta_presente():
    msg = _fake_message_v0()
    # Simular que ComputeBudget ya está en account_keys (caso raro pero posible)
    account_keys = list(msg.account_keys) + [executor.COMPUTE_BUDGET_PROGRAM_ID]
    header = MessageHeader(
        num_required_signatures=1,
        num_readonly_signed_accounts=0,
        num_readonly_unsigned_accounts=2,
    )
    msg2 = MessageV0(
        header=header,
        account_keys=account_keys,
        recent_blockhash=msg.recent_blockhash,
        instructions=list(msg.instructions),
        address_table_lookups=[],
    )
    new_msg = executor._prepend_compute_budget(msg2)

    # no debe haber agregado una segunda copia del programa
    assert list(new_msg.account_keys).count(executor.COMPUTE_BUDGET_PROGRAM_ID) == 1
    assert new_msg.header.num_readonly_unsigned_accounts == header.num_readonly_unsigned_accounts


# ── _build_signed_tip_tx ─────────────────────────────────────────────────────

def test_build_signed_tip_tx_firma_correctamente():
    kp = Keypair()
    raw = executor._build_signed_tip_tx(kp, Hash.default())

    tx = VersionedTransaction.from_bytes(raw)
    assert len(tx.message.instructions) == 1
    assert len(tx.message.account_keys) == 3
    assert tx.message.account_keys[0] == kp.pubkey()
    assert str(tx.message.account_keys[2]) == "11111111111111111111111111111111"  # System Program
