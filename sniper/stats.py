"""
Estadísticas del simulador: win rate, P&L, mejores/peores trades.
Lee data/trades.json y devuelve paneles Rich listos para imprimir.
"""

from __future__ import annotations
import json
import os
import time
from datetime import datetime

from rich.table  import Table
from rich.panel  import Panel
from rich.text   import Text
from rich.columns import Columns
from rich        import box

TRADES_FILE = "data/trades.json"


def load_trades() -> list[dict]:
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def compute(trades: list[dict]) -> dict:
    if not trades:
        return {}

    wins   = [t for t in trades if t.get("won")]
    losses = [t for t in trades if not t.get("won")]

    pnl_list  = [t["pnl_pct"] for t in trades]
    usd_list  = [t["pnl_usd"] for t in trades]
    hold_list = [t["hold_min"] for t in trades]

    win_rate   = len(wins) / len(trades) * 100
    avg_pnl    = sum(pnl_list)  / len(trades)
    avg_win    = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss   = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    total_usd  = sum(usd_list)
    avg_hold   = sum(hold_list) / len(trades)

    best  = max(trades, key=lambda t: t["pnl_pct"])
    worst = min(trades, key=lambda t: t["pnl_pct"])

    # Racha actual
    streak = 0
    streak_type = None
    for t in reversed(trades):
        if streak_type is None:
            streak_type = t["won"]
        if t["won"] == streak_type:
            streak += 1
        else:
            break

    # Expectativa matemática: (winRate * avgWin) + (lossRate * avgLoss)
    loss_rate  = (100 - win_rate) / 100
    expectancy = (win_rate / 100 * avg_win) + (loss_rate * avg_loss)

    return {
        "total":      len(trades),
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   win_rate,
        "avg_pnl":    avg_pnl,
        "avg_win":    avg_win,
        "avg_loss":   avg_loss,
        "total_usd":  total_usd,
        "avg_hold":   avg_hold,
        "best":       best,
        "worst":      worst,
        "streak":     streak,
        "streak_won": streak_type,
        "expectancy": expectancy,
    }


# ── Paneles Rich ──────────────────────────────────────────────────────────

def _stat_cell(label: str, value: str, color: str = "white", note: str = "") -> Text:
    t = Text()
    t.append(f"{label}\n", style="dim white")
    t.append(value, style=f"bold {color}")
    if note:
        t.append(f"  {note}", style="dim")
    return t


def summary_panel(trades: list[dict] | None = None) -> Panel:
    if trades is None:
        trades = load_trades()
    s = compute(trades)

    if not s:
        return Panel(
            Text("Sin trades registrados aún. Esperando primera señal...", style="dim italic"),
            title="[bold white]📈  Simulación — Estadísticas",
            border_style="bright_black",
            padding=(1, 2),
        )

    wr_color = "green" if s["win_rate"] >= 50 else "red"
    pnl_color = "green" if s["total_usd"] >= 0 else "red"
    exp_color = "green" if s["expectancy"] >= 0 else "red"

    streak_txt = (
        f"{'🔥' if s['streak_won'] else '❄️ '} "
        f"{'WIN' if s['streak_won'] else 'LOSS'} x{s['streak']}"
    )

    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    grid.add_row(
        _stat_cell("Win Rate",   f"{s['win_rate']:.1f}%",   wr_color,   f"({s['wins']}W / {s['losses']}L)"),
        _stat_cell("P&L Total",  f"${s['total_usd']:+.2f}", pnl_color),
        _stat_cell("Trades",     str(s["total"]),            "cyan"),
        _stat_cell("Racha",      streak_txt,                 "yellow"),
    )
    grid.add_row(Text(""), Text(""), Text(""), Text(""))
    grid.add_row(
        _stat_cell("Avg Win",    f"{s['avg_win']:+.1f}%",   "green"),
        _stat_cell("Avg Loss",   f"{s['avg_loss']:+.1f}%",  "red"),
        _stat_cell("Avg Hold",   f"{s['avg_hold']:.0f} min","dim white"),
        _stat_cell("Expectancy", f"{s['expectancy']:+.2f}%",exp_color, "por trade"),
    )
    grid.add_row(Text(""), Text(""), Text(""), Text(""))
    grid.add_row(
        _stat_cell("Mejor",  f"{s['best']['symbol']} {s['best']['pnl_pct']:+.1f}%",   "green", s['best']['reason']),
        _stat_cell("Peor",   f"{s['worst']['symbol']} {s['worst']['pnl_pct']:+.1f}%", "red",   s['worst']['reason']),
        Text(""),
        Text(""),
    )

    return Panel(
        grid,
        title="[bold white]📈  Simulación — Estadísticas",
        border_style="cyan",
        padding=(1, 1),
    )


def history_panel(n: int = 10) -> Panel:
    """Últimos N trades cerrados en tabla."""
    trades = load_trades()

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_black",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Hora",    style="dim white",  width=8)
    table.add_column("Token",   style="bold cyan",  width=8)
    table.add_column("Entrada", style="white",      width=13, justify="right")
    table.add_column("Salida",  style="white",      width=13, justify="right")
    table.add_column("P&L %",                       width=9,  justify="right")
    table.add_column("P&L $",                       width=9,  justify="right")
    table.add_column("Hold",    style="dim white",  width=7,  justify="right")
    table.add_column("MCap",    style="dim white",  width=10, justify="right")
    table.add_column("Razón",   style="dim white",  width=16)

    recent = list(reversed(trades))[:n]
    if not recent:
        table.add_row("—","—","—","—",
                      Text("sin historial", style="dim italic"),
                      "—","—","—","—")
    else:
        for t in recent:
            won   = t.get("won", False)
            pct   = t["pnl_pct"]
            usd   = t["pnl_usd"]
            mcap  = t.get("mcap_entry", 0)
            color = "bold green" if won else "bold red"
            icon  = "✅" if won else "❌"
            table.add_row(
                t.get("opened_str", "")[:5],
                f"{icon} {t['symbol']}",
                f"${t['entry_price']:.8f}",
                f"${t['exit_price']:.8f}",
                Text(f"{pct:+.1f}%",  style=color),
                Text(f"${usd:+.2f}", style=color),
                f"{t['hold_min']:.0f}m",
                f"${mcap:,.0f}" if mcap else "—",
                t.get("reason", "—")[:16],
            )

    return Panel(
        table,
        title=f"[bold white]📋  Últimos {n} trades cerrados",
        border_style="bright_black",
        padding=(0, 0),
    )
