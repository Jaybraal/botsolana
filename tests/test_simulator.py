import threading

import pytest

import copytrade.simulator as sim


# ── _stale_watcher_loop: backstop independiente de _handle_buy ─────────────
#
# Bug real (investigado 06/08/26): _auto_close_stale() solo se llamaba desde
# dentro de _handle_buy(), o sea, únicamente cuando llegaba una compra nueva
# de alguna wallet monitoreada. Si había un hueco de actividad, las
# posiciones vencidas (stop-loss/max-hold ya cumplido) quedaban sin cerrar
# — a veces horas — hasta la próxima compra de cualquier wallet, momento en
# que se cerraban todas de golpe al precio que hubiera en ese instante (ya
# desplomado en pump.fun). Evidencia: 41 auto-cierres en el log real, varios
# en ráfaga, con holds de hasta 721 min contra un SIM_MAX_HOLD_MIN=30 y
# pérdidas de hasta -93% contra un stop-loss mucho más chico.
#
# El fix: un loop propio en thread daemon que llama _auto_close_stale() cada
# POLL_INTERVAL_S, sin depender de que llegue una compra nueva.

def test_stale_watcher_loop_cierra_posiciones_sin_esperar_una_compra_nueva(monkeypatch):
    calls = []
    monkeypatch.setattr(sim, "_auto_close_stale", lambda: calls.append(1))

    def fake_sleep(_seconds):
        if calls:
            raise StopIteration  # corta el loop infinito tras la 1ra iteración

    monkeypatch.setattr(sim.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        sim._stale_watcher_loop()

    assert calls == [1]


def test_stale_watcher_loop_usa_sim_stale_poll_interval_s(monkeypatch):
    slept_for = []
    monkeypatch.setattr(sim, "_auto_close_stale", lambda: slept_for.append("closed") or None)

    def fake_sleep(seconds):
        slept_for.append(seconds)
        if len(slept_for) >= 2:
            raise StopIteration

    monkeypatch.setattr(sim.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        sim._stale_watcher_loop()

    assert slept_for[0] == sim.SIM_STALE_POLL_INTERVAL_S


def test_start_lanza_thread_daemon(monkeypatch):
    created = {}

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            created["target"] = target
            created["daemon"] = daemon

        def start(self):
            created["started"] = True

    monkeypatch.setattr(threading, "Thread", _FakeThread)

    sim.start()

    assert created["target"] == sim._stale_watcher_loop
    assert created["daemon"] is True
    assert created["started"] is True
