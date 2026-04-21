"""
Análisis profundo del historial de trades.
Responde preguntas clave para optimizar la estrategia:
  - ¿Qué rango de MCap gana más?
  - ¿Qué edad de token es mejor entrada?
  - ¿Qué DEX tiene mejor win rate?
  - Si el TP fuera X%, ¿cuántos más hubiéramos cerrado en verde?
  - ¿Cuánto P&L dejamos sobre la mesa (pico vs salida real)?
  - ¿A qué hora del día entran mejor los trades?
"""

from __future__ import annotations
import json
import os
from collections import defaultdict

from rich.table   import Table
from rich.panel   import Panel
from rich.text    import Text
from rich.columns import Columns
from rich.console import Console
from rich         import box

TRADES_FILE = "data/trades.json"


def load_trades() -> list[dict]:
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []


# ── Helpers ─────────────────────────────────────────────────────────────

def _bucket(value: float, edges: list[float], labels: list[str]) -> str:
    for i, edge in enumerate(edges):
        if value < edge:
            return labels[i]
    return labels[-1]


def _wr(group: list[dict]) -> float:
    if not group:
        return 0.0
    return sum(1 for t in group if t.get("won")) / len(group) * 100


def _avg_pnl(group: list[dict]) -> float:
    if not group:
        return 0.0
    return sum(t["pnl_pct"] for t in group) / len(group)


def _total_usd(group: list[dict]) -> float:
    return sum(t.get("pnl_usd", 0) for t in group)


def _color_wr(wr: float) -> str:
    if wr >= 60:  return "bold green"
    if wr >= 45:  return "yellow"
    return "bold red"


def _color_pnl(pnl: float) -> str:
    return "green" if pnl >= 0 else "red"


# ── Panel 1: Sensibilidad de TP ──────────────────────────────────────────

def tp_sensitivity_panel(trades: list[dict]) -> Panel:
    """
    Simula qué habría pasado con distintos valores de TP.
    Usa el campo peak_pct (pico real alcanzado) de cada trade.
    """
    if not trades:
        return Panel(Text("Sin datos", style="dim"), title="TP Sensitivity", border_style="bright_black")

    tp_values = [10, 15, 20, 25, 30, 35, 40, 50, 60, 75, 100]

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 2),
    )
    table.add_column("TP %",      width=8,  justify="right")
    table.add_column("Win Rate",  width=10, justify="right")
    table.add_column("Wins",      width=6,  justify="right")
    table.add_column("Losses",    width=7,  justify="right")
    table.add_column("Avg P&L",   width=10, justify="right")
    table.add_column("P&L $",     width=10, justify="right")
    table.add_column("Nota",      width=20)

    current_tp = None
    try:
        from config import SNIPER_PROFIT_PCT
        current_tp = SNIPER_PROFIT_PCT
    except Exception:
        pass

    for tp in tp_values:
        wins   = 0
        losses = 0
        total_pnl_usd = 0.0
        pnl_pcts = []

        for t in trades:
            peak  = t.get("peak_pct",   t["pnl_pct"])
            trough = t.get("trough_pct", t["pnl_pct"])
            amount = t.get("amount_usd", 30)

            # Si el pico llegó al TP → habría cerrado en ganancia
            if peak >= tp:
                sim_pnl = tp
                wins += 1
            else:
                # Cerró por SL o timeout (usamos el P&L real)
                sim_pnl = t["pnl_pct"]
                if sim_pnl > 0:
                    wins += 1
                else:
                    losses += 1
            pnl_pcts.append(sim_pnl)
            total_pnl_usd += amount * sim_pnl / 100

        n      = len(trades)
        wr     = wins / n * 100 if n else 0
        avg_p  = sum(pnl_pcts) / n if n else 0
        is_cur = (current_tp is not None and abs(tp - current_tp) < 0.1)
        nota   = Text("◀ actual", style="bold cyan") if is_cur else Text("")

        table.add_row(
            Text(f"{tp}%", style="bold cyan" if is_cur else "white"),
            Text(f"{wr:.1f}%",       style=_color_wr(wr)),
            Text(str(wins),          style="green"),
            Text(str(losses),        style="red"),
            Text(f"{avg_p:+.1f}%",   style=_color_pnl(avg_p)),
            Text(f"${total_pnl_usd:+.2f}", style=_color_pnl(total_pnl_usd)),
            nota,
        )

    return Panel(table, title="[bold white]🎯  Sensibilidad de Take Profit", border_style="yellow", padding=(0, 0))


