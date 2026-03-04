"""Shared helpers for advisor module and skills."""

import logging
import os
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.utils import load_config, KOAN_ROOT, INSTANCE_DIR, atomic_write

logger = logging.getLogger("advisor.helpers")

ADVISOR_DIR = INSTANCE_DIR / "advisor"

# ── Constants ────────────────────────────────────────────────────────

DET_DUPLICATION_GITLAB = "duplication-gitlab"
DET_DUPLICATION_MCP = "duplication-mcp"
DET_CONVERGENCE_CITIZEN = "convergence-citizen"

STATUS_PENDING = "pending"
STATUS_NOTIFIED = "notified"
STATUS_FALSE_POSITIVE = "false_positive"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RELEVANT = "relevant"
STATUS_IGNORE = "ignore"

PLATFORM_GITHUB = "github"
PLATFORM_GITLAB = "gitlab"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"


# ── Config ───────────────────────────────────────────────────────────

def get_advisor_config() -> dict:
    """Load the advisor section from config.yaml."""
    return load_config().get("advisor", {})


def get_instance_dir() -> Path:
    """Return the path to instance/advisor/."""
    ADVISOR_DIR.mkdir(parents=True, exist_ok=True)
    return ADVISOR_DIR


def get_db_path() -> Path:
    """Return the path to advisor.db (SQLite)."""
    return get_instance_dir() / "advisor.db"


def get_litellm_credentials() -> tuple[str, str]:
    """Return (base_url, api_key) for the LiteLLM proxy."""
    bc_config = load_config().get("budget_controller", {})
    base_url = bc_config.get("litellm_url", "http://litellm-proxy:4000")
    api_key = os.environ.get(
        bc_config.get("litellm_master_key_env", "LITELLM_MASTER_KEY"), ""
    )
    return base_url, api_key


# ── YAML persistence ────────────────────────────────────────────────

def save_yaml(path: Path, data: dict) -> None:
    """Save YAML data atomically to prevent corruption."""
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    atomic_write(path, content)


def _load_yaml(path: Path, default: dict | None = None) -> dict:
    """Load YAML data from a file, returning default on error."""
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path) as f:
            return yaml.safe_load(f) or default
    except (yaml.YAMLError, OSError) as e:
        logger.error("Error loading %s: %s", path, e)
        return default


def load_repo_index() -> list[dict]:
    """Load repo_index.yaml."""
    data = _load_yaml(ADVISOR_DIR / "repo_index.yaml", {"repos": []})
    return data.get("repos", [])


def save_repo_index(repos: list[dict]) -> None:
    """Save repo_index.yaml atomically."""
    save_yaml(ADVISOR_DIR / "repo_index.yaml", {"repos": repos})


def load_mcp_catalog() -> list[dict]:
    """Load mcp_catalog.yaml."""
    data = _load_yaml(ADVISOR_DIR / "mcp_catalog.yaml", {"resources": []})
    return data.get("resources", [])


def save_mcp_catalog(resources: list[dict]) -> None:
    """Save mcp_catalog.yaml atomically."""
    save_yaml(ADVISOR_DIR / "mcp_catalog.yaml", {"resources": resources})


# ── Embedding serialization ─────────────────────────────────────────

def serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize an embedding list to bytes for SQLite-vec storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def upsert_vec_embedding(conn: sqlite3.Connection, table: str,
                         column: str, row_id: int,
                         embedding: list[float]) -> None:
    """Insert or replace an embedding in a vec0 virtual table."""
    blob = serialize_embedding(embedding)
    existing = conn.execute(
        f"SELECT rowid FROM {table} WHERE rowid=?", (row_id,)
    ).fetchone()
    if existing:
        conn.execute(f"DELETE FROM {table} WHERE rowid=?", (row_id,))
    conn.execute(
        f"INSERT INTO {table} (rowid, {column}) VALUES (?, ?)",
        (row_id, blob),
    )


# ── LLM functions ───────────────────────────────────────────────────

def _get_litellm_breaker():
    """Get the LiteLLM circuit breaker (or None if unavailable)."""
    try:
        from app.circuit_breakers import get_breaker
        return get_breaker("litellm")
    except ImportError:
        return None


