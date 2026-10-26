"""Install the authorized /library-preview/ acceptance service, isolated from production."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
HOST = 'ubuntu@101.34.58.217'
SSH = str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/OpenSSH/ssh.exe') if os.name == 'nt' else 'ssh'
SCP = str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32/OpenSSH/scp.exe') if os.name == 'nt' else 'scp'

INSTALL = r"""
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
import hashlib,json,shutil,socket,subprocess,tarfile,time,urllib.request
manifest=json.loads(MANIFEST)
root=Path('/opt/qiubot-staging/library')
state=Path('/var/lib/qiubot-library-staging')
unit=Path('/etc/systemd/system/qiubot-library-staging.service')
caddy=Path('/etc/caddy/Caddyfile')
production=Path('/opt/qiubot')
incoming=Path(manifest['incoming'])
assert incoming.is_file() and hashlib.sha256(incoming.read_bytes()).hexdigest()==manifest['sha256']
assert not root.exists() and not state.exists() and not unit.exists(), 'Existing library staging must be reviewed before updating'
assert shutil.disk_usage('/opt').free>512*1024**2
with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',1034))!=0, 'Staging port occupied'
old=caddy.read_text()
assert '/library-preview' not in old
anchor='    encode zstd gzip\n'
assert old.count(anchor)==1
tracked=['web_runs/app.py','web_runs/static/runs.html','plugins/bazaar_plugin/cache/GameData.db']
def fingerprints(): return {name:hashlib.sha256((production/name).read_bytes()).hexdigest() for name in tracked}
before=fingerprints()
assert before['plugins/bazaar_plugin/cache/GameData.db']==manifest['game_sha256'], 'Game data changed since preview'
root.mkdir(parents=True)
state.mkdir(mode=0o755)
with tarfile.open(incoming,'r:gz') as bundle:
    for entry in bundle:
        path=PurePosixPath(entry.name)
        assert entry.isfile() and not path.is_absolute() and '..' not in path.parts
        target=root/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(bundle.extractfile(entry).read())
