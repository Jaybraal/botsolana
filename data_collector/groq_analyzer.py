"""
Analiza el historial de trades con Groq para encontrar patrones predictivos.

Lee data/wallet_history.db, formatea los trades con sus features y outcomes,
y le pide a Groq que identifique qué características predicen trades ganadores.

Genera: data/groq_patterns.json — reglas usables por el scorer autónomo.

Uso:
    GROQ_API_KEY=gsk_... python3 data_collector/groq_analyzer.py
"""

import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils.logger import get_logger

log = get_logger("groq_analyzer")

DB_PATH      = "data/wallet_history.db"
PATTERNS_OUT = "data/groq_patterns.json"
MIN_TRADES   = 10  # mínimo de trades con outcome conocido para analizar


def load_trades(db_path: str) -> list[dict]:
    if not os.path.exists(db_path):
        log.error(f"DB no encontrada: {db_path}. Corre fetch_history.py primero.")
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM trades
        WHERE outcome IN ('WIN','LOSS')
        ORDER BY ts DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    log.info(f"Trades con outcome: {len(rows)}")
    wins   = sum(1 for r in rows if r["outcome"] == "WIN")
    losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    log.info(f"  WIN: {wins} | LOSS: {losses}")
    return rows


def format_for_groq(trades: list[dict]) -> str:
    """Convierte trades a texto estructurado para el prompt."""
    lines = []
    for t in trades:
        ts_str = datetime.fromtimestamp(t["ts"]).strftime("%Y-%m-%d %H:%M") if t["ts"] else "?"
        lines.append(
            f"- [{t['outcome']}] {t['wallet_label']} | {t.get('token_symbol','?')} | "
            f"programa:{t.get('program','?')} | "
            f"edad:{t.get('token_age_min','?')}min | "
            f"liq:${t.get('liquidity_usd') or 0:.0f} | "
            f"mcap:${t.get('mcap_usd') or 0:.0f} | "
            f"SOL:{t.get('sol_spent','?')} | "
            f"cambio5m:{t.get('price_change_5m','?')}% | "
            f"cambio1h:{t.get('price_change_1h','?')}% | "
            f"compras5m:{t.get('buys_5m','?')} | "
            f"ventas5m:{t.get('sells_5m','?')} | "
            f"hold:{t.get('hold_min','?')}min | "
            f"pnl:{t.get('pnl_pct','?')}% | "
            f"fecha:{ts_str}"
        )
    return "\n".join(lines)


