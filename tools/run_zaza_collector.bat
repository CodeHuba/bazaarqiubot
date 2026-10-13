@echo off
cd /d D:\PJ\QiuBot-github\QiuBot
set PYTHONUNBUFFERED=1
set ZAZA_COLLECTOR_LOG=D:\PJ\QiuBot-github\QiuBot\tools\zaza_collector.log
"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" -u -m tools.zaza_browser_profile_collector --base-url https://bazaarqiubot.com/zaza-preview --server-token-file "D:\PJ\QiuBot-github\QiuBot\tools\zaza_collector_token.txt" --chrome-version 151 --chrome-user-data-dir "D:\PJ\QiuBot-github\QiuBot\tools\zaza-chrome-profile" >> "%ZAZA_COLLECTOR_LOG%" 2>&1
