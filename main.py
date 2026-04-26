"""
BOT SOLANA — Copy Trading
Monitorea wallets objetivo en tiempo real.
Cuando hacen un swap en Jupiter/Raydium/Orca, lo replica al instante.

Uso:
  python3 main.py

Configurar en .env:
  TARGET_WALLETS=wallet1,wallet2,wallet3
  WALLET_PUBKEY=tu_wallet
  WALLET_PRIVKEY_B58=tu_private_key
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from rich.table  import Table
from rich.panel  import Panel
from rich.text   import Text
from rich.align  import Align
from rich.rule   import Rule
from rich        import box

from config import TARGET_WALLETS, WALLET_PUBKEY, WALLET_LABELS
from copytrade.watcher import watch_all
from copytrade.learner import print_insights as print_learner_insights, load_rules
from copytrade.executor import recover_open_positions
from utils.logger import get_logger, console
import json, os

log = get_logger("main")

BANNER = """
  ██████╗ ██████╗ ██████╗ ██╗   ██╗    ████████╗██████╗  █████╗ ██████╗ ███████╗
 ██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝    ╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝
 ██║     ██║   ██║██████╔╝ ╚████╔╝        ██║   ██████╔╝███████║██║  ██║█████╗
 ██║     ██║   ██║██╔═══╝   ╚██╔╝         ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝
 ╚██████╗╚██████╔╝██║        ██║          ██║   ██║  ██║██║  ██║██████╔╝███████╗
  ╚═════╝ ╚═════╝ ╚═╝        ╚═╝          ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝"""


def _print_header():
    console.print(Text(BANNER, style="bold green"))
    console.print(Align(Text("WebSocket · Jupiter · Raydium · Orca", style="bold white"), align="center"))
    console.print()


def _print_wallets_panel():
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_black",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#",       style="dim white",   width=4,  justify="right")
    table.add_column("Nombre",  style="bold yellow", width=14)
    table.add_column("Wallet",  style="bold cyan",   width=46)

    for i, w in enumerate(TARGET_WALLETS, 1):
        nombre = WALLET_LABELS.get(w, f"{w[:8]}...{w[-4:]}")
        table.add_row(str(i), nombre, w)

    mode = (
        Text(" ◉ SIMULACIÓN ", style="bold black on yellow")
        if not WALLET_PUBKEY
        else Text(" ◉ EN VIVO ",   style="bold black on green")
    )

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column()
    grid.add_column(justify="right")
    grid.add_row(
        Text.assemble(("Wallets monitoreadas: ", "dim"), (str(len(TARGET_WALLETS)), "bold cyan")),
        mode,
    )

    panel_content = Table.grid(expand=True)
    panel_content.add_row(grid)
    panel_content.add_row(table)

    console.print(Panel(
        panel_content,
        title="[bold white]👁  Copy Trading — Wallets objetivo",
        border_style="green",
        padding=(0, 1),
    ))
    console.print()


def _print_copytrade_summary():
    path = "data/copytrades.json"
    if not os.path.exists(path):
        console.print("[dim]No hay copy trades registrados aún.[/]")
        return
    try:
        with open(path) as f:
            trades = json.load(f)
    except Exception:
        return
    if not trades:
        return

    table = Table(
        box=box.SIMPLE_HEAD, show_header=True,
        header_style="bold bright_white", border_style="bright_black",
        expand=True, padding=(0, 1),
    )
    table.add_column("Hora",      style="dim white",  width=14)
    table.add_column("Fuente",    style="bold cyan",  width=14)
    table.add_column("De",        style="yellow",     width=10)
    table.add_column("A",         style="green",      width=10)
    table.add_column("DEX",       style="dim white",  width=12)
    table.add_column("Modo",      width=10)

    for t in trades[-20:]:
        modo = "[dim]SIM[/]" if t.get("simulated") else "[bold green]LIVE[/]"
        fuente = t.get("wallet_label") or f"{t['wallet'][:8]}..."
        table.add_row(
            t.get("time_str", "—"),
            fuente,
            t.get("symbol_in",  "?"),
            t.get("symbol_out", "?"),
            t.get("program",    "?"),
            Text.from_markup(modo),
        )

    console.print(Panel(
        table,
        title=f"[bold white]📋  Copy Trades detectados [cyan]({len(trades)} total)[/]",
        border_style="green",
    ))


def main():
    _print_header()

    if not TARGET_WALLETS:
        console.print(Panel(
            "[bold red]✗  TARGET_WALLETS no configurado en .env[/]\n"
            "[dim]Ejemplo: TARGET_WALLETS=ABC123...,DEF456...[/]",
            border_style="red",
            padding=(1, 2),
        ))
        sys.exit(1)

    if not WALLET_PUBKEY:
        console.print(Panel(
            "[bold yellow]⚠  WALLET_PUBKEY no configurado — modo SIMULACIÓN[/]\n"
            "[dim]Los swaps se detectarán pero no se ejecutarán realmente.[/]",
            border_style="yellow",
            padding=(0, 2),
        ))

    _print_wallets_panel()

    # Mostrar patrones aprendidos si ya hay datos
    if load_rules():
        print_learner_insights()

    # Recuperar posiciones abiertas de reinicios previos
    if WALLET_PUBKEY:
        recover_open_positions()

    console.print(Rule("[dim]Conectando WebSocket...[/]", style="bright_black"))
    console.print()

    try:
        asyncio.run(watch_all())
    except KeyboardInterrupt:
        console.print()
        _print_copytrade_summary()
        console.print(Panel(
            "[bold yellow]Bot detenido por el usuario[/]",
            border_style="yellow",
            padding=(1, 4),
        ))


if __name__ == "__main__":
    main()
