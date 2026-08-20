"""
Login lockout (Rev5 §5.12, gate G4.2).

In-memory single-process counter keyed by IP AND email. Both must be unlocked
for login to proceed. A successful login clears the counter for both keys.
Returns 429 + Retry-After when locked.

Storage: in-memory dict. Scale-up: Redis. No multi-worker claim.
"""
import logging
import threading
import time
from typing import Tuple

logger = logging.getLogger(__name__)

LOCKOUT_WINDOW_SECONDS = 15 * 60  # 15 minutes
MAX_FAILS_PER_WINDOW = 5

# {key: (fail_count, window_start_epoch)}
_counters: dict = {}
_lock = threading.Lock()


def _get_or_reset(key: str, now: float) -> Tuple[int, float]:
    """
    Return (count, window_start) for the given key, resetting if the window
    has expired. Thread-safe.
    """
    with _lock:
        if key in _counters:
            count, window_start = _counters[key]
            if now - window_start >= LOCKOUT_WINDOW_SECONDS:
                # Window expired — reset
                del _counters[key]
                return 0, now
            return count, window_start
        return 0, now


def is_locked(key: str) -> Tuple[bool, int]:
    """
    Returns (is_locked, retry_after_seconds).
    If locked, retry_after tells the client when they can try again.
    """
    now = time.time()
    count, window_start = _get_or_reset(key, now)
    if count >= MAX_FAILS_PER_WINDOW:
        elapsed = now - window_start
        retry_after = max(1, int(LOCKOUT_WINDOW_SECONDS - elapsed))
        return True, retry_after
    return False, 0


def check_lockout(ip: str, email: str) -> Tuple[bool, int]:
    """
    Check both IP and email keys. Returns (locked, retry_after).
    Locked if EITHER key is locked. retry_after is the max of both.
    """
    ip_key = f"lockout:ip:{ip}"
    email_key = f"lockout:email:{email.lower()}"
    ip_locked, ip_retry = is_locked(ip_key)
    email_locked, email_retry = is_locked(email_key)
    locked = ip_locked or email_locked
    retry_after = max(ip_retry, email_retry)
    return locked, retry_after


def record_failure(ip: str, email: str) -> None:
    """Increment the failure counter for both IP and email keys."""
    now = time.time()
    ip_key = f"lockout:ip:{ip}"
    email_key = f"lockout:email:{email.lower()}"
    with _lock:
        for key in (ip_key, email_key):
            if key in _counters:
                count, window_start = _counters[key]
                if now - window_start >= LOCKOUT_WINDOW_SECONDS:
                    # Window expired — start a fresh window
                    _counters[key] = (1, now)
                else:
                    _counters[key] = (count + 1, window_start)
            else:
                _counters[key] = (1, now)


def record_success(ip: str, email: str) -> None:
    """Clear the counter for both IP and email keys on successful login."""
    ip_key = f"lockout:ip:{ip}"
    email_key = f"lockout:email:{email.lower()}"
    with _lock:
        _counters.pop(ip_key, None)
        _counters.pop(email_key, None)