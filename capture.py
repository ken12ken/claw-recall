#!/usr/bin/env python3
"""
Claw Recall — Thought Capture Module

Shared write path for capturing thoughts from CLI, HTTP, MCP, and Telegram.
Stores thoughts with optional embeddings for semantic search.
"""

import json
import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional

# Optional: OpenAI for embeddings
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

DB_PATH = Path(__file__).parent / "convo_memory.db"
EMBEDDING_MODEL = "text-embedding-3-small"
MIN_CONTENT_LENGTH = 10  # Minimum content length to generate embedding


def _get_db() -> sqlite3.Connection:
    """Open a WAL-mode connection to the convo_memory database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _generate_embedding(text: str, client: Optional['OpenAI'] = None) -> Optional[np.ndarray]:
    """Generate a single embedding for the given text."""
    if not OPENAI_AVAILABLE or client is None:
        return None
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[text[:2000]]  # Truncate to ~1500 tokens
        )
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception as e:
        print(f"⚠️  Embedding error: {e}")
        return None


def capture_thought(
    content: str,
    source: str = 'cli',
    agent: str = None,
    metadata: dict = None,
    generate_embedding: bool = True,
    conn: sqlite3.Connection = None,
) -> dict:
    """
    Capture a thought into Claw Recall.

    Args:
        content: The thought text to capture
        source: Origin — 'cli', 'http', 'mcp', 'telegram'
        agent: Which agent captured it (e.g., 'main', 'cyrus')
        metadata: Optional JSON-serializable dict of tags/topics
        generate_embedding: Whether to generate an embedding (requires OpenAI)
        conn: Optional existing DB connection (creates one if not provided)

    Returns:
        dict with {id, content, source, agent, created_at} on success,
        or {error: str} on failure
    """
    content = content.strip()
    if not content:
        return {"error": "Empty content"}

    metadata_json = json.dumps(metadata or {})
    close_conn = False

    try:
        if conn is None:
            conn = _get_db()
            close_conn = True

        cursor = conn.execute(
            "INSERT INTO thoughts (content, source, agent, metadata) VALUES (?, ?, ?, ?)",
            (content, source, agent, metadata_json)
        )
        thought_id = cursor.lastrowid

        # Generate and store embedding
        embed_stored = False
        if generate_embedding and len(content) >= MIN_CONTENT_LENGTH:
            openai_client = OpenAI() if OPENAI_AVAILABLE else None
            embedding = _generate_embedding(content, openai_client)
            if embedding is not None:
                conn.execute(
                    "INSERT INTO thought_embeddings (thought_id, embedding, model) VALUES (?, ?, ?)",
                    (thought_id, embedding.tobytes(), EMBEDDING_MODEL)
                )
                embed_stored = True

        conn.commit()

        return {
            "id": thought_id,
            "content": content,
            "source": source,
            "agent": agent,
            "metadata": metadata or {},
            "embedded": embed_stored,
            "created_at": datetime.now().isoformat(),
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        if close_conn and conn:
            conn.close()


def list_thoughts(
    limit: int = 20,
    offset: int = 0,
    source: str = None,
    agent: str = None,
    conn: sqlite3.Connection = None,
) -> list[dict]:
    """List thoughts in reverse chronological order."""
    close_conn = False
    try:
        if conn is None:
            conn = _get_db()
            close_conn = True

        sql = "SELECT id, content, source, agent, metadata, created_at FROM thoughts WHERE 1=1"
        params = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "source": r[2],
                "agent": r[3],
                "metadata": json.loads(r[4]) if r[4] else {},
                "created_at": r[5],
            }
            for r in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        if close_conn and conn:
            conn.close()


def delete_thought(thought_id: int, conn: sqlite3.Connection = None) -> dict:
    """Delete a thought by ID."""
    close_conn = False
    try:
        if conn is None:
            conn = _get_db()
            close_conn = True

        # Delete embedding first (FK)
        conn.execute("DELETE FROM thought_embeddings WHERE thought_id = ?", (thought_id,))
        cursor = conn.execute("DELETE FROM thoughts WHERE id = ?", (thought_id,))
        conn.commit()

        if cursor.rowcount == 0:
            return {"error": f"Thought {thought_id} not found"}
        return {"deleted": thought_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if close_conn and conn:
            conn.close()


def thought_stats(conn: sqlite3.Connection = None) -> dict:
    """Get statistics about captured thoughts."""
    close_conn = False
    try:
        if conn is None:
            conn = _get_db()
            close_conn = True

        total = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        embedded = conn.execute("SELECT COUNT(*) FROM thought_embeddings").fetchone()[0]
        by_source = dict(conn.execute(
            "SELECT source, COUNT(*) FROM thoughts GROUP BY source"
        ).fetchall())
        by_agent = dict(conn.execute(
            "SELECT COALESCE(agent, 'none'), COUNT(*) FROM thoughts GROUP BY agent"
        ).fetchall())

        return {
            "total": total,
            "embedded": embedded,
            "by_source": by_source,
            "by_agent": by_agent,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if close_conn and conn:
            conn.close()
