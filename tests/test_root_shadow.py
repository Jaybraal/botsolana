import json
import subprocess
import threading

import pytest

import copytrade.root_shadow as rs
from copytrade.root_shadow import _resolve_node_bin

ENTRY_CONTEXT = {
    "mcap_usd": 12000,
    "liquidity_usd": 5000,
    "volume_24h_usd": 30000,
    "vol_liq_ratio": 6,
    "buy_pressure": 0.55,
    "change_1h_pct": 10,
    "change_6h_pct": 5,
    "change_24h_pct": 2,
    "age_days": 0.02,
    "dex_id": "pumpfun",
}


class FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture(autouse=True)
def isolate_shadow_log(tmp_path, monkeypatch):
    """Cada test escribe a su propio jsonl, nunca al del repo real."""
    monkeypatch.setattr(rs, "SHADOW_LOG_PATH", str(tmp_path / "root_shadow_log.jsonl"))
    yield


def test_run_registra_cuando_root_coincide(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": False, "prob": 0.72, "score": 72, "threshold": 0.5, "decision": "COPIAR"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))

    rs._run("Cented", ENTRY_CONTEXT, rule_score=80, rule_passed=True)

    lines = (tmp_path / "root_shadow_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["wallet"] == "Cented"
    assert record["root_decision"] == "COPIAR"
    assert record["root_score"] == 72
    assert record["rule_score"] == 80
    assert record["rule_decision"] == "COPIAR"
    assert record["agree"] is True
    assert record["entry_context"] == ENTRY_CONTEXT


def test_run_registra_cuando_root_difiere(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": False, "prob": 0.2, "score": 20, "threshold": 0.5, "decision": "SKIP"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))

    rs._run("Theo", ENTRY_CONTEXT, rule_score=90, rule_passed=True)

    record = json.loads((tmp_path / "root_shadow_log.jsonl").read_text().strip())
    assert record["agree"] is False
    assert record["root_decision"] == "SKIP"
    assert record["rule_decision"] == "COPIAR"


def test_run_no_escribe_nada_si_root_dice_skipped(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": True, "reason": "faltan features requeridas en entry_context"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))

    rs._run("Decu", ENTRY_CONTEXT, rule_score=60, rule_passed=False)

    assert not (tmp_path / "root_shadow_log.jsonl").exists()


def test_run_no_explota_si_root_devuelve_error(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="boom"))

    rs._run("Nyhrox", ENTRY_CONTEXT, rule_score=50, rule_passed=False)  # no debe lanzar

    assert not (tmp_path / "root_shadow_log.jsonl").exists()


def test_run_no_explota_si_subprocess_falla(monkeypatch, tmp_path):
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="node", timeout=5)
    monkeypatch.setattr(subprocess, "run", _raise)

    rs._run("Yenni", ENTRY_CONTEXT, rule_score=50, rule_passed=False)  # no debe lanzar

    assert not (tmp_path / "root_shadow_log.jsonl").exists()


def test_run_no_explota_si_salida_no_es_json(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout="no soy json"))

    rs._run("Latuche", ENTRY_CONTEXT, rule_score=50, rule_passed=False)  # no debe lanzar

    assert not (tmp_path / "root_shadow_log.jsonl").exists()


def test_shadow_score_no_lanza_hilo_sin_entry_context(monkeypatch):
    calls = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: calls.append((a, k)) or pytest.fail("no debería crear Thread"))
    rs.shadow_score("Cented", None, rule_score=80, rule_passed=True)
    rs.shadow_score("Cented", {}, rule_score=80, rule_passed=True)
    assert calls == []


def test_shadow_score_no_lanza_hilo_si_deshabilitado(monkeypatch):
    monkeypatch.setattr(rs, "ENABLED", False)
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: pytest.fail("no debería crear Thread"))
    rs.shadow_score("Cented", ENTRY_CONTEXT, rule_score=80, rule_passed=True)


