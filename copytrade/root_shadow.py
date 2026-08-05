"""
Shadow-mode: además del scorer de reglas propio (scorer.py), le manda el
mismo entry_context al CopyScorer de ROOT (regresión logística entrenada en
~/Proyectos/root, ver bin/copyscorer-predict.mjs) y registra qué hubiera
decidido — SIN tocar la decisión real todavía.

Por qué: CopyScorer gana la comparación offline (F1 61% vs 11.8% de las
reglas, 05/08/26) pero nunca había evaluado un trade en vivo. Este es el
paso previo a conectarlo de verdad — correr en paralelo, guardar cada
comparación en data/root_shadow_log.jsonl, y decidir con datos reales (no
solo el holdout offline) si reemplaza a las reglas.

Garantía de no-interferencia: `shadow_score()` nunca bloquea ni puede hacer
fallar el camino de trading real — lanza `_run()` en un hilo daemon aparte
y cualquier excepción dentro de `_run()` se loguea y se descarta ahí mismo.
"""
import json
import os
import shutil
import subprocess
import threading
import time

from copytrade import root_sim
from utils.logger import get_logger

log = get_logger("root_shadow")

ROOT_DIR = os.path.expanduser(os.getenv("ROOT_DIR", "~/Proyectos/root"))
PREDICT_SCRIPT = os.path.join(ROOT_DIR, "bin", "copyscorer-predict.mjs")
SHADOW_LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "root_shadow_log.jsonl",
)

# Rutas típicas de node en macOS cuando no viene de una instalación via PATH
# estándar (Homebrew Intel/Apple Silicon, nvm no cubierto a propósito — ahí
# ROOT_NODE_BIN es la vía explícita).
_COMMON_NODE_PATHS = ["/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node"]


def _resolve_node_bin() -> str:
    """Resuelve el binario de `node` sin depender ciegamente del PATH.

    Bug real encontrado en vivo (05/08/26): bajo launchd el PATH es mínimo
    (no incluye /usr/local/bin ni /opt/homebrew/bin) — `node` a secas fallaba
    con "[Errno 2] No such file or directory: 'node'" aunque el shadow-mode
    andaba perfecto probado a mano desde una shell normal con PATH completo.
    """
    override = os.getenv("ROOT_NODE_BIN")
    if override:
        return override
    found = shutil.which("node")
    if found:
        return found
    for candidate in _COMMON_NODE_PATHS:
        if os.path.exists(candidate):
            return candidate
    return "node"  # último recurso — mismo error que antes, pero ya no hay más de dónde sacarlo


NODE_BIN = _resolve_node_bin()
TIMEOUT_S = float(os.getenv("ROOT_SHADOW_TIMEOUT_S", "5"))
ENABLED = os.getenv("ROOT_SHADOW_ENABLED", "true").lower() == "true"

_write_lock = threading.Lock()


def _write_result(record: dict) -> None:
    with _write_lock:
        os.makedirs(os.path.dirname(SHADOW_LOG_PATH), exist_ok=True)
        with open(SHADOW_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")


def _run(wallet_label: str, entry_context: dict, rule_score: int, rule_passed: bool, token_mint: str | None = None) -> None:
    """Llama a ROOT vía subprocess y registra el resultado. Corre en un hilo
    aparte, sin nadie esperándolo — cualquier error se loguea acá y se
    descarta, nunca debe propagar."""
    try:
        proc = subprocess.run(
            [NODE_BIN, PREDICT_SCRIPT],
            input=json.dumps(entry_context),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=ROOT_DIR,
        )
    except Exception as e:
        log.warning(f"[root_shadow] {wallet_label}: no se pudo llamar a ROOT — {e}")
        return

    if proc.returncode != 0:
        log.warning(f"[root_shadow] {wallet_label}: ROOT devolvió error — {proc.stderr.strip()[:200]}")
        return

    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log.warning(f"[root_shadow] {wallet_label}: salida de ROOT no es JSON — {e}")
        return

    if out.get("skipped"):
        log.debug(f"[root_shadow] {wallet_label}: ROOT sin evaluar — {out.get('reason')}")
        return

    root_score = out["score"]
    root_decision = out["decision"]
    rule_decision = "COPIAR" if rule_passed else "SKIP"
    agree = (root_decision == "COPIAR") == rule_passed

    log.info(
        f"[root_shadow] {wallet_label} → ROOT score={root_score} ({root_decision}) "
        f"vs reglas score={rule_score} ({rule_decision}) — {'coincide' if agree else 'DIFIERE'}"
    )
    _write_result({
        "ts": time.time(),
        "wallet": wallet_label,
        "root_score": root_score,
        "root_prob": out.get("prob"),
        "root_decision": root_decision,
        "rule_score": rule_score,
        "rule_decision": rule_decision,
        "agree": agree,
        "entry_context": entry_context,
    })

    # Además de comparar, si ROOT dice COPIAR y sabemos qué token es, que lo
    # "compre" de verdad en su propio paper-trading (root_sim.py) para medir
    # si es rentable — no solo si coincide con las reglas. Un error acá nunca
    # debe tirar abajo el registro de arriba (ya se guardó).
    if root_decision == "COPIAR" and token_mint:
        try:
            root_sim.open_position(wallet_label, token_mint, entry_context, root_score, out.get("prob"))
        except Exception as e:
            log.warning(f"[root_shadow] {wallet_label}: root_sim.open_position falló — {e}")


def shadow_score(
    wallet_label: str,
    entry_context: dict | None,
    rule_score: int,
    rule_passed: bool,
    token_mint: str | None = None,
) -> None:
    """Punto de entrada llamado desde scorer.py.should_copy(). Nunca bloquea:
    si está habilitado y hay entry_context, lanza `_run` en un hilo daemon
    aparte y vuelve enseguida. La decisión real de trading nunca depende de
    esto — es puramente observacional. `token_mint` es opcional (llamadores
    viejos que no lo pasan simplemente no disparan la simulación de ROOT)."""
    if not ENABLED or not entry_context:
        return
    if not os.path.exists(PREDICT_SCRIPT):
        return
    threading.Thread(
        target=_run,
        args=(wallet_label, entry_context, rule_score, rule_passed, token_mint),
        daemon=True,
    ).start()
