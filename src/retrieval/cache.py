"""
Redis cache for schema retrieval results.

Schema retrieval (embedding search over table/column descriptions) is the
same expensive-ish call every time the same db_id+question combination
recurs -- common during benchmark re-runs while iterating on prompts, since
you're re-asking the same BIRD-SQL benchmark questions repeatedly. Caching the
retrieved table list means those re-runs skip the embedding search entirely.

Falls back to "no cache" gracefully if Redis isn't running -- this is an
optimization, not a dependency the pipeline should hard-fail without.
"""

import hashlib
import json
from typing import Optional, List, Dict, Any

import redis

from src.config import settings

_client: Optional[redis.Redis] = None
_connection_failed = False


def _get_client() -> Optional[redis.Redis]:
    global _client, _connection_failed
    if _connection_failed:
        return None
    if _client is None:
        try:
            _client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1)
            _client.ping()
        except redis.exceptions.RedisError:
            _connection_failed = True
            return None
    return _client


def _cache_key(db_id: str, question: str, top_k: int) -> str:
    raw = f"{db_id}:{question}:{top_k}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"schema_retrieval:{digest}"


def get_cached_retrieval(db_id: str, question: str, top_k: int) -> Optional[List[Dict[str, Any]]]:
    client = _get_client()
    if client is None:
        return None
    try:
        cached = client.get(_cache_key(db_id, question, top_k))
        return json.loads(cached) if cached else None
    except redis.exceptions.RedisError:
        return None


def set_cached_retrieval(db_id: str, question: str, top_k: int, result: List[Dict[str, Any]], ttl_seconds: int = 3600) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.setex(_cache_key(db_id, question, top_k), ttl_seconds, json.dumps(result))
    except redis.exceptions.RedisError:
        pass
