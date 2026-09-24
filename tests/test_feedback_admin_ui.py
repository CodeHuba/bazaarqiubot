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
