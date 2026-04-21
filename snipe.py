"""
Bot Sniper — tokens nuevos en Solana via DexScreener (simulación por defecto).
Detecta lanzamientos con MCap en rango, simula entrada/salida y mide el win rate.

Comandos:
  python3 snipe.py           → loop normal
  python3 snipe.py --analyze → reporte completo de todos los trades y sale
"""

import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from rich.table   import Table
from rich.panel   import Panel
from rich.text    import Text
from rich.align   import Align
from rich.rule    import Rule
from rich         import box

from config import (
    SNIPER_MAX_POSITIONS, SNIPER_POLL_SEC,
    SNIPER_PROFIT_PCT, SNIPER_STOP_PCT, SNIPER_MAX_HOLD_MIN,
    SNIPER_AMOUNT_USD, SNIPER_MIN_MCAP, SNIPER_MAX_MCAP,
    SNIPER_MAX_TOKEN_AGE, SNIPER_TRAIL_START, SNIPER_TRAIL_DIST,
    WALLET_PUBKEY,
)
from sniper.scout      import find_opportunities, mark_seen
from sniper.positions  import check_exits, count as count_positions, get_all as get_positions
from sniper.trader     import buy_token, sell_token
from sniper.stats      import summary_panel, history_panel, load_trades
from sniper.analytics  import full_report
from sniper            import price_cache
from utils.logger      import get_logger, console

log = get_logger("snipe")

BANNER = """\
 ██████╗  ██████╗ ████████╗    ███████╗ ██████╗ ██╗      █████╗ ███╗   ██╗ █████╗
 ██╔══██╗██╔═══██╗╚══██╔══╝    ██╔════╝██╔═══██╗██║     ██╔══██╗████╗  ██║██╔══██╗
 ██████╔╝██║   ██║   ██║       ███████╗██║   ██║██║     ███████║██╔██╗ ██║███████║
 ██╔══██╗██║   ██║   ██║       ╚════██║██║   ██║██║     ██╔══██║██║╚██╗██║██╔══██║
 ██████╔╝╚██████╔╝   ██║       ███████║╚██████╔╝███████╗██║  ██║██║ ╚████║██║  ██║
 ╚═════╝  ╚═════╝    ╚═╝       ╚══════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝"""


# ── Paneles de UI ──────────────────────────────────────────────────────────

def _config_panel() -> Panel:
    grid = Table.grid(expand=True, padding=(0, 3))
    grid.add_column(); grid.add_column(); grid.add_column(); grid.add_column()

    def kv(k, v, vc="cyan"):
        return Text.assemble((f"{k}: ", "dim white"), (str(v), f"bold {vc}"))

    mode = (
        Text(" ◉ SIMULACIÓN ", style="bold black on yellow")
        if not WALLET_PUBKEY
        else Text(" ◉ EN VIVO ", style="bold black on green")
    )

    grid.add_row(
        kv("Capital/trade", f"${SNIPER_AMOUNT_USD}", "green"),
        kv("Take profit",   f"+{SNIPER_PROFIT_PCT}%","green"),
        kv("Stop loss",     f"-{SNIPER_STOP_PCT}%",  "red"),
        kv("Hold máx",      f"{SNIPER_MAX_HOLD_MIN}min","yellow"),
    )
    grid.add_row(
        kv("MCap",       f"${SNIPER_MIN_MCAP:,.0f}–${SNIPER_MAX_MCAP:,.0f}", "magenta"),
        kv("Edad",       f"30min–{SNIPER_MAX_TOKEN_AGE}h",    "magenta"),
        kv("Poll",       f"{SNIPER_POLL_SEC}s",               "cyan"),
        Text.assemble(("Modo: ", "dim white"), mode),
    )
    return Panel(grid, title="[bold white]⚙  Configuración", border_style="bright_black", padding=(0, 1))


