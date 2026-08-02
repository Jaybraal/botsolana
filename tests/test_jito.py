import asyncio
import base64

from solders.keypair import Keypair
from solders.pubkey import Pubkey

import utils.jito as jito


def test_tip_accounts_son_pubkeys_validas():
    assert len(jito.JITO_TIP_ACCOUNTS) == 8
    for addr in jito.JITO_TIP_ACCOUNTS:
        Pubkey.from_string(addr)  # no debe lanzar


def test_get_jito_tip_instruction_transfiere_a_una_tip_account():
    payer = Keypair().pubkey()
    ix = jito.get_jito_tip_instruction(payer, tip_lamports=123456)

    assert str(ix.program_id) == "11111111111111111111111111111111"  # System Program
    accounts = ix.accounts
    assert accounts[0].pubkey == payer
    assert accounts[0].is_signer is True
    assert accounts[0].is_writable is True
    assert str(accounts[1].pubkey) in jito.JITO_TIP_ACCOUNTS
    assert accounts[1].is_writable is True


def test_get_jito_tip_instruction_usa_default_lamports():
    payer = Keypair().pubkey()
    ix = jito.get_jito_tip_instruction(payer)
    # data de system_program::transfer: [4 bytes discriminante][8 bytes lamports LE]
    lamports = int.from_bytes(bytes(ix.data)[4:12], "little")
    assert lamports == jito.DEFAULT_TIP_LAMPORTS


def test_send_jito_transaction_reintenta_regiones_y_falla_limpio(monkeypatch):
    calls = []

    class _FakeResp:
        status_code = 500
        text = "boom"

    class _FakeClient:
        async def post(self, url, json=None):
            calls.append(url)
            return _FakeResp()

    monkeypatch.setattr(jito, "_async_http", _FakeClient())

    result = asyncio.run(jito.send_jito_transaction(b"\x00\x01"))

    assert result is None
    assert len(calls) == len(jito.JITO_BLOCK_ENGINE_URLS)


def test_send_jito_bundle_devuelve_id_si_alguna_region_responde(monkeypatch):
    calls = []

    class _FakeResp:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

    class _FakeClient:
        async def post(self, url, json=None):
            calls.append((url, json))
            if "frankfurt" in url:
                return _FakeResp(200, {"jsonrpc": "2.0", "result": "bundle-id-123", "id": 1})
            return _FakeResp(500, {})

    monkeypatch.setattr(jito, "_async_http", _FakeClient())

    result = asyncio.run(jito.send_jito_bundle([b"tx1", b"tx2"]))

    assert result == "bundle-id-123"
    # el primer intento (mainnet) fue antes que frankfurt, y sí se llamó
    assert calls[0][0].startswith("https://mainnet")
    # el payload manda la lista de txs en base64 como PRIMER param
    sent_payload = calls[0][1]
    assert sent_payload["method"] == "sendBundle"
    decoded = [base64.b64decode(t) for t in sent_payload["params"][0]]
    assert decoded == [b"tx1", b"tx2"]


def test_send_jito_bundle_vacio_no_llama_red(monkeypatch):
    called = False

    class _FakeClient:
        async def post(self, *a, **kw):
            nonlocal called
            called = True

    monkeypatch.setattr(jito, "_async_http", _FakeClient())

    result = asyncio.run(jito.send_jito_bundle([]))

    assert result is None
    assert called is False
