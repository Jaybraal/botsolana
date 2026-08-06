import pytest

import copytrade.root_trajectory as traj


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(traj, "TRAJECTORIES_PATH", str(tmp_path / "root_trajectories.jsonl"))
    traj._open_trajectories.clear()
    yield
    traj._open_trajectories.clear()


def test_record_snapshot_sin_start_no_hace_nada():
    traj.record_snapshot("MINT1", 15.0, 1.0)
    assert "MINT1" not in traj._open_trajectories


def test_start_record_close_persiste_snapshots(tmp_path):
    traj.start_trajectory("MINT1", entry_price=1.0)
    traj.record_snapshot("MINT1", 15.0, 1.05)
    traj.record_snapshot("MINT1", 30.0, 0.98)
    traj.close_trajectory("MINT1", wallet="Theo", config_id="champion")

    records = traj.load_trajectories(traj.TRAJECTORIES_PATH)
    assert len(records) == 1
    assert records[0]["token_mint"] == "MINT1"
    assert records[0]["wallet"] == "Theo"
    assert records[0]["config_id"] == "champion"
    assert records[0]["snapshots"] == [[0.0, 1.0], [15.0, 1.05], [30.0, 0.98]]
    assert "MINT1" not in traj._open_trajectories  # se limpia de memoria


def test_close_sin_start_no_escribe_nada():
    traj.close_trajectory("NUNCA_ABIERTO", wallet="X", config_id="champion")
    import os
    assert not os.path.exists(traj.TRAJECTORIES_PATH)


def test_load_trajectories_archivo_inexistente(tmp_path):
    assert traj.load_trajectories(str(tmp_path / "nope.jsonl")) == []
