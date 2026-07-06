"""Lock de instancia única — evita que corran varias copias de main.py a la vez."""
import fcntl
import os

_lock_handle = None  # mantener referencia viva: si se cierra, el lock se libera


def acquire_lock(path: str) -> bool:
    """Toma un flock exclusivo no bloqueante sobre `path`.

    True → somos la única instancia (el lock queda tomado de por vida del proceso).
    False → otra instancia ya tiene el lock.
    """
    global _lock_handle
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.write(str(os.getpid()))
    handle.flush()
    _lock_handle = handle
    return True
