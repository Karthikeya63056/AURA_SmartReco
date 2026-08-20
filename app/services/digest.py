"""
Digest content-fingerprint dedupe (Rev5 §5.9, gate G4.5).

Computes a stable sha256 fingerprint of a digest's content and compares it
against the last-sent fingerprint for that user. Identical digests are skipped
so a user never gets the same recommendation email twice in a row.

Storage: in-memory TTL cache (consistent with §5.12 lockout storage).
Scale-up: Redis / DB column. No multi-worker claim.
"""
import hashlib
import json
import logging
from typing import List, Optional

from app.core.cache import cache

logger = logging.getLogger(__name__)

# Store fingerprints a bit longer than the 24h digest interval
DIGEST_FP_TTL_SECONDS = 90000  # ~25 hours


def compute_digest_fingerprint(
    user_id: int,
    product_ids: List[int],
    narrative: Optional[str],
) -> str:
    """
    Compute a stable sha256 fingerprint of digest content.
    
    Deterministic: same user + same products + same narrative → same hash.
    product_ids are sorted so ordering doesn't affect the fingerprint.
    """
    payload = {
        "u": user_id,
        "p": sorted(product_ids or []),
        "n": narrative or "",
    }
    stable_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable_json.encode("utf-8")).hexdigest()


def should_send_digest(user_id: int, fingerprint: str) -> bool:
    """
    Returns True if this digest differs from the last one sent to the user.
    Returns True (send) if no prior fingerprint is recorded.
    """
    key = f"digest_fp:{user_id}"
    last_fingerprint = cache.get(key)
    if last_fingerprint is None:
        return True
    return last_fingerprint != fingerprint


def record_digest_sent(user_id: int, fingerprint: str) -> None:
    """Record that this fingerprint was sent, so the next run can dedupe."""
    key = f"digest_fp:{user_id}"
    cache.set(key, fingerprint, DIGEST_FP_TTL_SECONDS)


def is_duplicate_digest(user_id: int, product_ids: List[int], narrative: Optional[str]) -> bool:
    """
    Convenience: returns True if this exact digest was already sent recently.
    Used by the daily digest job to skip sending.
    """
    fingerprint = compute_digest_fingerprint(user_id, product_ids, narrative)
    return not should_send_digest(user_id, fingerprint)