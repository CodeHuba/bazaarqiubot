#!/usr/bin/env bash
# BazaarQiuBot season and patch update SOP.
#
# Patch:
#   ./season_update.sh patch <new_phase> "<release time in Asia/Shanghai>"
# Season:
#   ./season_update.sh season <new_season_id> <new_phase> "<start time in Asia/Shanghai>"
#
# Upload GameData.db and zh-CN.bytes to /home/ubuntu before running. This script
# archives the entire outgoing online runs database to COS. Every phase transition
# starts a new, empty online runs database; historic runs are never retagged.

set -euo pipefail
IFS=$'\n\t'

QIUBOT_ROOT="/opt/qiubot"
DATA_CLIENT="$QIUBOT_ROOT/plugins/bazaar_plugin/data_client.py"
FETCH_IMAGES="$QIUBOT_ROOT/tools/fetch_card_images.py"
README_IMAGES="$QIUBOT_ROOT/tools/README_card_images.md"
GAMEDATA_DB="$QIUBOT_ROOT/plugins/bazaar_plugin/cache/GameData.db"
ZH_CN_BYTES="$QIUBOT_ROOT/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes"
RUNS_DB="$QIUBOT_ROOT/data/bazaar_runs.db"
RUNS_TARGET=""
UPLOAD_DIR="/home/ubuntu"
BACKUP_ROOT="$QIUBOT_ROOT/backups"
COSCMD="$QIUBOT_ROOT/venv/bin/coscmd"
ARCHIVE_BUILDER="$QIUBOT_ROOT/tools/season_archive_runs.py"
WEB_SERVICE="web_runs.service"
BOT_RESTART_SCRIPT="$QIUBOT_ROOT/restart.sh"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { printf '%b[INFO]%b %s\n' "$GREEN" "$NC" "$*"; }
log_warn() { printf '%b[WARN]%b %s\n' "$YELLOW" "$NC" "$*"; }
die() { printf '%b[ERROR]%b %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage:
  Patch update:
    $0 patch <new_phase> "<release time in Asia/Shanghai>"
    Example: $0 patch 18.2 "2026-09-20 10:00:00"

  Season update:
    $0 season <new_season_id> <new_phase> "<start time in Asia/Shanghai>"
    Example: $0 season 19 19.1 "2026-10-01 10:00:00"

The time argument is Beijing time and records the new phase start. The outgoing
online runs database is archived to COS, then replaced by an empty database for
the new phase. Upload $UPLOAD_DIR/GameData.db and $UPLOAD_DIR/zh-CN.bytes first.
EOF
}

require_file() {
    [ -f "$1" ] || die "Missing required file: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

utc_from_beijing() {
    python3 - "$1" <<'PY'
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
try:
    value = datetime.strptime(sys.argv[1], '%Y-%m-%d %H:%M:%S')
except ValueError as exc:
    raise SystemExit(f'Invalid Beijing time: {exc}')
print(value.replace(tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(ZoneInfo('UTC')).isoformat(timespec='seconds'))
PY
}

read_config_int() {
    grep -oP "^$1 = \\K[0-9]+" "$DATA_CLIENT" | head -1
}

read_config_phase() {
    grep -oP '^CURRENT_PHASE = "\\K[^"]+' "$DATA_CLIENT" | head -1
}

validate_sqlite() {
    local db=$1
    python3 - "$db" <<'PY'
import sqlite3
import sys
path = sys.argv[1]
conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)
try:
    result = conn.execute('PRAGMA quick_check(1)').fetchone()[0]
finally:
    conn.close()
if result != 'ok':
    raise SystemExit(f'SQLite quick_check failed for {path}: {result}')
print(f'SQLite quick_check OK: {path}')
PY
}

validate_staged_assets() {
    python3 - "$1" "$2" <<'PY'
import os
import sqlite3
import sys

gamedata, translations = sys.argv[1:]
conn = sqlite3.connect(f'file:{gamedata}?mode=ro', uri=True, timeout=30)
try:
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cards'").fetchone()
    cards = conn.execute('SELECT COUNT(*) FROM cards').fetchone()[0] if table else 0
finally:
    conn.close()
if not table or cards <= 0:
    raise SystemExit('Staged GameData.db has no usable cards table')
if os.path.getsize(translations) <= 0:
    raise SystemExit('Staged zh-CN.bytes is empty')
print(f'Staged GameData.db OK: {cards} cards')
print(f'Staged zh-CN.bytes OK: {os.path.getsize(translations)} bytes')
PY
}

online_backup() {
    local source=$1 destination=$2
    python3 - "$source" "$destination" <<'PY'
import sqlite3
import sys
source, destination = sys.argv[1:]
src = sqlite3.connect(source, timeout=60)
dst = sqlite3.connect(destination)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
PY
}

restart_and_verify() {
    sudo systemctl restart "$WEB_SERVICE"
    for _ in $(seq 1 20); do
        if sudo systemctl is-active --quiet "$WEB_SERVICE"; then
            status=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:1027/api/heroes || true)
            [ "$status" = "200" ] && break
        fi
        sleep 2
    done
    sudo systemctl is-active --quiet "$WEB_SERVICE" || return 1
    [ "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:1027/api/heroes || true)" = "200" ] || return 1
    if [ -x "$BOT_RESTART_SCRIPT" ]; then
        sudo systemctl stop qiubot.service || true
        "$BOT_RESTART_SCRIPT"
    else
        log_warn "Bot restart script not found; Web service was restarted, Bot restart is manual."
    fi
    for _ in $(seq 1 15); do
        pgrep -f '/opt/qiubot/venv/bin/python main.py' >/dev/null 2>&1 && return 0
        sleep 2
    done
    log_warn 'Bot process was not detected after restart script; inspect /opt/qiubot/logs/bot.log.'
    return 1
}

