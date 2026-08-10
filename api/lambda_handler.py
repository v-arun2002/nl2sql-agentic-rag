"""
AWS Lambda entry point. Wraps the existing FastAPI app with Mangum so the
same api/main.py serves both local dev (uvicorn) and Lambda (this handler)
-- no duplicated routing logic between environments.

Lambda's filesystem is read-only outside /tmp, but Chroma's PersistentClient
needs a writable directory even for read queries (it manages WAL/lock files
internally). So: copy the pre-built index baked into the container image at
/var/task/chroma_db to /tmp/chroma_db on cold start, then point
CHROMA_PERSIST_DIR there. Lambda reuses warm execution environments, so this
copy only runs once per cold start, not per request.

This env override MUST happen before importing api.main, since that import
chain (api.main -> src.graph -> ... -> src.config) reads CHROMA_PERSIST_DIR
at module-load time.

Note: BIRD-SQL's .sqlite database files themselves are opened read-only
(SELECT-only queries in this project) and generally work fine directly from
the read-only /var/task path. If you hit filesystem errors there too, apply
the same copy-to-/tmp pattern for BENCHMARK_DATA_PATH.
"""

import os
import shutil

_BAKED_CHROMA_DIR = "/var/task/chroma_db"
_WRITABLE_CHROMA_DIR = "/tmp/chroma_db"

if os.path.isdir(_BAKED_CHROMA_DIR) and not os.path.isdir(_WRITABLE_CHROMA_DIR):
    shutil.copytree(_BAKED_CHROMA_DIR, _WRITABLE_CHROMA_DIR)
os.environ["CHROMA_PERSIST_DIR"] = _WRITABLE_CHROMA_DIR

from mangum import Mangum  # noqa: E402

from api.main import app  # noqa: E402

handler = Mangum(app)
