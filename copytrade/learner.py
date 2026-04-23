"""
Analiza el historial de trades cerrados para extraer patrones ganadores.
Genera reglas de scoring usables para evaluar futuras oportunidades.

Flujo:
  1. simulator.py captura contexto de mercado en cada entrada/salida
  2. Al cerrar un trade, simulator llama a learner.update()
  3. Con >= MIN_TRADES, genera data/learner_rules.json con thresholds aprendidos
  4. score_opportunity() puntúa nuevas oportunidades contra esas reglas

Fase 2 (cuando haya suficientes datos): scanner autónomo que detecta tokens
nuevos, los puntúa y compra sin necesitar copiar a nadie.
"""
import json
import os
from rich.table import Table
from rich.panel import Panel
from rich import box
from utils.logger import get_logger, console

log = get_logger("learner")

HISTORY_FILE = "data/sim_history.json"
RULES_FILE   = "data/learner_rules.json"
MIN_TRADES   = 5  # mínimo de trades cerrados antes de generar reglas útiles


# ── Persistencia ──────────────────────────────────────────────────────────────

def _load_history() -> list:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def load_rules() -> dict:
    if not os.path.exists(RULES_FILE):
        return {}
    try:
        with open(RULES_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_rules(rules: dict):
    with open(RULES_FILE, "w") as f:
        json.dump(rules, f, indent=2)


# ── Helpers estadísticos ──────────────────────────────────────────────────────

def _avg(trades: list, key: str) -> float | None:
    vals = [(t.get("entry_context") or {}).get(key) for t in trades]
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _win_rate_by(trades: list, field: str, from_context: bool = False) -> dict:
    groups: dict = {}
    for t in trades:
        k = ((t.get("entry_context") or {}).get(field, "?")
             if from_context else t.get(field, "?"))
        groups.setdefault(k, {"w": 0, "t": 0})
        groups[k]["t"] += 1
        if t.get("won"):
            groups[k]["w"] += 1
    return {
        k: {"win_rate": round(v["w"] / v["t"] * 100, 1), "trades": v["t"]}
        for k, v in sorted(groups.items(), key=lambda x: -(x[1]["w"] / x[1]["t"]))
    }


# ── Motor de aprendizaje ──────────────────────────────────────────────────────

def update() -> dict | None:
    """
    Lee el historial, calcula patrones y persiste learner_rules.json.
    Llamar automáticamente después de cada trade cerrado.
    Retorna None si aún no hay suficientes datos.
    """
    history  = _load_history()
    with_ctx = [t for t in history if t.get("entry_context")]

    if len(with_ctx) < MIN_TRADES:
        remaining = MIN_TRADES - len(with_ctx)
        log.info(
            f"[LEARNER] {len(with_ctx)}/{MIN_TRADES} trades con contexto — "
            f"faltan {remaining} para generar reglas"
        )
        return None

    winners = [t for t in with_ctx if t.get("won")]
    losers  = [t for t in with_ctx if not t.get("won")]

    if not winners:
        log.info("[LEARNER] Aún no hay trades ganadores para aprender.")
        return None

    ctx_keys = (
        "mcap_usd", "liquidity_usd", "volume_24h_usd",
        "vol_liq_ratio", "buy_pressure",
        "change_1h_pct", "change_24h_pct", "age_days",
    )
    winner_avg = {k: _avg(winners, k) for k in ctx_keys}
    loser_avg  = {k: _avg(losers,  k) for k in ctx_keys}

    # Thresholds derivados: zona de confort de los ganadores con margen de seguridad
    wa = winner_avg
    scoring_rules: dict = {}
    if wa["mcap_usd"]       is not None: scoring_rules["max_mcap_usd"]       = round(wa["mcap_usd"]       * 1.5, 0)
    if wa["liquidity_usd"]  is not None: scoring_rules["min_liquidity_usd"]   = round(wa["liquidity_usd"]  * 0.5, 0)
    if wa["volume_24h_usd"] is not None: scoring_rules["min_volume_24h_usd"]  = round(wa["volume_24h_usd"] * 0.5, 0)
    if wa["buy_pressure"]   is not None: scoring_rules["min_buy_pressure"]    = round(wa["buy_pressure"]   * 0.85, 3)
    if wa["change_1h_pct"]  is not None: scoring_rules["min_change_1h_pct"]   = round(wa["change_1h_pct"]  * 0.5, 2)
    if wa["age_days"]       is not None: scoring_rules["max_age_days"]        = round(wa["age_days"]       * 2.0, 1)

    rules = {
        "total_trades":       len(with_ctx),
        "winners":            len(winners),
        "losers":             len(losers),
        "win_rate":           round(len(winners) / len(with_ctx) * 100, 1),
        "winner_avg":         winner_avg,
        "loser_avg":          loser_avg,
        "win_rate_by_wallet": _win_rate_by(with_ctx, "wallet_label"),
        "win_rate_by_dex":    _win_rate_by(with_ctx, "dex_id", from_context=True),
        "scoring_rules":      scoring_rules,
    }

    _save_rules(rules)
    log.info(
        f"[bold cyan][LEARNER][/] Reglas actualizadas — "
        f"{len(with_ctx)} trades | "
        f"Win rate: [{'green' if rules['win_rate'] >= 50 else 'red'}]{rules['win_rate']}%[/] | "
        f"{len(scoring_rules)} reglas activas"
    )
    return rules


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_opportunity(context: dict) -> tuple[float, list[str]]:
    """
    Puntúa una oportunidad de 0.0 a 1.0 contra las reglas aprendidas.
    Retorna (score, lista_de_razones).
    Un score >= 0.7 indica buena coincidencia con patrones ganadores.
    """
    rules   = load_rules()
    scoring = rules.get("scoring_rules", {})
    if not scoring:
        return 0.5, ["Sin reglas aprendidas aún — se necesitan más trades"]

    passed  = 0
    total   = 0
    reasons: list[str] = []

    def check(label: str, value, threshold, is_max: bool = False):
        nonlocal passed, total
        if value is None or threshold is None:
            return
        total += 1
        ok = (value <= threshold) if is_max else (value >= threshold)
        if ok:
            passed += 1
        sym   = "≤" if is_max else "≥"
        color = "green" if ok else "red"
        reasons.append(
            f"[{color}]{'✓' if ok else '✗'}[/] {label}: "
            f"{value:,.2f} {sym} {threshold:,.2f}"
        )

    check("McAp",          context.get("mcap_usd"),       scoring.get("max_mcap_usd"),      is_max=True)
    check("Liquidez",      context.get("liquidity_usd"),  scoring.get("min_liquidity_usd"))
    check("Volumen 24h",   context.get("volume_24h_usd"), scoring.get("min_volume_24h_usd"))
    check("Buy pressure",  context.get("buy_pressure"),   scoring.get("min_buy_pressure"))
    check("Trend 1h %",    context.get("change_1h_pct"),  scoring.get("min_change_1h_pct"))
    check("Edad (días)",   context.get("age_days"),        scoring.get("max_age_days"),      is_max=True)

    score = round(passed / total, 2) if total > 0 else 0.5
    return score, reasons


# ── Display ───────────────────────────────────────────────────────────────────

def print_insights():
    """Muestra en consola los patrones aprendidos y las reglas generadas."""
    rules = load_rules()
    if not rules:
        console.print(Panel(
            f"[dim]Faltan {MIN_TRADES} trades cerrados para generar patrones.\n"
            "Los datos se acumulan automáticamente en cada trade.[/]",
            title="[bold white]🧠 Learner — Acumulando datos...",
            border_style="cyan",
        ))
        return

    wr       = rules.get("win_rate", 0)
    wr_color = "green" if wr >= 50 else "red"
    wa, la   = rules.get("winner_avg", {}), rules.get("loser_avg", {})

    # --- Tabla comparativa ---
    tbl = Table(box=box.SIMPLE_HEAD, show_header=True,
                header_style="bold bright_white", border_style="bright_black",
                expand=True, padding=(0, 1))
    tbl.add_column("Métrica",        style="dim white",  width=22)
    tbl.add_column("⬆ Ganadores",    style="bold green", width=18, justify="right")
    tbl.add_column("⬇ Perdedores",   style="bold red",   width=18, justify="right")
    tbl.add_column("Señal",          width=14, justify="center")

    def add_row(label: str, key: str, fmt: str = ".0f", better_if_high: bool = True):
        wv, lv = wa.get(key), la.get(key)
        ws = f"{wv:{fmt}}" if wv is not None else "—"
        ls = f"{lv:{fmt}}" if lv is not None else "—"
        if wv is not None and lv is not None:
            ok = (wv > lv) == better_if_high
            signal = "[green]↑ comprar[/]" if ok else "[red]↓ evitar[/]"
        else:
            signal = "—"
        tbl.add_row(label, ws, ls, signal)

    add_row("McAp USD",        "mcap_usd",       ".0f", better_if_high=False)
    add_row("Liquidez USD",    "liquidity_usd",  ".0f")
    add_row("Volumen 24h USD", "volume_24h_usd", ".0f")
    add_row("Ratio vol/liq",   "vol_liq_ratio",  ".2f")
    add_row("Buy pressure",    "buy_pressure",   ".1%")
    add_row("Cambio 1h %",     "change_1h_pct",  ".1f")
    add_row("Cambio 24h %",    "change_24h_pct", ".1f")
    add_row("Edad (días)",     "age_days",        ".1f", better_if_high=False)

    console.print(Panel(
        tbl,
        title=(
            f"[bold white]🧠 Patrones aprendidos — {rules['total_trades']} trades "
            f"| Win rate [{wr_color}]{wr}%[/] "
            f"([green]{rules['winners']}W[/] / [red]{rules['losers']}L[/])"
        ),
        border_style="cyan",
    ))

    # --- Win rate por wallet ---
    by_wallet = rules.get("win_rate_by_wallet", {})
    if by_wallet:
        wt = Table(box=box.SIMPLE_HEAD, show_header=True,
                   header_style="bold bright_white", border_style="bright_black",
                   padding=(0, 1))
        wt.add_column("Wallet / Plataforma", style="bold cyan")
        wt.add_column("Trades", style="dim white", justify="right")
        wt.add_column("Win Rate",             justify="right")
        for name, v in by_wallet.items():
            c = "green" if v["win_rate"] >= 55 else "yellow" if v["win_rate"] >= 40 else "red"
            wt.add_row(name, str(v["trades"]), f"[{c}]{v['win_rate']}%[/]")
        console.print(Panel(wt, title="[bold white]🏆 Win rate por wallet fuente",
                            border_style="cyan", padding=(0, 1)))

    # --- Reglas de scoring actuales ---
    sr = rules.get("scoring_rules", {})
    if sr:
        labels = {
            "max_mcap_usd":       ("McAp máximo",         "≤ X  (token pequeño = más upside)"),
            "min_liquidity_usd":  ("Liquidez mínima",     "≥ X  (puedo salir sin problema)"),
            "min_volume_24h_usd": ("Volumen 24h mínimo",  "≥ X  (hay actividad real)"),
            "min_buy_pressure":   ("Buy pressure mínima", "≥ X  (más compradores que vendedores)"),
            "min_change_1h_pct":  ("Tendencia 1h mínima", "≥ X% (momentum positivo)"),
            "max_age_days":       ("Edad máxima",         "≤ X  (token relativamente nuevo)"),
        }
        rt = Table(box=box.SIMPLE_HEAD, show_header=True,
                   header_style="bold bright_white", border_style="bright_black",
                   padding=(0, 1))
        rt.add_column("Regla aprendida",  style="bold yellow",  width=22)
        rt.add_column("Umbral",           style="white",        width=14, justify="right")
        rt.add_column("Lógica",           style="dim white")
        for key, val in sr.items():
            name, desc = labels.get(key, (key, ""))
            rt.add_row(name, f"{val:,.2f}", desc)
        console.print(Panel(rt, title="[bold white]📐 Reglas de scoring activas",
                            border_style="yellow", padding=(0, 1)))
