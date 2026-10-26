"""Small read-only Linux health probe, suitable for a systemd timer.

Each run emits one JSON record to the journal and atomically updates a state
file. It never restarts services, clears caches, kills queries or sends messages.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import urllib.request


def read_memory(path=Path('/proc/meminfo')):
    values = {}
    for line in path.read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.split()[0])
    total, free = values.get('SwapTotal', 0), values.get('SwapFree', 0)
    return dict(available_mb=round(values['MemAvailable']/1024, 1),
                swap_used_percent=round((total-free)*100/total, 1) if total else 0)


def evaluate(metrics):
    alerts = []
    available = metrics.get('available_mb')
    if available is None:
        alerts.append(('critical', 'memory_probe_failed'))
    elif available < 192:
        alerts.append(('critical', 'memory_exhaustion'))
    elif available < 512:
        alerts.append(('warning', 'low_available_memory'))
    if metrics.get('swap_used_percent', 0) >= 85 and available is not None and available < 768:
        alerts.append(('warning', 'swap_headroom_low'))
    if metrics.get('memory_full_avg10', 0) >= 5:
        alerts.append(('critical', 'memory_stall'))
    if metrics.get('load1', 0) >= 8:
        alerts.append(('warning', 'high_load'))
    if metrics.get('services') != ['active', 'active']:
        alerts.append(('critical', 'service_unavailable'))
    if metrics.get('website_status') != 200:
        alerts.append(('critical', 'website_unavailable'))
    return alerts


def collect(url):
    metrics, errors = {}, []
    try:
        metrics.update(read_memory())
        metrics['load1'] = float(Path('/proc/loadavg').read_text().split()[0])
        full = next(line for line in Path('/proc/pressure/memory').read_text().splitlines()
                    if line.startswith('full '))
        metrics['memory_full_avg10'] = float(dict(p.split('=') for p in full.split()[1:])['avg10'])
    except (OSError, ValueError, StopIteration, KeyError) as exc:
        errors.append('system:' + type(exc).__name__)
    try:
        result = subprocess.run(['systemctl', 'is-active', 'web_runs.service', 'caddy.service'],
                                capture_output=True, text=True, timeout=3)
        metrics['services'] = result.stdout.splitlines()
        if len(metrics['services']) != 2:
            errors.append('services:StateUnavailable')
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append('services:' + type(exc).__name__)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'QiuBot-Health/1.0'}), timeout=3) as response:
            metrics['website_status'] = response.status
            response.read(1024)
    except Exception as exc:
        errors.append('website:' + type(exc).__name__)
    return metrics, errors


def record(metrics, errors, previous):
    alerts = evaluate(metrics)
    if errors:
        alerts.append(('warning', 'probe_incomplete'))
    severity = 'critical' if any(s == 'critical' for s, _ in alerts) else 'warning' if alerts else 'ok'
    return dict(at=datetime.now(timezone.utc).isoformat(), severity=severity,
                consecutive_unhealthy=(previous.get('consecutive_unhealthy', 0)+1 if alerts else 0),
                changed=severity != previous.get('severity'),
                alerts=[code for _, code in alerts], metrics=metrics, probe_errors=errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='https://bazaarqiubot.com/')
    parser.add_argument('--state', type=Path, default=Path('/var/lib/qiubot-health/latest.json'))
    args = parser.parse_args()
    try:
        previous = json.loads(args.state.read_text())
    except (OSError, ValueError):
        previous = {}
    state = record(*collect(args.url), previous)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.state.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=True)+'\n', encoding='utf-8')
    temporary.replace(args.state)
    print(json.dumps(state, ensure_ascii=True), flush=True)
    return 2 if state['severity'] == 'critical' else 1 if state['severity'] == 'warning' else 0


if __name__ == '__main__':
    raise SystemExit(main())