[ $# -ge 1 ] || { usage; exit 2; }
case "$1" in
    --help|-h) usage; exit 0 ;;
    patch|season) ;;
    *) usage; exit 2 ;;
esac

UPDATE_TYPE=$1
if [ "$UPDATE_TYPE" = "patch" ]; then
    [ $# -eq 3 ] || die 'Patch usage: season_update.sh patch <new_phase> "<Beijing time>"'
    NEW_PHASE=$2
    CUTOFF_BJ=$3
    CURRENT_SEASON_ID=$(read_config_int CURRENT_SEASON_ID)
    PREVIOUS_RUNS_SEASON_ID=$(read_config_int RUNS_SEASON_ID)
    TARGET_RUNS_SEASON_ID=$PREVIOUS_RUNS_SEASON_ID
else
    [ $# -eq 4 ] || die 'Season usage: season_update.sh season <new_season_id> <new_phase> "<Beijing time>"'
    CURRENT_SEASON_ID=$2
    NEW_PHASE=$3
    CUTOFF_BJ=$4
    PREVIOUS_RUNS_SEASON_ID=$(read_config_int RUNS_SEASON_ID)
    TARGET_RUNS_SEASON_ID=$CURRENT_SEASON_ID
fi

[ -n "$CURRENT_SEASON_ID" ] || die 'Could not read CURRENT_SEASON_ID'
[ -n "$PREVIOUS_RUNS_SEASON_ID" ] || die 'Could not read RUNS_SEASON_ID'
[ -n "$NEW_PHASE" ] || die 'New phase must not be empty'
CUTOFF_UTC=$(utc_from_beijing "$CUTOFF_BJ")
OLD_PHASE=$(read_config_phase)

for command in python3 sha256sum curl df sudo; do require_command "$command"; done
for file in "$DATA_CLIENT" "$FETCH_IMAGES" "$README_IMAGES" "$GAMEDATA_DB" "$ZH_CN_BYTES" "$RUNS_DB" "$COSCMD" "$ARCHIVE_BUILDER" "$BOT_RESTART_SCRIPT" "$UPLOAD_DIR/GameData.db" "$UPLOAD_DIR/zh-CN.bytes"; do require_file "$file"; done
RUNS_TARGET=$(realpath "$RUNS_DB")
[ -f "$RUNS_TARGET" ] || die "Runs database target is missing: $RUNS_TARGET"

log_info "Preflight: $UPDATE_TYPE, phase $OLD_PHASE -> $NEW_PHASE, start $CUTOFF_UTC"
log_info "Outgoing database will be archived; new online scope: S$TARGET_RUNS_SEASON_ID / $NEW_PHASE"
validate_sqlite "$RUNS_DB"
validate_staged_assets "$UPLOAD_DIR/GameData.db" "$UPLOAD_DIR/zh-CN.bytes"

RUNS_BYTES=$(stat -Lc '%s' "$RUNS_DB")
ROOT_AVAIL=$(df -PB1 "$BACKUP_ROOT" | awk 'NR==2 {print $4}')
MIN_FREE=$((RUNS_BYTES + 1024 * 1024 * 1024))
[ "$ROOT_AVAIL" -ge "$MIN_FREE" ] || die "Insufficient root space for the outgoing-database rollback copy: need $MIN_FREE bytes, have $ROOT_AVAIL"

STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="$BACKUP_ROOT/season_update_$STAMP"
RUNS_BACKUP="$BACKUP_DIR/bazaar_runs.outgoing_${OLD_PHASE}.db"
NEW_RUNS_DB="${RUNS_TARGET}.new_${NEW_PHASE}_${STAMP}"
COS_KEY="history/bazaar_runs_s${PREVIOUS_RUNS_SEASON_ID}_${OLD_PHASE}_${STAMP}.db"
mkdir -p "$BACKUP_DIR"

cp -a "$DATA_CLIENT" "$GAMEDATA_DB" "$ZH_CN_BYTES" "$FETCH_IMAGES" "$README_IMAGES" "$BACKUP_DIR/"

rollback() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        log_warn "Update failed; restoring application files and outgoing database"
        sudo systemctl stop "$WEB_SERVICE" || true
        cp -a "$BACKUP_DIR/$(basename "$DATA_CLIENT")" "$DATA_CLIENT" || true
        cp -a "$BACKUP_DIR/$(basename "$GAMEDATA_DB")" "$GAMEDATA_DB" || true
        cp -a "$BACKUP_DIR/$(basename "$ZH_CN_BYTES")" "$ZH_CN_BYTES" || true
        cp -a "$BACKUP_DIR/$(basename "$FETCH_IMAGES")" "$FETCH_IMAGES" || true
        cp -a "$BACKUP_DIR/$(basename "$README_IMAGES")" "$README_IMAGES" || true
        if [ -f "$RUNS_BACKUP" ]; then
            cp "$RUNS_BACKUP" "$RUNS_TARGET" || true
            rm -f "${RUNS_TARGET}-wal" "${RUNS_TARGET}-shm" || true
        fi
        rm -f "$NEW_RUNS_DB" || true
        restart_and_verify || true
    fi
    exit "$status"
}
trap rollback EXIT

log_info 'Stopping Web service to freeze the outgoing database'
sudo systemctl stop "$WEB_SERVICE"
log_info "Creating complete outgoing-database rollback/history copy: $RUNS_BACKUP"
online_backup "$RUNS_DB" "$RUNS_BACKUP"
validate_sqlite "$RUNS_BACKUP"
RUNS_SHA=$(sha256sum "$RUNS_BACKUP" | awk '{print $1}')
RUNS_SIZE=$(stat -Lc '%s' "$RUNS_BACKUP")
log_info "Uploading complete history database to COS: $COS_KEY"
"$COSCMD" upload "$RUNS_BACKUP" "$COS_KEY"
COS_INFO=$("$COSCMD" info "$COS_KEY")
printf '%s\n' "$COS_INFO" | grep -q "$RUNS_SIZE" || die 'COS history object size does not match local archive'
log_info "COS history verified; SHA-256: $RUNS_SHA; bytes: $RUNS_SIZE"

log_info 'Building an empty online database for the new phase'
python3 "$ARCHIVE_BUILDER" \
    --source "$RUNS_BACKUP" \
    --destination "$NEW_RUNS_DB" \
    --phase-start-utc "$CUTOFF_UTC" \
    --target-season "$TARGET_RUNS_SEASON_ID" \
    --target-phase "$NEW_PHASE"
validate_sqlite "$NEW_RUNS_DB"
mv -f "$NEW_RUNS_DB" "$RUNS_TARGET"
rm -f "${RUNS_TARGET}-wal" "${RUNS_TARGET}-shm"
log_info 'Outgoing runs were archived to COS and removed from the online database'

cp "$UPLOAD_DIR/GameData.db" "$GAMEDATA_DB"
cp "$UPLOAD_DIR/zh-CN.bytes" "$ZH_CN_BYTES"
sed -i "s/^CURRENT_SEASON_ID = .*/CURRENT_SEASON_ID = $CURRENT_SEASON_ID/" "$DATA_CLIENT"
sed -i "s/^RUNS_SEASON_ID = .*/RUNS_SEASON_ID = $TARGET_RUNS_SEASON_ID/" "$DATA_CLIENT"
sed -i "s/^CURRENT_PHASE = .*/CURRENT_PHASE = \"$NEW_PHASE\"  # Current season phase/" "$DATA_CLIENT"
sed -i "s/IMAGE_VERSION = os.getenv(\"CARD_IMAGE_VERSION\", \"[^\"]*\")/IMAGE_VERSION = os.getenv(\"CARD_IMAGE_VERSION\", \"$NEW_PHASE\")/" "$FETCH_IMAGES"
sed -i "s/\"version\": \"[0-9.]*\"/\"version\": \"$NEW_PHASE\"/" "$README_IMAGES"
find "$QIUBOT_ROOT/plugins/bazaar_plugin" "$QIUBOT_ROOT/web_runs" -name '*.pyc' -delete

restart_and_verify || die 'Service restart or health check failed'
validate_sqlite "$RUNS_DB"

log_info 'Update complete.'
log_info "Backup directory: $BACKUP_DIR"
log_info "COS rollback backup: $COS_KEY"
log_info "Current config: season=$CURRENT_SEASON_ID runs_season=$TARGET_RUNS_SEASON_ID phase=$NEW_PHASE"
log_warn 'Required follow-up: run fetch_card_images.py and verify card_images.json URLs use the new z<phase> version.'
if [ "$UPDATE_TYPE" = "season" ]; then
    log_warn 'Required follow-up: update Windows collector SEASON_START before the next scheduled collection.'
fi
log_warn 'Git is intentionally not committed here. Review and commit from the authoritative local repository.'

rm -f "$RUNS_BACKUP"
log_info 'Removed local runs rollback copy after COS verification; metadata and application-file backups remain locally.'
trap - EXIT
