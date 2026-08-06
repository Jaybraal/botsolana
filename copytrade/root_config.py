"""
Config mutable que ROOT usa para decidir — nada fijo: pesos por feature,
bias, confianza por wallet, y los parámetros de riesgo (stop-loss, max-hold,
tamaño de posición) son todos genes que root_evolution.py puede mutar. Este
módulo solo tiene funciones puras: cómo se calcula un score y cómo se
serializa una config — sin I/O, sin red, fácil de testear y de razonar.
"""
import math
from dataclasses import dataclass, field

FEATURES = [
    "mcap_usd", "liquidity_usd", "volume_24h_usd", "vol_liq_ratio",
    "buy_pressure", "change_1h_pct", "buys_1h", "sells_1h", "age_days",
]


@dataclass
class Config:
    config_id: str
    weights: dict[str, float] = field(default_factory=dict)
    bias: float = 0.0
    wallet_trust: dict[str, float] = field(default_factory=dict)
    stop_loss_pct: float = 15.0
    max_hold_min: float = 30.0
    trade_usd: float = 50.0


def neutral_config(config_id: str = "champion-seed") -> Config:
    """Punto de partida sin sesgo: pesos y bias en cero → score siempre 0 →
    decide() copia todo (igual que el fallback 'sin patrón, dejar pasar' que
    ya existía). De ahí en más, root_evolution.py es quien aprende qué pesar."""
    return Config(
        config_id=config_id,
        weights={f: 0.0 for f in FEATURES},
        bias=0.0,
        wallet_trust={},
        stop_loss_pct=15.0,
        max_hold_min=30.0,
        trade_usd=50.0,
    )


def _feature_vector(entry_context: dict) -> dict[str, float]:
    """Normaliza entry_context (features crudas de DexScreener) a un vector
    en escalas comparables: log1p para magnitudes (mcap/liquidez/volumen/
    conteos de compras-ventas, que varían en órdenes de magnitud), raw para
    ratios y porcentajes ya acotados. Un feature ausente o no numérico
    aporta 0 — nunca inventa un valor."""
    ctx = entry_context or {}

    def _num(key: str) -> float:
        v = ctx.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "mcap_usd": math.log1p(max(0.0, _num("mcap_usd"))),
        "liquidity_usd": math.log1p(max(0.0, _num("liquidity_usd"))),
        "volume_24h_usd": math.log1p(max(0.0, _num("volume_24h_usd"))),
        "vol_liq_ratio": _num("vol_liq_ratio"),
        "buy_pressure": _num("buy_pressure"),
        "change_1h_pct": _num("change_1h_pct") / 100.0,
        "buys_1h": math.log1p(max(0.0, _num("buys_1h"))),
        "sells_1h": math.log1p(max(0.0, _num("sells_1h"))),
        "age_days": _num("age_days"),
    }


def score_entry_context(config: Config, wallet_label: str, entry_context: dict) -> float:
    feats = _feature_vector(entry_context)
    score = config.bias + config.wallet_trust.get(wallet_label, 0.0)
    for name in FEATURES:
        score += config.weights.get(name, 0.0) * feats[name]
    return score


def decide(config: Config, wallet_label: str, entry_context: dict) -> str:
    """score >= 0 → COPIAR. El umbral vive implícito en bias (un gen menos
    para mutar por separado, mismo poder expresivo)."""
    return "COPIAR" if score_entry_context(config, wallet_label, entry_context) >= 0.0 else "SKIP"


def config_to_dict(config: Config) -> dict:
    return {
        "config_id": config.config_id,
        "weights": dict(config.weights),
        "bias": config.bias,
        "wallet_trust": dict(config.wallet_trust),
        "stop_loss_pct": config.stop_loss_pct,
        "max_hold_min": config.max_hold_min,
        "trade_usd": config.trade_usd,
    }


def config_from_dict(d: dict) -> Config:
    return Config(
        config_id=d["config_id"],
        weights=dict(d.get("weights", {})),
        bias=d.get("bias", 0.0),
        wallet_trust=dict(d.get("wallet_trust", {})),
        stop_loss_pct=d.get("stop_loss_pct", 15.0),
        max_hold_min=d.get("max_hold_min", 30.0),
        trade_usd=d.get("trade_usd", 50.0),
    )
