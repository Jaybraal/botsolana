"""Señales compartidas entre watcher (copy) y autonomous_scanner (snipe)."""

_elite_mints: set[str] = set()


def register_elite_buy(mint: str) -> None:
    """Registra que una wallet élite compró este mint."""
    _elite_mints.add(mint)


def is_elite_signal(mint: str) -> bool:
    """¿Alguna wallet élite compró este mint?"""
    return mint in _elite_mints


def clear_mint(mint: str) -> None:
    """Limpia la señal cuando el bot ya abrió o descartó la posición."""
    _elite_mints.discard(mint)
