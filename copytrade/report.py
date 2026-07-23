"""Cálculo y formato del resumen diario de BotSolana (sin I/O)."""
from datetime import datetime


def compute_summary(history, balance_data, positions, now_ts, window_hours=24.0):
    window_start = now_ts - window_hours * 3600
    recent = [t for t in history if t.get("timestamp", 0) >= window_start]
    wins = [t for t in recent if t.get("won")]
    losses = [t for t in recent if not t.get("won")]
    pnl_24h = sum(t.get("pnl_usd", 0.0) for t in recent)
    best = max(recent, key=lambda t: t.get("pnl_pct", float("-inf")), default=None)
    worst = min(recent, key=lambda t: t.get("pnl_pct", float("inf")), default=None)

    balance = balance_data.get("balance", 0.0)
    initial = balance_data.get("initial", 0.0)
    roi_pct = ((balance / initial) - 1) * 100 if initial else 0.0

    return {
        "trades_24h": len(recent),
        "wins_24h": len(wins),
        "losses_24h": len(losses),
        "win_rate_24h": (len(wins) / len(recent) * 100) if recent else 0.0,
        "pnl_24h_usd": pnl_24h,
        "best_trade": best,
        "worst_trade": worst,
        "open_positions": len(positions),
        "balance": balance,
        "initial": initial,
        "roi_pct": roi_pct,
    }


def format_report_message(summary, bot_alive, now_dt):
    estado = "🟢 corriendo" if bot_alive else "🔴 proceso caído"
    fecha = now_dt.strftime("%d/%m")
    hora = now_dt.strftime("%H:%M")

    lines = [
        f"🤖 BotSolana — Reporte {fecha} {hora}",
        f"Estado: {estado}",
        f"Balance: ${summary['balance']:,.2f} (inicial ${summary['initial']:,.2f} "
        f"→ ROI {summary['roi_pct']:+.1f}%)",
    ]

    if summary["trades_24h"] > 0:
        lines.append(
            f"Últimas 24h: {summary['trades_24h']} trades | "
            f"WR {summary['win_rate_24h']:.0f}% "
            f"({summary['wins_24h']}W/{summary['losses_24h']}L) | "
            f"P&L ${summary['pnl_24h_usd']:+,.2f}"
        )
        best = summary["best_trade"]
        worst = summary["worst_trade"]
        if best:
            lines.append(
                f"Mejor: {best.get('symbol', '?')} {best.get('wallet_label', '?')} "
                f"{best.get('pnl_pct', 0):+.1f}%"
            )
        if worst:
            lines.append(
                f"Peor: {worst.get('symbol', '?')} {worst.get('wallet_label', '?')} "
                f"{worst.get('pnl_pct', 0):+.1f}%"
            )
    else:
        lines.append("Últimas 24h: sin actividad")

    lines.append(f"Posiciones abiertas: {summary['open_positions']}")
    return "\n".join(lines)
