import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "season_update.sh"


def _text():
    return SCRIPT.read_text(encoding="utf-8")


def test_season_update_script_has_safe_production_guards():
    text = _text()

    assert 'set -euo pipefail' in text
    assert 'src.backup(dst)' in text
    assert 'PRAGMA quick_check(1)' in text
    assert '"$COSCMD" upload "$RUNS_BACKUP" "$COS_KEY"' in text
    assert '"$COSCMD" info "$COS_KEY"' in text
    assert 'sudo systemctl restart "$WEB_SERVICE"' in text
    assert 'sudo systemctl restart "$WEB_SERVICE" "$BOT_SERVICE"' not in text
    assert '"$BOT_RESTART_SCRIPT"' in text
    assert 'nohup ../venv/bin/python app.py' not in text


def test_phase_transition_archives_all_outgoing_runs_without_retagging():
    text = _text()

    assert 'ARCHIVE_BUILDER="$QIUBOT_ROOT/tools/season_archive_runs.py"' in text
    assert 'history/bazaar_runs_s${PREVIOUS_RUNS_SEASON_ID}_${OLD_PHASE}_${STAMP}.db' in text
    assert 'RUNS_TARGET=$(realpath "$RUNS_DB")' in text
    assert 'mv -f "$NEW_RUNS_DB" "$RUNS_TARGET"' in text
    assert 'cp "$RUNS_BACKUP" "$RUNS_TARGET"' in text
    assert 'UPDATE runs SET' not in text
    assert 'Only runs at or after that UTC cutoff are' not in text


def test_season_update_uses_new_season_for_future_ingest():
    text = _text()

    assert 'PREVIOUS_RUNS_SEASON_ID=$(read_config_int RUNS_SEASON_ID)' in text
    assert 'TARGET_RUNS_SEASON_ID=$CURRENT_SEASON_ID' in text
    assert 'RUNS_SEASON_ID=$((NEW_SEASON_ID - 1))' not in text
    assert 'RUNS_SEASON_ID = $TARGET_RUNS_SEASON_ID' in text


def test_season_update_restores_database_on_failure_and_avoids_auto_commit():
    text = _text()

    assert 'cp "$RUNS_BACKUP" "$RUNS_TARGET"' in text
    assert 'rm -f "${RUNS_TARGET}-wal" "${RUNS_TARGET}-shm"' in text
    assert 'README_IMAGES="$QIUBOT_ROOT/tools/README_card_images.md"' in text
    assert 'git commit' not in text
    assert 'git add' not in text


def test_help_is_side_effect_free_and_documents_both_modes():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "patch <new_phase>" in result.stdout
    assert "season <new_season_id>" in result.stdout
    assert "Asia/Shanghai" in result.stdout