def summarize_with_llm(prompt: str, config: dict) -> str:
    """Call the LLM (via LiteLLM proxy) to summarize content.

    Args:
        prompt: complete prompt to send
        config: advisor config section

    Returns:
        LLM response text, or empty string on failure
    """
    import requests as req

    model = config.get("summary_model", "claude-haiku-4-5-20251001")
    base_url, api_key = get_litellm_credentials()
    breaker = _get_litellm_breaker()

    try:
        call_fn = breaker.call if breaker else lambda fn, *a, **kw: fn(*a, **kw)
        resp = call_fn(
            req.post,
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.2,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        try:
            from pybreaker import CircuitBreakerError
            if isinstance(e, CircuitBreakerError):
                logger.warning("LiteLLM circuit breaker open, skipping summarization")
                return ""
        except ImportError:
            pass
        logger.error("LLM summarization failed: %s", e)
        return ""


def call_llm_judge(prompt: str, config: dict) -> tuple[float, str]:
    """Call the LLM judge (via LiteLLM proxy) and parse JSON response.

    Args:
        prompt: complete judge prompt
        config: advisor config section

    Returns:
        (confidence_score 0-1, explanation) or (0.0, "") on failure
    """
    import json
    import requests as req

    model = config.get("judge_model", "claude-sonnet-4-6")
    base_url, api_key = get_litellm_credentials()

    breaker = _get_litellm_breaker()
    try:
        call_fn = breaker.call if breaker else lambda fn, *a, **kw: fn(*a, **kw)
        resp = call_fn(
            req.post,
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        content = content.strip("`").strip()
        if content.startswith("json"):
            content = content[4:].strip()

        result = json.loads(content)
        score = float(result.get("confidence", 0.0))
        explanation = result.get("explanation", "")
        return score, explanation
    except Exception as e:
        try:
            from pybreaker import CircuitBreakerError
            if isinstance(e, CircuitBreakerError):
                logger.warning("LiteLLM circuit breaker open, skipping judge")
                return 0.0, ""
        except ImportError:
            pass
        logger.error("LLM judge failed: %s", e)
        return 0.0, ""


def embed_text(text: str, config: dict) -> list[float]:
    """Generate an embedding via LiteLLM proxy.

    Args:
        text: text to embed
        config: advisor config section

    Returns:
        embedding vector (list of floats), or empty list on failure
    """
    import requests as req

    model = config.get("embedding_model", "voyage-code-3")
    base_url, api_key = get_litellm_credentials()

    breaker = _get_litellm_breaker()
    try:
        call_fn = breaker.call if breaker else lambda fn, *a, **kw: fn(*a, **kw)
        resp = call_fn(
            req.post,
            f"{base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": text[:8000],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        try:
            from pybreaker import CircuitBreakerError
            if isinstance(e, CircuitBreakerError):
                logger.warning("LiteLLM circuit breaker open, skipping embedding")
                return []
        except ImportError:
            pass
        logger.error("Embedding generation failed: %s", e)
        return []


# ── Detection functions ─────────────────────────────────────────────

def load_detections(days: int = 30) -> list[dict]:
    """Load recent detections from detections.yaml."""
    data = _load_yaml(ADVISOR_DIR / "detections.yaml", {"detections": []})
    all_dets = data.get("detections", [])

    if days <= 0:
        return all_dets

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [d for d in all_dets if d.get("created_at", "") >= cutoff]


def save_detections(detections: list[dict]) -> None:
    """Save detections to detections.yaml (append mode).

    Merges new detections with existing ones, deduplicating by id.
    """
    existing = _load_yaml(ADVISOR_DIR / "detections.yaml", {"detections": []})
    existing_dets = existing.get("detections", [])
    existing_ids = {d.get("id") for d in existing_dets}

    for det in detections:
        if det.get("id") not in existing_ids:
            existing_dets.append(det)
            existing_ids.add(det.get("id"))

    save_yaml(ADVISOR_DIR / "detections.yaml", {"detections": existing_dets})


def load_detection_history() -> dict:
    """Load detection_history.yaml."""
    data = _load_yaml(ADVISOR_DIR / "detection_history.yaml", {"pairs": {}})
    return data.get("pairs", {})


def save_detection_history(pairs: dict) -> None:
    """Save detection_history.yaml atomically."""
    save_yaml(ADVISOR_DIR / "detection_history.yaml", {"pairs": pairs})


def is_duplicate_detection(source_repo: str, target_id: str,
                           pairs: dict | None = None) -> bool:
    """Check if a detection for this pair was notified recently (within dedup window)."""
    config = get_advisor_config()
    window_days = config.get("dedup_window_days", 7)

    if pairs is None:
        pairs = load_detection_history()
    pair_key = f"{source_repo}:{target_id}"
    entry = pairs.get(pair_key)
    if not entry:
        return False

    last_notified = entry.get("last_notified_at", "")
    if not last_notified:
        return False

    try:
        last_dt = datetime.fromisoformat(last_notified.replace("Z", "+00:00"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        return last_dt > cutoff
    except (ValueError, TypeError):
        return False


def mark_detection(detection_id: str, status: str, by: str,
                   notes: str = "", source: str = "") -> bool:
    """Update the status of a detection.

    Valid statuses: false_positive, acknowledged, relevant, ignore.
    Returns True if the detection was found and updated, False otherwise.
    """
    data = _load_yaml(ADVISOR_DIR / "detections.yaml", {"detections": []})
    dets = data.get("detections", [])
    now = datetime.now(timezone.utc).isoformat()

    for det in dets:
        if det.get("id") == detection_id:
            det["status"] = status
            det["feedback_at"] = now
            det["feedback_by"] = by
            if notes:
                det["feedback_notes"] = notes
            if source:
                det["feedback_source"] = source

            if status == STATUS_FALSE_POSITIVE:
                pair_key = f"{det.get('source_repo', '')}:{det.get('target_id', '')}"
                pairs = load_detection_history()
                entry = pairs.setdefault(pair_key, {
                    "last_notified_at": "",
                    "total_detections": 0,
                    "false_positive_count": 0,
                })
                entry["false_positive_count"] = entry.get("false_positive_count", 0) + 1
                save_detection_history(pairs)

            if status == STATUS_RELEVANT:
                pair_key = f"{det.get('source_repo', '')}:{det.get('target_id', '')}"
                pairs = load_detection_history()
                entry = pairs.setdefault(pair_key, {
                    "last_notified_at": "",
                    "total_detections": 0,
                    "false_positive_count": 0,
                    "relevant_count": 0,
                })
                entry["relevant_count"] = entry.get("relevant_count", 0) + 1
                save_detection_history(pairs)

            save_yaml(ADVISOR_DIR / "detections.yaml", data)
            return True

    return False


def update_detection_history(source_repo: str, target_id: str) -> None:
    """Record a notification in detection history for dedup tracking."""
    pairs = load_detection_history()
    pair_key = f"{source_repo}:{target_id}"
    now = datetime.now(timezone.utc).isoformat()

    entry = pairs.setdefault(pair_key, {
        "last_notified_at": "",
        "total_detections": 0,
        "false_positive_count": 0,
    })
    entry["last_notified_at"] = now
    entry["total_detections"] = entry.get("total_detections", 0) + 1

    save_detection_history(pairs)


# ── SQLite database ─────────────────────────────────────────────────

_db_conn: sqlite3.Connection | None = None


def init_db() -> sqlite3.Connection:
    """Initialize the advisor SQLite database with sqlite-vec extension.

    Creates tables if they don't exist. Caches the connection.
    """
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.execute("SELECT 1")
            return _db_conn
        except sqlite3.ProgrammingError:
            _db_conn = None

    import sqlite_vec

    db_path = get_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS file_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            summary TEXT NOT NULL,
            embedding BLOB,
            data_resources TEXT,
            indexed_at TEXT NOT NULL,
            UNIQUE(repo_id, file_path)
        );

        CREATE TABLE IF NOT EXISTS commit_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            repo_id TEXT NOT NULL,
            author TEXT NOT NULL,
            author_name TEXT,
            summary TEXT NOT NULL,
            embedding BLOB,
            heuristic_flags TEXT,
            analyzed_at TEXT NOT NULL,
            UNIQUE(event_id)
        );

        CREATE TABLE IF NOT EXISTS mcp_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource_id TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            embedding BLOB,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_file_summaries_repo ON file_summaries(repo_id);
        CREATE INDEX IF NOT EXISTS idx_commit_analyses_repo ON commit_analyses(repo_id);
        CREATE INDEX IF NOT EXISTS idx_commit_analyses_author ON commit_analyses(author);
    """)

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_file_summaries USING vec0(
            summary_embedding float[1024]
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_commit_analyses USING vec0(
            summary_embedding float[1024]
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_mcp_embeddings USING vec0(
            description_embedding float[1024]
        )
    """)

    conn.commit()
    _db_conn = conn
    return conn


def get_db() -> sqlite3.Connection:
    """Get a connection to the advisor database (creates if needed)."""
    return init_db()
