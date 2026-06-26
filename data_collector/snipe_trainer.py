"""
Genera data/snipe_patterns.json desde wallet_history.db.
Ejecutar una vez antes de arrancar el bot:
    python3 -m data_collector.snipe_trainer
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/wallet_history.db")
OUTPUT_PATH = os.getenv("PATTERNS_PATH", "data/snipe_patterns.json")

_AGE_BUCKETS = [
    {"label": "<1min",    "min": 0,      "max": 1   },
    {"label": "1-3min",   "min": 1,      "max": 3   },
    {"label": "3-10min",  "min": 3,      "max": 10  },
    {"label": "10-30min", "min": 10,     "max": 30  },
    {"label": "30+min",   "min": 30,     "max": 9999},
]

_MCAP_BUCKETS = [
    {"label": "<10k",    "min": 0,       "max": 10000     },
    {"label": "10-30k",  "min": 10000,   "max": 30000     },
    {"label": "30-70k",  "min": 30000,   "max": 70000     },
    {"label": "70-150k", "min": 70000,   "max": 150000    },
    {"label": "150k+",   "min": 150000,  "max": 999999999 },
]

_BUYS_BUCKETS = [
    {"label": "<10",     "min": 0,   "max": 10    },
    {"label": "10-50",   "min": 10,  "max": 50    },
    {"label": "50-150",  "min": 50,  "max": 150   },
    {"label": "150-300", "min": 150, "max": 300   },
    {"label": "300+",    "min": 300, "max": 999999},
]


def _wr_to_pts(wr: float) -> int:
    """Convierte WR% a puntos de score (0-40). Derivado de los datos."""
    if wr >= 90:
        return 40
    if wr >= 87:
        return 35
    if wr >= 82:
        return 25
    if wr >= 79:
        return 15
    return 5


def _bucket_stats(cursor: sqlite3.Cursor, col: str, lo: float, hi: float) -> dict:
    cursor.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) "
        f"FROM trades "
        f"WHERE outcome IN ('WIN','LOSS') AND {col} >= ? AND {col} < ?",
        (lo, hi),
    )
    n, wins = cursor.fetchone()
    n = n or 0
    wins = wins or 0
    wr = round(wins / n * 100, 1) if n else 0.0
    return {"n": n, "wins": wins, "wr": wr}


def compute_patterns() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM trades WHERE outcome IN ('WIN','LOSS')")
    total = c.fetchone()[0]

    def _enrich(template_list: list[dict], col: str) -> list[dict]:
        result = []
        for b in template_list:
            stats = _bucket_stats(c, col, b["min"], b["max"])
            result.append({**b, **stats, "score_pts": _wr_to_pts(stats["wr"])})
        return result

    patterns = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_trades":       total,
        "age_buckets":        _enrich(_AGE_BUCKETS,  "token_age_min"),
        "mcap_buckets":       _enrich(_MCAP_BUCKETS, "mcap_usd"),
        "buys_buckets":       _enrich(_BUYS_BUCKETS, "buys_5m"),
        "elite_wallet_boost": 15,
        "buy_threshold":      55,
    }

    conn.close()
    return patterns


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"ERROR: DB no encontrada en {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    patterns = compute_patterns()
    Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(patterns, f, indent=2)

    print(f"✅ {OUTPUT_PATH} generado — {patterns['total_trades']} trades analizados")
    print("\nResumen por edad del token:")
    for b in patterns["age_buckets"]:
        print(f"  {b['label']:10s}: WR={b['wr']:5.1f}%  n={b['n']:4d}  pts={b['score_pts']}")
    print("\nResumen por mcap:")
    for b in patterns["mcap_buckets"]:
        print(f"  {b['label']:10s}: WR={b['wr']:5.1f}%  n={b['n']:4d}  pts={b['score_pts']}")


if __name__ == "__main__":
    main()
