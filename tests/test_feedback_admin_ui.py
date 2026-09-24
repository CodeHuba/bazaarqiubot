from pathlib import Path


STATS_HTML = Path(__file__).parents[1] / "web_runs" / "static" / "stats.html"
APP_PY = Path(__file__).parents[1] / "web_runs" / "app.py"


def test_admin_dashboard_exposes_feedback_management_controls():
    html = STATS_HTML.read_text(encoding="utf-8")
    assert "意见反馈管理" in html
    assert "openFeedbackAdminModal()" in html
    assert "/api/admin/feedback" in html
    assert "feedback-admin-list" in html
    for label in ("待处理", "处理中", "已解决", "已隐藏"):
        assert label in html


def test_feedback_admin_routes_require_existing_admin_auth():
    source = APP_PY.read_text(encoding="utf-8")
    assert "@app.route('/api/admin/feedback', methods=['GET'])" in source
    assert "@app.route('/api/admin/feedback/<int:fid>', methods=['PUT'])" in source
    assert "@app.route('/api/admin/feedback/<int:fid>', methods=['DELETE'])" in source
    route_section = source[source.index("@app.route('/api/admin/feedback', methods=['GET'])"):]
    assert route_section.count("@require_stats_auth") >= 3


def test_public_feedback_list_excludes_hidden_and_does_not_leak_admin_notes():
    source = APP_PY.read_text(encoding="utf-8")
    public_section = source[
        source.index("@app.route('/api/feedback', methods=['GET'])"):
        source.index("@app.route('/api/feedback', methods=['POST'])")
    ]
    assert "COALESCE(status, 'open') != 'hidden'" in public_section
    assert "admin_note" not in public_section
    assert "contact" not in public_section


def test_public_feedback_post_uses_a_field_allowlist():
    source = APP_PY.read_text(encoding="utf-8")
    post_section = source[
        source.index("@app.route('/api/feedback', methods=['POST'])"):
        source.index("@app.route('/api/admin/feedback', methods=['GET'])")
    ]
    assert "SELECT *" not in post_section
    assert "admin_note" not in post_section
    assert "SELECT id, content, image_path, likes, created_at" in post_section


def test_feedback_schema_bootstraps_empty_database_and_comments_table(tmp_path):
    import sqlite3
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / "web_runs"))
    from feedback_admin import migrate_feedback_schema

    db = tmp_path / "empty-feedback.db"
    migrate_feedback_schema(db)
    conn = sqlite3.connect(db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"feedback", "comments"} <= tables