def _positions_panel(cache: dict) -> Panel:
    positions = get_positions()
    trades    = load_trades()
    n_wins    = sum(1 for t in trades if t.get("won"))
    wr_str    = f"{n_wins/len(trades)*100:.0f}%" if trades else "—"

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 1),
    )
    table.add_column("Token",    style="bold cyan", width=8)
    table.add_column("Entrada",                    width=13, justify="right")
    table.add_column("P&L live",                   width=13, justify="right")
    table.add_column("Pico",   style="dim green",  width=8,  justify="right")
    table.add_column("Trail SL",                   width=10, justify="right")
    table.add_column("1h",                         width=7,  justify="right")
    table.add_column("6h",                         width=7,  justify="right")
    table.add_column("MCap",   style="dim white",  width=10, justify="right")
    table.add_column("Hold",   style="dim white",  width=7,  justify="right")

    if not positions:
        table.add_row(
            Text("sin posiciones abiertas", style="dim italic"),
            *[Text("—", style="dim")] * 8,
        )
    else:
        for addr, pos in positions.items():
            entry    = pos["entry_price"]
            hold_min = (time.time() - pos["opened_at"]) / 60
            mcap     = pos.get("mcap_entry", 0)
            ch_1h    = pos.get("change_1h",  0)
            ch_6h    = pos.get("change_6h",  0)

            c            = cache.get(addr)
            pnl_pct      = c["pnl_pct"]      if c else 0.0
            peak_pct     = c["peak_pct"]     if c else 0.0
            trail_sl     = c["trail_sl"]     if c else -SNIPER_STOP_PCT
            trail_active = c["trail_active"] if c else False

            if pnl_pct >= 0:
                frac   = min(pnl_pct / max(SNIPER_TRAIL_START, 1), 1.0)
                filled = int(frac * 6)
                bar    = f"[green]{'█'*filled}[/][dim]{'░'*(6-filled)}[/]"
                pnl_mk = f"[bold green]+{pnl_pct:.1f}%[/] {bar}"
            else:
                frac   = min(abs(pnl_pct) / SNIPER_STOP_PCT, 1.0)
                filled = int(frac * 6)
                bar    = f"[red]{'█'*filled}[/][dim]{'░'*(6-filled)}[/]"
                pnl_mk = f"[bold red]{pnl_pct:.1f}%[/] {bar}"

            trail_mk = (
                f"[bold yellow]{trail_sl:+.1f}% 🔒[/]"
                if trail_active
                else f"[dim]-{SNIPER_STOP_PCT:.0f}%[/]"
            )

            table.add_row(
                pos["symbol"],
                f"${entry:.8f}",
                Text.from_markup(pnl_mk),
                f"+{peak_pct:.1f}%",
                Text.from_markup(trail_mk),
                Text(f"{ch_1h:+.0f}%", style="green" if ch_1h >= 0 else "red"),
                Text(f"{ch_6h:+.0f}%", style="green" if ch_6h >= 0 else "red"),
                f"${mcap:,.0f}" if mcap else "—",
                f"{hold_min:.0f}min",
            )

    wr_color = "green" if trades and n_wins / len(trades) >= 0.5 else "red"
    title = (
        f"[bold white]📊  Posiciones [cyan]({len(positions)}/{SNIPER_MAX_POSITIONS})[/]"
        f"  ·  Cerradas [white]{len(trades)}[/]"
        f"  ·  Win Rate [{wr_color}]{wr_str}[/]"
    )
    return Panel(table, title=title, border_style="blue", padding=(0, 0))


def _countdown(remaining: int, total: int, last_event: str, cycle: int) -> str:
    filled = int((total - remaining) / total * 32)
    bar    = "[cyan]" + "━" * filled + "[/][dim]" + "─" * (32 - filled) + "[/]"
    now    = datetime.now().strftime("%H:%M:%S")
    return (
        f" [dim]{now}[/]  Ciclo [bold cyan]#{cycle}[/]  {bar}  "
        f"[cyan]{remaining}s[/]  [dim]{last_event}[/]"
    )


# ── Loop principal ──────────────────────────────────────────────────────────

def run():
    console.print(Text(BANNER, style="bold cyan"))
    console.print(Align(
        Text("Token Scout · MCap Entry · Simulación", style="bold white"),
        align="center",
    ))
    console.print()

    cycle      = 0
    last_event = "Iniciando scanner..."

    while True:
        cycle += 1

        # ── 1. Actualizar precios de posiciones abiertas ────────
        positions = get_positions()
        if positions:
            cache = price_cache.update_all(positions)
        else:
            cache = {}

        # ── 2. Revisar salidas con el cache ya actualizado ──────
        exits = check_exits()
        for signal in exits:
            sell_token(signal)
            last_event = f"CIERRE {signal.reason}"

        # ── 3. Buscar nuevos tokens ──────────────────────────────
        n_pos = count_positions()
        if n_pos < SNIPER_MAX_POSITIONS:
            slots        = SNIPER_MAX_POSITIONS - n_pos
            already_open = set(get_positions().keys())
            opps         = find_opportunities(exclude=already_open)

            if opps:
                for opp in opps[:slots]:
                    mark_seen(opp["token_address"])
                    ok = buy_token(opp)
                    if ok:
                        last_event = (
                            f"ENTRADA {opp['symbol']} "
                            f"${opp['mcap']:,.0f} "
                            f"{opp['age_hours']:.1f}h"
                        )
                        n_pos += 1
                        if n_pos >= SNIPER_MAX_POSITIONS:
                            break
            elif not last_event.startswith("ENTRADA") and not last_event.startswith("CIERRE"):
                last_event = "Esperando tokens nuevos..."

        # ── 4. Render ────────────────────────────────────────────
        # Refresh del cache tras posibles nuevas entradas
        cache = price_cache.update_all(get_positions())

        console.print(Rule(style="bright_black"))
        console.print(_config_panel())
        console.print(_positions_panel(cache))
        console.print(summary_panel())
        console.print(history_panel(n=6))

        # ── 5. Countdown ─────────────────────────────────────────
        for remaining in range(SNIPER_POLL_SEC, 0, -1):
            console.print(
                Text.from_markup(_countdown(remaining, SNIPER_POLL_SEC, last_event, cycle)),
                end="\r",
            )
            time.sleep(1)
        console.print()


# ── Modo análisis ───────────────────────────────────────────────────────────

def analyze():
    console.print(Text(BANNER, style="bold cyan"))
    console.print()
    full_report(console)


if __name__ == "__main__":
    if "--analyze" in sys.argv:
        try:
            analyze()
        except KeyboardInterrupt:
            pass
    else:
        try:
            run()
        except KeyboardInterrupt:
            console.print()
            trades = load_trades()
            if trades:
                console.print(Rule("[yellow]Resumen final[/]", style="yellow"))
                full_report(console)
            console.print(Panel(
                f"[bold yellow]Bot detenido.[/]  [dim]{len(trades)} trades en data/trades.json[/]",
                border_style="yellow", padding=(1, 4),
            ))