def test_shadow_score_lanza_hilo_daemon_cuando_corresponde(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["target"] = target
            started["args"] = args
            started["daemon"] = daemon

        def start(self):
            started["started"] = True

    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(rs, "ENABLED", True)
    monkeypatch.setattr("os.path.exists", lambda p: True)

    rs.shadow_score("Cented", ENTRY_CONTEXT, rule_score=80, rule_passed=True, token_mint="MINT1")

    assert started["started"] is True
    assert started["daemon"] is True
    assert started["target"] is rs._run
    assert started["args"] == ("Cented", ENTRY_CONTEXT, 80, True, "MINT1")


def test_shadow_score_token_mint_default_none(monkeypatch):
    """Los llamadores viejos que no pasan token_mint (p.ej. executor.py hoy)
    siguen funcionando — solo no habrá simulación de ROOT para esos casos."""
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            started["args"] = args

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(rs, "ENABLED", True)
    monkeypatch.setattr("os.path.exists", lambda p: True)

    rs.shadow_score("Cented", ENTRY_CONTEXT, rule_score=80, rule_passed=True)

    assert started["args"][-1] is None


# ── Resolución robusta del binario de node ──────────────────────────────────
# Bug real encontrado en vivo (05/08/26): bajo launchd el PATH es mínimo
# (no trae /usr/local/bin ni /opt/homebrew/bin) — `node` a secas fallaba con
# "[Errno 2] No such file or directory: 'node'" aunque andaba perfecto
# corriendo a mano desde una shell normal.

def test_resolve_node_bin_respeta_override_de_env(monkeypatch):
    monkeypatch.setenv("ROOT_NODE_BIN", "/ruta/custom/node")
    assert _resolve_node_bin() == "/ruta/custom/node"


def test_resolve_node_bin_usa_shutil_which_si_hay_path(monkeypatch):
    monkeypatch.delenv("ROOT_NODE_BIN", raising=False)
    monkeypatch.setattr(rs.shutil, "which", lambda name: "/opt/homebrew/bin/node")
    assert _resolve_node_bin() == "/opt/homebrew/bin/node"


def test_resolve_node_bin_cae_a_rutas_comunes_si_which_falla(monkeypatch):
    monkeypatch.delenv("ROOT_NODE_BIN", raising=False)
    monkeypatch.setattr(rs.shutil, "which", lambda name: None)
    monkeypatch.setattr(rs.os.path, "exists", lambda p: p == "/usr/local/bin/node")
    assert _resolve_node_bin() == "/usr/local/bin/node"


def test_resolve_node_bin_ultimo_recurso_es_node_literal(monkeypatch):
    monkeypatch.delenv("ROOT_NODE_BIN", raising=False)
    monkeypatch.setattr(rs.shutil, "which", lambda name: None)
    monkeypatch.setattr(rs.os.path, "exists", lambda p: False)
    assert _resolve_node_bin() == "node"


# ── Integración con root_sim: abrir paper-trade cuando ROOT dice COPIAR ─────
# El usuario pidió (05/08) que además de comparar, ROOT simule sus propios
# trades para ver si es rentable — root_shadow._run() dispara root_sim solo
# cuando ROOT dice COPIAR y hay token_mint disponible.

def test_run_abre_posicion_en_root_sim_si_root_dice_copiar(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": False, "prob": 0.8, "score": 80, "threshold": 0.5, "decision": "COPIAR"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))
    calls = []
    monkeypatch.setattr(rs.root_sim, "open_position", lambda *a, **k: calls.append((a, k)))

    rs._run("Cented", ENTRY_CONTEXT, rule_score=50, rule_passed=False, token_mint="MINT1")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ("Cented", "MINT1")


def test_run_no_abre_posicion_si_root_dice_skip(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": False, "prob": 0.2, "score": 20, "threshold": 0.5, "decision": "SKIP"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))
    calls = []
    monkeypatch.setattr(rs.root_sim, "open_position", lambda *a, **k: calls.append((a, k)))

    rs._run("Cented", ENTRY_CONTEXT, rule_score=90, rule_passed=True, token_mint="MINT1")

    assert calls == []


def test_run_no_abre_posicion_sin_token_mint(monkeypatch, tmp_path):
    fake_out = json.dumps({"skipped": False, "prob": 0.8, "score": 80, "threshold": 0.5, "decision": "COPIAR"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))
    calls = []
    monkeypatch.setattr(rs.root_sim, "open_position", lambda *a, **k: calls.append((a, k)))

    rs._run("Cented", ENTRY_CONTEXT, rule_score=50, rule_passed=False, token_mint=None)

    assert calls == []


def test_run_falla_de_root_sim_no_rompe_el_shadow_logging(monkeypatch, tmp_path):
    """Si root_sim explota al abrir la posición, el registro de comparación
    (lo más importante) igual se guarda — no se pierde por un error aparte."""
    fake_out = json.dumps({"skipped": False, "prob": 0.8, "score": 80, "threshold": 0.5, "decision": "COPIAR"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(stdout=fake_out))
    monkeypatch.setattr(rs.root_sim, "open_position", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    rs._run("Cented", ENTRY_CONTEXT, rule_score=50, rule_passed=False, token_mint="MINT1")  # no debe lanzar

    record = json.loads((tmp_path / "root_shadow_log.jsonl").read_text().strip())
    assert record["root_decision"] == "COPIAR"
