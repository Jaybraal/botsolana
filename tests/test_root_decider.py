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