# ── Panel 2: Rendimiento por MCap ────────────────────────────────────────

def mcap_panel(trades: list[dict]) -> Panel:
    buckets_edges  = [50_000, 100_000, 250_000, 500_000, 1_000_000, float("inf")]
    buckets_labels = ["<$50K", "$50K–$100K", "$100K–$250K", "$250K–$500K", "$500K–$1M", ">$1M"]

    groups: dict[str, list] = {l: [] for l in buckets_labels}
    for t in trades:
        mc = t.get("mcap_entry", 0)
        label = _bucket(mc, buckets_edges, buckets_labels)
        groups[label].append(t)

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 2),
    )
    table.add_column("MCap",       width=14)
    table.add_column("Trades",     width=7,  justify="right")
    table.add_column("Win Rate",   width=10, justify="right")
    table.add_column("Avg P&L",    width=10, justify="right")
    table.add_column("P&L $",      width=10, justify="right")
    table.add_column("Avg Peak",   width=10, justify="right")

    for label in buckets_labels:
        g = groups[label]
        if not g:
            continue
        wr     = _wr(g)
        avg_p  = _avg_pnl(g)
        tot    = _total_usd(g)
        avg_pk = sum(t.get("peak_pct", t["pnl_pct"]) for t in g) / len(g)
        table.add_row(
            Text(label, style="cyan"),
            str(len(g)),
            Text(f"{wr:.1f}%",     style=_color_wr(wr)),
            Text(f"{avg_p:+.1f}%", style=_color_pnl(avg_p)),
            Text(f"${tot:+.2f}",   style=_color_pnl(tot)),
            Text(f"+{avg_pk:.1f}%", style="dim green"),
        )

    return Panel(table, title="[bold white]💰  Rendimiento por MarketCap", border_style="magenta", padding=(0, 0))


# ── Panel 3: Rendimiento por Edad del token ──────────────────────────────

def age_panel(trades: list[dict]) -> Panel:
    edges  = [0.5, 1, 2, 3, 6, float("inf")]
    labels = ["<30min", "30min–1h", "1h–2h", "2h–3h", "3h–6h", ">6h"]

    groups: dict[str, list] = {l: [] for l in labels}
    for t in trades:
        age = t.get("age_hours", 0)
        label = _bucket(age, edges, labels)
        groups[label].append(t)

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 2),
    )
    table.add_column("Edad token",  width=12)
    table.add_column("Trades",      width=7,  justify="right")
    table.add_column("Win Rate",    width=10, justify="right")
    table.add_column("Avg P&L",     width=10, justify="right")
    table.add_column("P&L $",       width=10, justify="right")
    table.add_column("Avg Peak",    width=10, justify="right")

    for label in labels:
        g = groups[label]
        if not g:
            continue
        wr     = _wr(g)
        avg_p  = _avg_pnl(g)
        tot    = _total_usd(g)
        avg_pk = sum(t.get("peak_pct", t["pnl_pct"]) for t in g) / len(g)
        table.add_row(
            Text(label, style="cyan"),
            str(len(g)),
            Text(f"{wr:.1f}%",      style=_color_wr(wr)),
            Text(f"{avg_p:+.1f}%",  style=_color_pnl(avg_p)),
            Text(f"${tot:+.2f}",    style=_color_pnl(tot)),
            Text(f"+{avg_pk:.1f}%", style="dim green"),
        )

    return Panel(table, title="[bold white]⏱  Rendimiento por Edad del Token", border_style="cyan", padding=(0, 0))


# ── Panel 4: DEX + Hora del día ──────────────────────────────────────────

