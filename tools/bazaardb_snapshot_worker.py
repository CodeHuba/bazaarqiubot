"""Long-running Windows worker for authenticated BazaarDB Day snapshots."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from tracker_rsc import extract_combat_snapshots


INGEST_API = os.environ['BAZAAR_INGEST_API'].rstrip('/')
INGEST_TOKEN = os.environ['BAZAAR_INGEST_TOKEN']
if not INGEST_API.startswith('https://'):
    raise RuntimeError('BAZAAR_INGEST_API must use HTTPS')
if not INGEST_TOKEN:
    raise RuntimeError('BAZAAR_INGEST_TOKEN must not be empty')
SNAPSHOT_API = INGEST_API.rsplit('/api/ingest', 1)[0] + '/api/snapshot-jobs'
SNAPSHOT_CHROME_PROFILE = os.getenv(
    'SNAPSHOT_CHROME_PROFILE',
    str(Path.home() / 'AppData' / 'Local' / 'BazaarQiuBot' / 'snapshot-chrome-profile'),
)
WORKER_POLL_SECONDS = int(os.getenv('SNAPSHOT_WORKER_POLL_SECONDS', '60'))
SNAPSHOT_TIMEOUT = int(os.getenv('SNAPSHOT_HTTP_TIMEOUT', '30'))
SNAPSHOT_WORKERS = int(os.getenv('SNAPSHOT_WORKERS', '5'))
SNAPSHOT_BATCH_SIZE = int(os.getenv('SNAPSHOT_BATCH_SIZE', '5'))
SNAPSHOT_CHROME_MAJOR = int(os.getenv('SNAPSHOT_CHROME_MAJOR', '0'))
RECONCILE_INTERVAL_SECONDS = int(os.getenv('SNAPSHOT_RECONCILE_INTERVAL_SECONDS', '3600'))
_last_reconcile_at = 0.0


class AuthenticationExpired(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BrowserSession:
    def __init__(self):
        self.driver = None
        self.cookies: dict[str, str] = {}
        self.refresh_authentication()

    def refresh_authentication(self) -> None:
        import undetected_chromedriver as uc

        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
        options = uc.ChromeOptions()
        options.add_argument(f'--user-data-dir={SNAPSHOT_CHROME_PROFILE}')
        options.add_argument('--window-size=1200,800')
        chrome_kwargs = {'options': options}
        if SNAPSHOT_CHROME_MAJOR > 0:
            chrome_kwargs['version_main'] = SNAPSHOT_CHROME_MAJOR
        self.driver = uc.Chrome(**chrome_kwargs)
        self.driver.get('https://bazaardb.gg/run')
        for _ in range(30):
            title = self.driver.title
            if 'Just a moment' not in title and 'Checking' not in title:
                break
            time.sleep(2)
        else:
            raise AuthenticationExpired('Cloudflare browser verification did not complete')
        self.cookies = {item['name']: item['value'] for item in self.driver.get_cookies()}
        if not self.cookies:
            raise AuthenticationExpired('browser session returned no cookies')

    def tracker_session(self):
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:
            raise RuntimeError('curl_cffi is required for authenticated tracker requests') from exc
        session = curl_requests.Session(impersonate='chrome131')
        session.cookies.update(self.cookies)
        return session

    @staticmethod
    def control_session() -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        return session

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()


def fetch_combat_snapshots(session: Any, run_id: str) -> tuple[list[dict], int]:
    path = f'/run/tracker/{run_id}'
    response = session.get(
        'https://bazaardb.gg' + path,
        headers={
            'Accept': 'text/x-component',
            'Rsc': '1',
            'Next-Url': path,
            'Referer': 'https://bazaardb.gg/run',
        },
        timeout=SNAPSHOT_TIMEOUT,
    )
    challenge_markers = ('just a moment', 'cf-chl-', 'cloudflare ray id', 'attention required')
    response_prefix = response.text[:5000].lower()
    if response.status_code in (401, 403, 429) or any(marker in response_prefix for marker in challenge_markers):
        raise AuthenticationExpired(f'authenticated tracker request rejected: {response.status_code}', response.status_code)
    response.raise_for_status()
    snapshots = extract_combat_snapshots(response.text)
    if not snapshots:
        raise RuntimeError('RSC response contains no CombatStart snapshots')
    return snapshots, response.status_code


def claim_jobs(http: requests.Session) -> list[dict]:
    response = http.post(
        SNAPSHOT_API + '/claim',
        json={'limit': max(1, min(SNAPSHOT_BATCH_SIZE, 20))},
        headers={'X-Ingest-Token': INGEST_TOKEN},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get('jobs', [])


def reconcile_current_phase(http: requests.Session) -> None:
    response = http.post(
        SNAPSHOT_API + '/backfill-current-phase',
        headers={'X-Ingest-Token': INGEST_TOKEN},
        json={}, timeout=60,
    )
    response.raise_for_status()


def report(http: requests.Session, run_id: str, lease_token: str, *, snapshots: list[dict] | None = None,
           error: Exception | None = None, status: int | None = None, elapsed_ms: int | None = None) -> bool:
    body: dict[str, Any] = {'httpStatus': status, 'elapsedMs': elapsed_ms,
                            'leaseToken': lease_token}
    if error is None:
        body['combatSnapshots'] = snapshots or []
    else:
        body.update({'error': str(error), 'errorType': type(error).__name__})
        if isinstance(error, AuthenticationExpired):
            body['httpStatus'] = error.status
    response = http.put(
        SNAPSHOT_API + '/' + run_id,
        json=body,
        headers={'X-Ingest-Token': INGEST_TOKEN},
        timeout=60,
    )
    response.raise_for_status()
    return True


def process_job(browser: BrowserSession, job: dict, *, allow_auth_retry: bool = True) -> None:
    started = time.perf_counter()
    control = browser.control_session()
    session = None
    try:
        session = browser.tracker_session()
        snapshots, status = fetch_combat_snapshots(session, job['run_id'])
        report(control, job['run_id'], job['lease_token'], snapshots=snapshots, status=status,
               elapsed_ms=round((time.perf_counter() - started) * 1000))
    except AuthenticationExpired as exc:
        if allow_auth_retry:
            raise exc
        report(control, job['run_id'], job['lease_token'], error=exc,
               elapsed_ms=round((time.perf_counter() - started) * 1000))
    except Exception as exc:
        report(control, job['run_id'], job['lease_token'], error=exc,
               elapsed_ms=round((time.perf_counter() - started) * 1000))
    finally:
        if session is not None:
            session.close()
        control.close()


def process_once(browser: BrowserSession) -> int:
    global _last_reconcile_at
    control = browser.control_session()
    try:
        now = time.monotonic()
        if RECONCILE_INTERVAL_SECONDS > 0 and now - _last_reconcile_at >= RECONCILE_INTERVAL_SECONDS:
            try:
                reconcile_current_phase(control)
                _last_reconcile_at = now
            except Exception as exc:
                print(f"[snapshot-worker] reconcile failed; continuing claim: {exc}", flush=True)
        jobs = claim_jobs(control)
        if not jobs:
            return 0
        auth_failed_jobs = []
        with ThreadPoolExecutor(max_workers=SNAPSHOT_WORKERS) as pool:
            futures = {pool.submit(process_job, browser, job): job for job in jobs}
            for future in as_completed(futures):
                try:
                    future.result()
                except AuthenticationExpired:
                    auth_failed_jobs.append(futures[future])
                except Exception as exc:
                    print(f"[snapshot-worker] job {futures[future]['run_id']} failed before report: {exc}", flush=True)
        if auth_failed_jobs:
            try:
                browser.refresh_authentication()
            except Exception as exc:
                retry_control = browser.control_session()
                try:
                    for job in auth_failed_jobs:
                        try:
                            report(retry_control, job['run_id'], job['lease_token'], error=exc)
                        except Exception as report_exc:
                            print(f"[snapshot-worker] retry report failed for {job['run_id']}: {report_exc}", flush=True)
                finally:
                    retry_control.close()
            else:
                for job in auth_failed_jobs:
                    try:
                        process_job(browser, job, allow_auth_retry=False)
                    except Exception as exc:
                        print(f"[snapshot-worker] retry report failed for {job['run_id']}: {exc}", flush=True)
        return len(jobs)
    finally:
        control.close()


def run_forever() -> None:
    browser = BrowserSession()
    try:
        next_cycle = time.monotonic()
        while True:
            try:
                process_once(browser)
            except AuthenticationExpired:
                browser.refresh_authentication()
            except Exception as exc:
                print(f'[snapshot-worker] cycle failed: {exc}', flush=True)
            next_cycle += WORKER_POLL_SECONDS
            time.sleep(max(0, next_cycle - time.monotonic()))
    finally:
        browser.close()


if __name__ == '__main__':
    import sys
    if '--once' in sys.argv:
        browser = BrowserSession()
        try:
            count = process_once(browser)
            print(f'[snapshot-worker] processed={count}', flush=True)
        finally:
            browser.close()
    else:
        run_forever()
