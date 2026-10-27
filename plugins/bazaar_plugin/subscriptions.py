"""Transactional watches; import the original JSON once without modifying its backup."""
import json
from pathlib import Path
import sqlite3

SUBS_FILE = Path(__file__).parent / 'cache' / 'subscriptions.json'


def connect():
    path = SUBS_FILE.with_suffix('.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    try:
        db.execute('PRAGMA busy_timeout=15000')
        db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS subscriptions (
                chat TEXT NOT NULL, identity TEXT NOT NULL, username TEXT NOT NULL,
                PRIMARY KEY (chat, identity));
            CREATE TABLE IF NOT EXISTS deliveries (
                chat TEXT NOT NULL, report_key TEXT NOT NULL, page INTEGER NOT NULL,
                path TEXT NOT NULL, fallback TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (chat, report_key, page));
        ''')
        db.execute('BEGIN IMMEDIATE')
        if not db.execute("SELECT 1 FROM settings WHERE key='json_imported'").fetchone():
            if SUBS_FILE.is_file():
                data = json.loads(SUBS_FILE.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or any(not isinstance(v, list) or any(not isinstance(u, str) for u in v) for v in data.values()):
                    raise ValueError('旧订阅文件格式不正确，请检查 subscriptions.json')
                for chat, names in data.items():
                    for name in names:
                        name = name.strip()
                        if name:
                            db.execute('INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)', (chat, name.casefold(), name))
            db.execute("INSERT INTO settings VALUES ('json_imported','1')")
        db.commit()
        return db
    except Exception:
        db.rollback()
        db.close()
        raise


def add_subscription(chat_key: str, username: str) -> bool:
    username = username.strip()
    if not username:
        raise ValueError('请提供用户名')
    db = connect()
    try:
        with db:
            return bool(db.execute('INSERT OR IGNORE INTO subscriptions VALUES (?,?,?)',
                                   (chat_key, username.casefold(), username)).rowcount)
    finally:
        db.close()


def remove_subscription(chat_key: str, username: str) -> bool:
    db = connect()
    try:
        with db:
            return bool(db.execute('DELETE FROM subscriptions WHERE chat=? AND identity=?',
                                   (chat_key, username.strip().casefold())).rowcount)
    finally:
        db.close()


def get_subscriptions(chat_key: str) -> list[str]:
    db = connect()
    try:
        return [row[0] for row in db.execute('SELECT username FROM subscriptions WHERE chat=? ORDER BY rowid', (chat_key,))]
    finally:
        db.close()


def get_all_subscriptions() -> dict[str, list[str]]:
    db = connect()
    try:
        result = {}
        for chat, name in db.execute('SELECT chat,username FROM subscriptions ORDER BY rowid'):
            result.setdefault(chat, []).append(name)
        return result
    finally:
        db.close()


def delivery_pages(chat, report_key):
    db = connect()
    try:
        return list(db.execute('SELECT page,path,fallback,state,attempts,updated FROM deliveries WHERE chat=? AND report_key=? ORDER BY page', (chat, report_key)))
    finally:
        db.close()


def prepare_delivery(chat, report_key, paths, fallback):
    """Freeze pagination for this group/date; later subscription edits affect tomorrow."""
    db = connect()
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM deliveries WHERE chat=? AND report_key=?', (chat, report_key)).fetchone():
                texts = fallback if isinstance(fallback, list) else [fallback]*len(paths)
                if len(texts) != len(paths):
                    raise ValueError('发送页数与兜底文本页数不一致')
                db.executemany('INSERT INTO deliveries(chat,report_key,page,path,fallback) VALUES (?,?,?,?,?)',
                               [(chat, report_key, i, path, texts[i-1]) for i, path in enumerate(paths, 1)])
    finally:
        db.close()


def claim_delivery(chat, report_key, page, now):
    db = connect()
    try:
        with db:
            # Interrupted sends have uncertain acknowledgements; do not automatically duplicate them.
            return bool(db.execute("UPDATE deliveries SET state='sending',attempts=attempts+1,updated=? WHERE chat=? AND report_key=? AND page=? AND state IN ('pending','failed') AND attempts<3 AND (attempts=0 OR updated<=?)",
                                   (now, chat, report_key, page, now-60)).rowcount)
    finally:
        db.close()


def finish_delivery(chat, report_key, page, now, success):
    db = connect()
    try:
        with db:
            db.execute('UPDATE deliveries SET state=?,updated=? WHERE chat=? AND report_key=? AND page=?',
                       ('sent' if success else 'failed', now, chat, report_key, page))
    finally:
        db.close()
