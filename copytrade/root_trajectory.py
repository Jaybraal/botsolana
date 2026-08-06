"""
Persiste la trayectoria de precio completa de una posición mientras está
abierta (no solo entrada/salida). Sin esto, root_backtest.py no puede
simular qué hubiera pasado con otro stop-loss/max-hold — solo puede
reevaluar si una config hubiera dicho COPIAR, usando el pnl ya registrado.

Corre llamado desde root_sim.py en el mismo thread de monitoreo — no tiene
su propio thread, solo maneja el estado en memoria de trayectorias abiertas
y las escribe a disco al cerrar.
"""
import json
import os
import threading

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TRAJECTORIES_PATH = os.path.join(_DATA_DIR, "root_trajectories.jsonl")

_lock = threading.Lock()
_open_trajectories: dict[str, list[list[float]]] = {}


def start_trajectory(token_mint: str, entry_price: float) -> None:
    with _lock:
        _open_trajectories[token_mint] = [[0.0, entry_price]]


def record_snapshot(token_mint: str, elapsed_s: float, price: float) -> None:
    """No hace nada si no hay trayectoria arrancada para ese mint — nunca
    inventa un punto de partida que no se registró."""
    with _lock:
        if token_mint not in _open_trajectories:
            return
        _open_trajectories[token_mint].append([elapsed_s, price])


def close_trajectory(token_mint: str, wallet: str, config_id: str) -> None:
    with _lock:
        snapshots = _open_trajectories.pop(token_mint, None)
    if not snapshots:
        return
    os.makedirs(os.path.dirname(TRAJECTORIES_PATH), exist_ok=True)
    record = {"token_mint": token_mint, "wallet": wallet, "config_id": config_id, "snapshots": snapshots}
    with open(TRAJECTORIES_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_trajectories(path: str = None) -> list[dict]:
    path = path or TRAJECTORIES_PATH
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
