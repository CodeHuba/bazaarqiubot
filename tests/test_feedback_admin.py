import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "web_runs"))

from feedback_admin import (
    delete_feedback,
    list_feedback,
    migrate_feedback_schema,
    update_feedback,
)


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            image_path TEXT,
            contact TEXT,
            likes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_id INTEGER NOT NULL,
            parent_id INTEGER,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO feedback (content, contact, likes, created_at)
        VALUES ('希望增加筛选', 'user@example.com', 3, '2026-09-24 09:00:00');
        INSERT INTO feedback (content, likes, created_at)
        VALUES ('页面有点慢', 1, '2026-09-23 09:00:00');
        INSERT INTO comments (feedback_id, content) VALUES (1, '管理员已收到');
        """
    )
    conn.commit()
    conn.close()


def test_migration_adds_admin_fields_without_losing_existing_feedback(tmp_path):
    db = tmp_path / "feedback.db"
    _seed_db(db)

    migrate_feedback_schema(db)

    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback)")}
    count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    conn.close()
    assert {"status", "admin_note", "updated_at"} <= columns
    assert count == 2


def test_admin_list_filters_status_and_returns_comment_counts(tmp_path):
    db = tmp_path / "feedback.db"
    _seed_db(db)
    migrate_feedback_schema(db)
    update_feedback(db, 2, status="resolved", admin_note="已优化")

    result = list_feedback(db, status="open", page=1, per_page=20)

    assert result["total"] == 1
    assert result["items"][0]["content"] == "希望增加筛选"
    assert result["items"][0]["comment_count"] == 1
    assert result["items"][0]["contact"] == "user@example.com"


def test_admin_update_validates_status_and_persists_note(tmp_path):
    db = tmp_path / "feedback.db"
    _seed_db(db)
    migrate_feedback_schema(db)

    with pytest.raises(ValueError, match="invalid status"):
        update_feedback(db, 1, status="deleted", admin_note="")

    item = update_feedback(db, 1, status="in_progress", admin_note="排查中")
    assert item["status"] == "in_progress"
    assert item["admin_note"] == "排查中"
    assert item["updated_at"]


def test_admin_delete_removes_feedback_comments_and_returns_image_path(tmp_path):
    db = tmp_path / "feedback.db"
    _seed_db(db)
    migrate_feedback_schema(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE feedback SET image_path='/static/uploads/a.png' WHERE id=1")
    conn.commit()
    conn.close()

    deleted = delete_feedback(db, 1)

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM feedback WHERE id=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM comments WHERE feedback_id=1").fetchone()[0] == 0
    conn.close()
    assert deleted["image_path"] == "/static/uploads/a.png"
