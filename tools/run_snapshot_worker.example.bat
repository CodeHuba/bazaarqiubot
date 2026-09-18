@rem BazaarDB Day Snapshot Worker (Windows)
@rem Copy this file to tools\run_snapshot_worker.bat and set the two credentials locally.
@rem Do not commit the populated copy.

@echo off
setlocal

set "BAZAAR_INGEST_API=https://YOUR_SERVER/api/ingest"
set "BAZAAR_INGEST_TOKEN=REPLACE_WITH_INGEST_TOKEN"
set "SNAPSHOT_CHROME_PROFILE=%LOCALAPPDATA%\BazaarQiuBot\snapshot-chrome-profile"
set "SNAPSHOT_WORKER_POLL_SECONDS=60"
set "SNAPSHOT_BATCH_SIZE=5"
set "SNAPSHOT_WORKERS=5"

cd /d "%~dp0"
python bazaardb_snapshot_worker.py