def dex_hour_panel(trades: list[dict]) -> Panel:
    # DEX
    dex_groups: dict[str, list] = defaultdict(list)
    for t in trades:
        dex_groups[t.get("dex", "?")].append(t)

    dex_table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        padding=(0, 2),
    )
    dex_table.add_column("DEX",      width=14)
    dex_table.add_column("Trades",   width=7,  justify="right")
    dex_table.add_column("Win Rate", width=10, justify="right")
    dex_table.add_column("Avg P&L",  width=10, justify="right")

    for dex, g in sorted(dex_groups.items(), key=lambda x: len(x[1]), reverse=True):
        wr    = _wr(g)
        avg_p = _avg_pnl(g)
        dex_table.add_row(
            Text(dex, style="cyan"),
            str(len(g)),
            Text(f"{wr:.1f}%",     style=_color_wr(wr)),
            Text(f"{avg_p:+.1f}%", style=_color_pnl(avg_p)),
        )

    # Hora del día (bloques de 4h)
    hour_labels = ["00–04", "04–08", "08–12", "12–16", "16–20", "20–24"]
    hour_groups: dict[str, list] = {l: [] for l in hour_labels}
    for t in trades:
        h = t.get("hour_of_day", 0)
        label = hour_labels[h // 4]
        hour_groups[label].append(t)

    hour_table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        padding=(0, 2),
    )
    hour_table.add_column("Hora",    width=8)
    hour_table.add_column("Trades",  width=7,  justify="right")
    hour_table.add_column("Win Rate",width=10, justify="right")
    hour_table.add_column("Avg P&L", width=10, justify="right")

    for label in hour_labels:
        g = hour_groups[label]
        if not g:
            continue
        wr    = _wr(g)
        avg_p = _avg_pnl(g)
        hour_table.add_row(
            Text(label, style="cyan"),
            str(len(g)),
            Text(f"{wr:.1f}%",     style=_color_wr(wr)),
            Text(f"{avg_p:+.1f}%", style=_color_pnl(avg_p)),
        )

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Panel(dex_table, title="[bold white]🔁 Por DEX",        border_style="bright_black", padding=(0,0)),
        Panel(hour_table,title="[bold white]🕐 Por Hora del día",border_style="bright_black", padding=(0,0)),
    )
    return Panel(grid, title="[bold white]📡  DEX & Timing", border_style="bright_black", padding=(0, 0))


# ── Panel 5: P&L "dejado sobre la mesa" ──────────────────────────────────

def peak_analysis_panel(trades: list[dict]) -> Panel:
    """
    Compara el pico real alcanzado con el P&L final.
    Si el pico fue mucho mayor que la salida → el TP está muy alto
    o el SL se activó tras un rebote.
    """
    if not trades:
        return Panel(Text("Sin datos", style="dim"), title="Peak Analysis", border_style="bright_black")

    rows = []
    for t in trades:
        peak    = t.get("peak_pct", t["pnl_pct"])
        actual  = t["pnl_pct"]
        left    = peak - actual       # cuánto dejamos sobre la mesa
        rows.append((t["symbol"], peak, actual, left, t["won"], t["reason"]))

    rows.sort(key=lambda x: x[3], reverse=True)  # mayor "dejado" primero

    avg_peak   = sum(r[1] for r in rows) / len(rows)
    avg_actual = sum(r[2] for r in rows) / len(rows)
    avg_left   = sum(r[3] for r in rows) / len(rows)

    # Trades donde el pico fue grande pero salimos en pérdida
    missed = [r for r in rows if r[1] >= 20 and r[2] < 0]

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 1),
    )
    table.add_column("Token",    width=8)
    table.add_column("Pico",     width=9,  justify="right")
    table.add_column("Salida",   width=9,  justify="right")
    table.add_column("Dejado",   width=9,  justify="right")
    table.add_column("Razón",    width=18)

    for sym, peak, actual, left, won, reason in rows[:12]:
        table.add_row(
            Text(sym, style="cyan"),
            Text(f"+{peak:.1f}%",   style="dim green"),
            Text(f"{actual:+.1f}%", style=_color_pnl(actual)),
            Text(f"{left:+.1f}%",   style="yellow" if left > 10 else "dim"),
            Text(reason[:18],       style="dim"),
        )

    summary = Table.grid(expand=True, padding=(0, 3))
    summary.add_column(); summary.add_column(); summary.add_column(); summary.add_column()
    summary.add_row(
        Text.assemble(("Pico promedio:  ", "dim"), (f"+{avg_peak:.1f}%", "bold green")),
        Text.assemble(("Salida promedio: ", "dim"), (f"{avg_actual:+.1f}%", f"bold {'green' if avg_actual>=0 else 'red'}")),
        Text.assemble(("Dejado promedio: ", "dim"), (f"{avg_left:+.1f}%", "yellow")),
        Text.assemble(("Pico≥20% pero loss: ", "dim"), (str(len(missed)), "bold red")),
    )

    content = Table.grid(expand=True)
    content.add_row(summary)
    content.add_row(Text(""))
    content.add_row(table)

    return Panel(content, title="[bold white]📉  Análisis de Picos (P&L dejado sobre la mesa)", border_style="yellow", padding=(0, 1))