def analyze_with_groq(trades: list[dict], api_key: str) -> dict:
    from groq import Groq

    client = Groq(api_key=api_key)

    # Dividir por wallet para análisis individual + combinado
    wallets = {}
    for t in trades:
        w = t.get("wallet_label", "Unknown")
        wallets.setdefault(w, []).append(t)

    all_patterns = {}

    for wallet_label, wallet_trades in wallets.items():
        if len(wallet_trades) < MIN_TRADES:
            log.info(f"  {wallet_label}: {len(wallet_trades)} trades — insuficiente, saltando")
            continue

        wins   = [t for t in wallet_trades if t["outcome"] == "WIN"]
        losses = [t for t in wallet_trades if t["outcome"] == "LOSS"]

        if not wins or not losses:
            log.info(f"  {wallet_label}: solo {len(wins)}W/{len(losses)}L — sin contraste suficiente")
            continue

        # Samplear para mantenerse bajo el límite de tokens de Groq free (12k TPM)
        SAMPLE = 25
        sampled_wins   = random.sample(wins,   min(SAMPLE, len(wins)))
        sampled_losses = random.sample(losses, min(SAMPLE, len(losses)))
        sample         = sampled_wins + sampled_losses
        random.shuffle(sample)

        log.info(f"  Analizando {wallet_label}: {len(wins)}W / {len(losses)}L (muestra {len(sample)} trades)...")

        trades_text = format_for_groq(sample)

        prompt = f"""Eres un experto en trading de tokens de Solana (Pump.fun, PumpSwap, Raydium).

Analiza estos {len(wallet_trades)} trades de la wallet "{wallet_label}" y encuentra patrones predictivos.

TRADES (formato: [WIN/LOSS] wallet | token | programa | edad_token | liquidez | mcap | SOL_gastados | cambio_precio_5m | cambio_precio_1h | compras_5m | ventas_5m | hold_tiempo | pnl | fecha):

{trades_text}

TAREA:
1. Identifica las características de los tokens que resultaron en WIN vs LOSS
2. Genera reglas CONCRETAS con valores numéricos (umbrales) que separen wins de losses
3. Da un score de confianza (0-100) para cada regla

Responde SOLO con JSON válido (sin markdown), estructura exacta:
{{
  "wallet": "{wallet_label}",
  "win_rate": <float 0-1>,
  "total_trades": <int>,
  "patterns": {{
    "buy_signals": [
      {{
        "feature": "<nombre_feature>",
        "condition": "<mayor_que|menor_que|entre|igual>",
        "value": <valor_o_[min,max]>,
        "confidence": <0-100>,
        "description": "<explicación en español>"
      }}
    ],
    "avoid_signals": [
      {{
        "feature": "<nombre_feature>",
        "condition": "<condición>",
        "value": <valor>,
        "confidence": <0-100>,
        "description": "<explicación>"
      }}
    ],
    "best_program": "<Pump.fun|PumpSwap|Raydium|cualquiera>",
    "ideal_token_age_min": {{"min": <int>, "max": <int>}},
    "ideal_liquidity_usd": {{"min": <int>, "max": <int>}},
    "ideal_mcap_usd": {{"min": <int>, "max": <int>}},
    "summary": "<resumen en 2 frases de cuándo copiar esta wallet>"
  }}
}}"""

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2000,
            )
            raw = response.choices[0].message.content.strip()

            # Limpiar markdown si viene envuelto
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            pattern = json.loads(raw)
            all_patterns[wallet_label] = pattern
            log.info(f"  ✅ {wallet_label}: patrón generado")

        except json.JSONDecodeError as e:
            log.warning(f"  ⚠️  {wallet_label}: Groq devolvió JSON inválido — {e}")
        except Exception as e:
            log.warning(f"  ⚠️  {wallet_label}: error Groq — {e}")

        time.sleep(6)  # respetar límite 12k TPM de Groq free tier

    # Análisis combinado: qué wallets copiar y cuándo
    if len(all_patterns) >= 2:
        log.info("\n  Generando análisis combinado...")
        summaries = "\n".join([
            f"- {w}: {p.get('patterns', {}).get('summary', 'N/A')}"
            for w, p in all_patterns.items()
        ])

        combined_prompt = f"""Dado este análisis de wallets de copy trading en Solana:

{summaries}

Genera una estrategia unificada respondiendo SOLO con JSON válido:
{{
  "recommended_wallets": ["<wallet1>", "<wallet2>"],
  "avoid_wallets": ["<wallet>"],
  "universal_filters": {{
    "min_liquidity_usd": <int>,
    "max_token_age_min": <int>,
    "preferred_programs": ["<programa>"],
    "min_buys_5m": <int>
  }},
  "strategy_summary": "<resumen de la estrategia óptima en 3 frases>"
}}"""

        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": combined_prompt}],
                temperature=0.1,
                max_tokens=800,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            all_patterns["_combined"] = json.loads(raw.strip())
            log.info("  ✅ Análisis combinado generado")
        except Exception as e:
            log.warning(f"  ⚠️  Análisis combinado falló: {e}")

    return all_patterns


def save_patterns(patterns: dict, output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(patterns, f, indent=2, ensure_ascii=False)
    log.info(f"\n✅ Patrones guardados en {output_path}")


def print_summary(patterns: dict):
    print("\n" + "═" * 60)
    print("RESUMEN DE PATRONES ENCONTRADOS")
    print("═" * 60)

    for wallet, data in patterns.items():
        if wallet == "_combined":
            continue
        p = data.get("patterns", {})
        wr = data.get("win_rate", 0)
        total = data.get("total_trades", 0)
        print(f"\n📊 {wallet} — {total} trades | WR: {wr*100:.1f}%")
        print(f"   Resumen: {p.get('summary', 'N/A')}")
        print(f"   Programa ideal: {p.get('best_program', '?')}")
        age = p.get('ideal_token_age_min', {})
        liq = p.get('ideal_liquidity_usd', {})
        print(f"   Edad token: {age.get('min','?')}–{age.get('max','?')} min")
        print(f"   Liquidez:   ${liq.get('min','?'):,}–${liq.get('max','?'):,}")

    if "_combined" in patterns:
        c = patterns["_combined"]
        print("\n🎯 ESTRATEGIA COMBINADA:")
        print(f"   Wallets recomendadas: {c.get('recommended_wallets', [])}")
        print(f"   Evitar: {c.get('avoid_wallets', [])}")
        uf = c.get('universal_filters', {})
        print(f"   Liquidez mínima: ${uf.get('min_liquidity_usd', '?'):,}")
        print(f"   Edad máx token: {uf.get('max_token_age_min', '?')} min")
        print(f"   Programas: {uf.get('preferred_programs', [])}")
        print(f"\n   {c.get('strategy_summary', '')}")

    print("═" * 60)


if __name__ == "__main__":
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        api_key = input("Pega tu GROQ_API_KEY: ").strip()

    trades = load_trades(DB_PATH)
    if len(trades) < MIN_TRADES:
        log.warning(f"Solo {len(trades)} trades con outcome. Necesitas al menos {MIN_TRADES}.")
        log.warning("Corre fetch_history.py primero y espera a que se calculen los outcomes.")
        sys.exit(1)

    patterns = analyze_with_groq(trades, api_key)

    if not patterns:
        log.warning("No se pudieron generar patrones. Revisa los logs.")
        sys.exit(1)

    save_patterns(patterns, PATTERNS_OUT)
    print_summary(patterns)
