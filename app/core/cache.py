import time
import threading
from typing import Any, Optional, Dict


class InMemoryTTLCache:
    """Thread-safe In-memory Key-Value cache with TTL (Time To Live)."""

    def __init__(self, cleanup_interval_seconds: float = 300):
        self._cache: Dict[str, tuple[float, float, Any]] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = max(10.0, float(cleanup_interval_seconds))
        # Background daemon periodically purges expired keys so the cache can't
        # grow unboundedly with low-traffic keys that nobody ever reads again.
        self._cleanup_thread = threading.Thread(
            target=self._run_background_cleanup,
            name="ttl-cache-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def _run_background_cleanup(self) -> None:
        while True:
            time.sleep(self._cleanup_interval)
            try:
                self.cleanup()
            except Exception:
                # cleanup must never kill the daemon thread
                pass

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        """Store key-value pair with TTL in seconds."""
        expiry = time.time() + ttl_seconds
        with self._lock:
            self._cache[key] = (expiry, ttl_seconds, value)

    def get(self, key: str) -> Optional[Any]:
        """Retrieve key value if present and not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            
            expiry, ttl_seconds, value = self._cache[key]
            if time.time() > expiry:
                del self._cache[key]
                return None
                
            return value

    def delete(self, key: str) -> bool:
        """Delete specific key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Clear all keys in cache."""
        with self._lock:
            self._cache.clear()

    def cleanup(self) -> None:
        """Purge expired entries."""
        now = time.time()
        with self._lock:
            keys_to_del = [k for k, (exp, _, _) in self._cache.items() if now > exp]
            for k in keys_to_del:
                del self._cache[k]


# Global cache instance
cache = InMemoryTTLCache()
