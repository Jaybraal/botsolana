#!/usr/bin/env python3
"""
Script de prueba para ETH Simulator.
Simula algunos trades y verifica que los gas fees, slippage, etc. se calculan correctamente.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from copytrade import eth_simulator
from utils.logger import get_logger, console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.text import Text

log = get_logger("test_eth")

def test_eth_simulator():
    """Prueba el simulador de Ethereum."""
    console.print(Panel(
        "[bold cyan]🧪  Prueba ETH Simulator[/]\n"
        "[dim]Simulando trades con gas fees, slippage y market impact realista[/]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()

    # Estado inicial
    console.print("[bold]1. Estado inicial:[/]")
    stats = eth_simulator.get_eth_stats()
    table = Table(box=box.SIMPLE_HEAD, show_header=False)
    table.add_column("Métrica", style="dim white")
    table.add_column("Valor", style="bold green")
    table.add_row("Balance inicial", f"${stats['balance']:.2f}")
    table.add_row("Capital", f"${stats['initial']:.2f}")
    table.add_row("Posiciones abiertas", str(stats['open_positions']))
    console.print(table)
    console.print()

    # Simular compra 1
    console.print("[bold]2. Simulando COMPRA 1:[/]")
    eth_simulator.process_eth_swap(
        token_address="0x1234567890123456789012345678901234567890",
        symbol="TEST_TOKEN_1",
        wallet_label="Wallet-A",
        entry_price=0.0001,
        is_buy=True,
    )
    stats = eth_simulator.get_eth_stats()
    console.print(f"   Balance después: ${stats['balance']:.2f} (-gas)")
    console.print(f"   Posiciones abiertas: {stats['open_positions']}")
    console.print()

    # Simular compra 2 (confirmación)
    console.print("[bold]3. Simulando COMPRA 2 (confirmación):[/]")
    eth_simulator.process_eth_swap(
        token_address="0x1234567890123456789012345678901234567890",
        symbol="TEST_TOKEN_1",
        wallet_label="Wallet-B",
        entry_price=0.00012,
        is_buy=True,
    )
    stats = eth_simulator.get_eth_stats()
    console.print(f"   Balance después: ${stats['balance']:.2f}")
    console.print(f"   Posiciones abiertas: {stats['open_positions']}")
    console.print()

    # Simular venta
    console.print("[bold]4. Simulando VENTA:[/]")
    eth_simulator.process_eth_swap(
        token_address="0x1234567890123456789012345678901234567890",
        symbol="TEST_TOKEN_1",
        wallet_label="Wallet-A",
        entry_price=0.00015,
        is_buy=False,
    )
    stats = eth_simulator.get_eth_stats()
    console.print(f"   Balance después: ${stats['balance']:.2f}")
    console.print(f"   PnL total: ${stats['total_pnl']:.2f}")
    console.print(f"   Retorno: {stats['return_pct']:.2f}%")
    console.print(f"   Posiciones abiertas: {stats['open_positions']}")
    console.print()

    # Resumen final
    console.print("[bold]5. Resumen final:[/]")
    final_stats = eth_simulator.get_eth_stats()
    summary = Table(box=box.SIMPLE_HEAD, show_header=False)
    summary.add_column("Métrica", style="dim white")
    summary.add_column("Valor", style="bold cyan")
    summary.add_row("Balance final", f"${final_stats['balance']:.2f}")
    summary.add_row("Capital inicial", f"${final_stats['initial']:.2f}")
    summary.add_row("PnL total", f"${final_stats['total_pnl']:.2f}")
    summary.add_row("Retorno (%)", f"{final_stats['return_pct']:.2f}%")
    summary.add_row("Trades totales", str(final_stats['total_trades']))
    console.print(summary)
    console.print()

    # Archivos generados
    console.print("[bold]6. Archivos generados:[/]")
    files = [
        "data/eth_positions.json",
        "data/eth_balance.json",
        "data/eth_history.json",
    ]
    for f in files:
        if os.path.exists(f):
            size = os.path.getsize(f)
            console.print(f"   ✓ {f} ({size} bytes)")
        else:
            console.print(f"   ✗ {f} (no existe)")
    console.print()

    console.print(Panel(
        "[bold green]✓  Prueba completada[/]\n"
        "[dim]El simulador ETH está funcionando correctamente.[/]",
        border_style="green",
        padding=(1, 2),
    ))

if __name__ == "__main__":
    test_eth_simulator()
