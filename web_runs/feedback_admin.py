import os
import sqlite3
from datetime import datetime
from pathlib import Path


FEEDBACK_STATUSES = frozenset({"open", "in_progress", "resolved", "hidden"})


def _connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_feedback_schema(db_path):
    """Create the base schema and add moderation metadata to existing databases."""
    conn = _connect(db_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            image_path TEXT,
            contact TEXT,
            likes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feedback_id INTEGER NOT NULL,
            parent_id INTEGER,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(feedback)")}
        if "status" not in columns:
            conn.execute("ALTER TABLE feedback ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")
        if "admin_note" not in columns:
            conn.execute("ALTER TABLE feedback ADD COLUMN admin_note TEXT")
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE feedback ADD COLUMN updated_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_status_time ON feedback(status, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_feedback ON comments(feedback_id)")
        conn.commit()
    finally:
        conn.close()


def list_feedback(db_path, *, status="all", search="", page=1, per_page=20):
    if status != "all" and status not in FEEDBACK_STATUSES:
        raise ValueError("invalid status")
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 100))
    where = []
    params = []
    if status != "all":
        where.append("f.status = ?")
        params.append(status)
    search = (search or "").strip()
    if search:
        where.append("(f.content LIKE ? OR COALESCE(f.contact, '') LIKE ? OR COALESCE(f.admin_note, '') LIKE ?)")
        term = f"%{search}%"
        params.extend([term, term, term])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    offset = (page - 1) * per_page

    conn = _connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM feedback f {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT f.id, f.content, f.image_path, f.contact, f.likes, f.created_at,
                   f.status, f.admin_note, f.updated_at,
                   COUNT(c.id) AS comment_count
            FROM feedback f
            LEFT JOIN comments c ON c.feedback_id = f.id
            {where_sql}
            GROUP BY f.id
            ORDER BY CASE f.status
                WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1
                WHEN 'resolved' THEN 2 ELSE 3 END,
                f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
        }
    finally:
        conn.close()


def update_feedback(db_path, feedback_id, *, status, admin_note=""):
    if status not in FEEDBACK_STATUSES:
        raise ValueError("invalid status")
    note = (admin_note or "").strip()
    if len(note) > 2000:
        raise ValueError("admin note too long")
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        updated = conn.execute(
            "UPDATE feedback SET status=?, admin_note=?, updated_at=? WHERE id=?",
            (status, note or None, updated_at, feedback_id),
        ).rowcount
        if not updated:
            raise LookupError("feedback not found")
        conn.commit()
        row = conn.execute(
            "SELECT id, content, image_path, contact, likes, created_at, status, admin_note, updated_at FROM feedback WHERE id=?",
            (feedback_id,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def delete_feedback(db_path, feedback_id):
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT image_path FROM feedback WHERE id=?", (feedback_id,)).fetchone()
        if row is None:
            raise LookupError("feedback not found")
        conn.execute("DELETE FROM comments WHERE feedback_id=?", (feedback_id,))
        conn.execute("DELETE FROM feedback WHERE id=?", (feedback_id,))
        conn.commit()
        return {"image_path": row["image_path"]}
    finally:
        conn.close()


def remove_feedback_image(image_path, upload_dir):
    if not image_path or not image_path.startswith("/static/uploads/"):
        return False
    filename = os.path.basename(image_path)
    target = Path(upload_dir) / filename
    upload_root = Path(upload_dir).resolve()
    try:
        resolved = target.resolve()
        if upload_root not in resolved.parents:
            return False
        resolved.unlink(missing_ok=True)
        return True
    except OSError:
        return False
