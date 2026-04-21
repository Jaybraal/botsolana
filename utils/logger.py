import logging
import os
from datetime import datetime

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

os.makedirs("logs", exist_ok=True)

_theme = Theme({
    "logging.level.info":    "bold cyan",
    "logging.level.warning": "bold yellow",
    "logging.level.error":   "bold red",
    "logging.level.debug":   "dim white",
})

# Consola compartida para todo el proyecto
console = Console(theme=_theme, highlight=False)


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)

    rich_handler = RichHandler(
        console=console,
        show_path=False,
        rich_tracebacks=True,
        markup=True,
        log_time_format="[%H:%M:%S]",
    )
    rich_handler.setLevel(logging.INFO)

    today = datetime.now().strftime("%Y%m%d")
    fh = logging.FileHandler(f"logs/{name}_{today}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    log.addHandler(rich_handler)
    log.addHandler(fh)
    return log
