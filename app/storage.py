import sqlite3
from pathlib import Path
from typing import List, Optional

from app.models import ScoredArticle

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "articles.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            url TEXT UNIQUE NOT NULL,
            published_at TEXT,
            relevance_score REAL,
            is_crypto_related INTEGER,
            reason TEXT,
            collected_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_articles(articles: List[ScoredArticle]) -> int:
    """url 기준 중복은 무시하고 새 기사만 저장. 저장된 개수 반환."""
    conn = sqlite3.connect(DB_PATH)
    saved = 0
    for a in articles:
        try:
            conn.execute("""
                INSERT INTO articles
                (source, source_type, title, content, url, published_at,
                 relevance_score, is_crypto_related, reason, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                a.source, a.source_type, a.title, a.content, a.url, a.published_at,
                a.relevance_score, int(a.is_crypto_related), a.reason, a.collected_at
            ))
            saved += 1
        except sqlite3.IntegrityError:
            continue  # 이미 저장된 URL
    conn.commit()
    conn.close()
    return saved


def get_articles(min_score: float = 0, source_type: Optional[str] = None, limit: int = 50) -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    query = "SELECT * FROM articles WHERE relevance_score >= ?"
    params: list = [min_score]

    if source_type:
        query += " AND source_type = ?"
        params.append(source_type)

    query += " ORDER BY collected_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