# ── Panel 6: Señales de Buys 5m ──────────────────────────────────────────

def entry_signal_panel(trades: list[dict]) -> Panel:
    """¿Cuántos buys_5m al entrar correlaciona con ganar?"""
    edges  = [10, 25, 50, 100, 250, float("inf")]
    labels = ["<10", "10–25", "25–50", "50–100", "100–250", ">250"]

    groups: dict[str, list] = {l: [] for l in labels}
    for t in trades:
        b = t.get("buys_5m", 0)
        groups[_bucket(b, edges, labels)].append(t)

    ch_edges  = [-20, -5, 0, 5, 15, 30, float("inf")]
    ch_labels = ["<-20%", "-20–-5%", "-5–0%", "0–5%", "5–15%", "15–30%", ">30%"]
    ch_groups: dict[str, list] = {l: [] for l in ch_labels}
    for t in trades:
        ch = t.get("change_5m", 0)
        ch_groups[_bucket(ch, ch_edges, ch_labels)].append(t)

    buys_table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        padding=(0, 1),
    )
    buys_table.add_column("Buys 5m",   width=10)
    buys_table.add_column("Trades",    width=7, justify="right")
    buys_table.add_column("Win Rate",  width=10, justify="right")
    buys_table.add_column("Avg P&L",   width=10, justify="right")

    for label in labels:
        g = groups[label]
        if not g:
            continue
        buys_table.add_row(
            Text(label, style="cyan"),
            str(len(g)),
            Text(f"{_wr(g):.1f}%",      style=_color_wr(_wr(g))),
            Text(f"{_avg_pnl(g):+.1f}%",style=_color_pnl(_avg_pnl(g))),
        )

    ch_table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        padding=(0, 1),
    )
    ch_table.add_column("Cambio 5m",  width=10)
    ch_table.add_column("Trades",     width=7, justify="right")
    ch_table.add_column("Win Rate",   width=10, justify="right")
    ch_table.add_column("Avg P&L",    width=10, justify="right")

    for label in ch_labels:
        g = ch_groups[label]
        if not g:
            continue
        ch_table.add_row(
            Text(label, style="cyan"),
            str(len(g)),
            Text(f"{_wr(g):.1f}%",      style=_color_wr(_wr(g))),
            Text(f"{_avg_pnl(g):+.1f}%",style=_color_pnl(_avg_pnl(g))),
        )

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1); grid.add_column(ratio=1)
    grid.add_row(
        Panel(buys_table, title="[bold white]🛒 Buys en 5m al entrar",   border_style="bright_black", padding=(0,0)),
        Panel(ch_table,   title="[bold white]📈 Cambio 5m al entrar",    border_style="bright_black", padding=(0,0)),
    )
    return Panel(grid, title="[bold white]🔬  Calidad de la señal de entrada", border_style="green", padding=(0, 0))


# ── Reporte completo ─────────────────────────────────────────────────────

def full_report(console: Console | None = None):
    """Imprime todos los paneles de análisis."""
    from rich.console import Console as C
    from rich.rule    import Rule
    from sniper.stats import summary_panel, history_panel

    con = console or C()
    trades = load_trades()

    if not trades:
        con.print(Panel(
            "[dim]Sin trades registrados. Deja correr el bot unos ciclos primero.[/]",
            border_style="bright_black",
        ))
        return

    con.print(Rule(f"[bold white]ANÁLISIS COMPLETO — {len(trades)} trades", style="cyan"))
    con.print(summary_panel(trades))
    con.print(tp_sensitivity_panel(trades))
    con.print(peak_analysis_panel(trades))
    con.print(mcap_panel(trades))
    con.print(age_panel(trades))
    con.print(dex_hour_panel(trades))
    con.print(entry_signal_panel(trades))
    con.print(history_panel(n=20))
