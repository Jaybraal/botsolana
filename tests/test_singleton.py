import fcntl

import pytest

from utils.singleton import acquire_lock


def test_acquire_lock_devuelve_true_primera_vez(tmp_path):
    lock = tmp_path / "bot.lock"
    assert acquire_lock(str(lock)) is True


def test_lock_bloquea_segunda_instancia(tmp_path):
    lock = tmp_path / "bot2.lock"
    assert acquire_lock(str(lock)) is True
    # Simula otra instancia: un file descriptor nuevo sobre el mismo path
    f = open(lock, "w")
    with pytest.raises(OSError):
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f.close()


def test_acquire_lock_devuelve_false_si_ya_tomado(tmp_path):
    lock = tmp_path / "bot3.lock"
    assert acquire_lock(str(lock)) is True
    assert acquire_lock(str(lock)) is False