for name,digest in manifest['files'].items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest, name
inputs=root/'input'
shutil.copy2(production/'plugins/bazaar_plugin/cache/GameData.db',inputs/'GameData.db')
assert hashlib.sha256((inputs/'GameData.db').read_bytes()).hexdigest()==manifest['game_sha256']
if (root/'preseed-art').is_dir():shutil.move(str(root/'preseed-art'),str(state/'images-native-1'))
for path in inputs.iterdir():path.chmod(0o444)
subprocess.run(['chown','-R','ubuntu:ubuntu',str(state)],check=True)
unit.write_text('''[Unit]
Description=QiuBot isolated library and builder acceptance staging
After=network.target
[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/qiubot-staging/library
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/qiubot/venv/bin/python tools/preview_library.py --port 1034 --database /opt/qiubot-staging/library/input/GameData.db --translations /opt/qiubot-staging/library/input/zh-CN.bytes --images /opt/qiubot-staging/library/input/card_images.json --state-dir /var/lib/qiubot-library-staging --prefix /library-preview --data-label "19.3 · staging 验收"
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/opt/qiubot-staging/library
ReadWritePaths=/var/lib/qiubot-library-staging
MemoryHigh=384M
MemoryMax=512M
CPUQuota=50%
TasksMax=96
[Install]
WantedBy=multi-user.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
block='''
    # isolated library builder acceptance staging
    redir /library-preview /library-preview/ 308
    handle_path /library-preview/* {
        reverse_proxy 127.0.0.1:1034
    }
'''
backup=caddy.with_name('Caddyfile.library-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
route_installed=False
try:
    subprocess.run(['systemctl','start','qiubot-library-staging'],check=True)
    for attempt in range(45):
        try:
            with urllib.request.urlopen('http://127.0.0.1:1034/healthz',timeout=2) as response:health=json.load(response)
            if health.get('ok'):break
        except (OSError,ValueError):time.sleep(1)
    else:raise RuntimeError('Library staging failed readiness check')
    assert health['cards']==1850 and health['merchants']==38, health
    with urllib.request.urlopen('http://127.0.0.1:1034/api/card-preview/13081c31-f8b4-400d-bedc-8f311b08153b',timeout=8) as response:
        detail=json.load(response)
    assert detail['name']=='蓝莓派' and detail['effects']
    assert caddy.read_text()==old, 'Caddy changed concurrently; not replacing it'
    candidate=caddy.with_name('Caddyfile.library-candidate')
    expected=old.replace(anchor,anchor+block,1)
    assert expected.replace(block,'',1)==old
    candidate.write_text(expected)
    subprocess.run(['caddy','validate','--config',str(candidate),'--adapter','caddyfile'],check=True)
    shutil.copy2(caddy,backup)
    candidate.replace(caddy)
    route_installed=True
    subprocess.run(['systemctl','reload','caddy'],check=True)
    subprocess.run(['systemctl','enable','qiubot-library-staging'],check=True)
    after=fingerprints()
    assert before==after, 'Production input/source fingerprint changed during staging install'
    evidence={'url':'https://bazaarqiubot.com/library-preview/','health':health,'production_before':before,
              'production_after':after,'production_files_unchanged':before==after,'caddy_backup':str(backup),
              'staging_route_only':True,'files':manifest['files']}
    (state/'deployment.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in evidence.items() if k!='files'},ensure_ascii=False))
except BaseException:
    if route_installed:
        current=caddy.read_text()
        if current.count(block)==1:
            caddy.write_text(current.replace(block,'',1))
            subprocess.run(['systemctl','reload','caddy'],check=False)
    subprocess.run(['systemctl','disable','--now','qiubot-library-staging'],check=False)
    raise
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('Isolated plan: /library-preview/* -> loopback 1034; dedicated code, game snapshot and writable cache; no production navigation change.')
        return
    files = {name: ROOT / name for name in (
        'tools/preview_library.py','web_runs/card_preview_api.py',
        'web_runs/static/library.html','web_runs/static/library.css','web_runs/static/library.js','web_runs/static/library-layout.js','web_runs/static/library-tracking.js',
        'web_runs/static/theme.css','web_runs/static/theme.js',
        'plugins/bazaar_plugin/card_preview.py','plugins/bazaar_plugin/gamedata_client.py',
        'plugins/bazaar_plugin/encounter_data.py','plugins/bazaar_plugin/encounter_sources.json')}
    assets = ROOT / '.tmp/season-19.3-20261024/assets'
    files.update({'input/zh-CN.bytes':assets/'zh-CN.bytes','input/card_images.json':assets/'card_images.json'})
    files.update({'preseed-art/'+p.name:p for p in (ROOT/'.tmp/library-preview/images-native-1').glob('*.webp')})
    evidence = ROOT / '.tmp/library-staging'
    evidence.mkdir(parents=True, exist_ok=True)
    archive = evidence / 'release.tar.gz'
    hashes = {}
    with tarfile.open(archive, 'w:gz') as bundle:
        for name,path in sorted(files.items()):
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            bundle.add(path, arcname=name, recursive=False)
    incoming = '/tmp/qiubot-library-staging-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz'
    manifest = {'incoming':incoming,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                'files':hashes,'game_sha256':hashlib.sha256((assets/'GameData.db').read_bytes()).hexdigest()}
    (evidence/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    subprocess.run([SCP,'-o','BatchMode=yes','-o','ConnectTimeout=15',str(archive),HOST+':'+incoming],check=True)
    code = 'MANIFEST='+repr(json.dumps(manifest))+'\n'+INSTALL
    result = subprocess.run([SSH,'-o','BatchMode=yes','-o','ConnectTimeout=15',HOST,
                             'sudo -n /opt/qiubot/venv/bin/python -'],
                            input=code.encode('utf-8'),capture_output=True,check=False)
    (evidence/'install.stdout.log').write_bytes(result.stdout)
    (evidence/'install.stderr.log').write_bytes(result.stderr)
    print(result.stdout.decode('utf-8',errors='replace'))
    print(result.stderr.decode('utf-8',errors='replace'))
    if result.returncode:raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
