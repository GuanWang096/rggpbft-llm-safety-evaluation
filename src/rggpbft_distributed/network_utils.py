import socket
import threading
import time


_cache = {}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def resolve_host(host, attempts=30):
    with _lock:
        cached = _cache.get(host)
    if cached:
        return cached

    last_error = None
    for attempt in range(attempts):
        try:
            address = socket.gethostbyname(host)
            with _lock:
                _cache[host] = address
            return address
        except socket.gaierror as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.1 * (attempt + 1), 1.0))
    raise last_error
