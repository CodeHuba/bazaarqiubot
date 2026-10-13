"""Windows 浏览器认证的 Zaza profile-runs 受保护任务采集器。

浏览器只负责在 ``/api/run`` 同源请求中携带 Turnstile ``t`` token；任务
服务端永远只收到采集结果和 lease token，不会收到该浏览器 token。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any
from urllib.parse import urlencode

from tools.zaza_profile_runs import collect_profile_runs, ProfileRunsAuthError


class CollectorProtocolError(RuntimeError):
    """服务端任务协议或浏览器响应格式无效。"""


class BrowserProfileClient:
    """通过 webdriver 的同源 fetch 实现 zaza_profile_runs 的 client 协议。"""

    def __init__(self, driver, token: str, *, run_path: str = "/api/run"):
        if not isinstance(token, str) or not token:
            raise ValueError("浏览器认证 token 不能为空")
        self.driver = driver
        self._token = token
        self.run_path = run_path

    def get(self, path: str, params: dict[str, Any]):
        query = dict(params)
        query["t"] = self._token
        url = path + "?" + urlencode(query)
        # token 仅插入浏览器上下文；不要把 script/result 写入日志或异常。
        script = """
            const done = arguments[arguments.length - 1];
            fetch(arguments[0], {credentials: 'include'})
              .then(async response => done(JSON.stringify({
                  status: response.status, ok: response.ok,
                  body: await response.text()
              })))
              .catch(error => done(JSON.stringify({network_error: String(error)})));
        """
        try:
            raw = self.driver.execute_async_script(script, url)
        except Exception as exc:
            raise RuntimeError("浏览器请求 /api/run 失败") from exc
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("浏览器请求返回了无效 JSON") from exc
        if not isinstance(envelope, dict):
            raise ValueError("浏览器请求返回格式无效")
        if envelope.get("network_error"):
            raise RuntimeError("浏览器请求 /api/run 失败")
        status = envelope.get("status")
        if status in (401, 403):
            raise ProfileRunsAuthError("浏览器认证已失效或被拒绝")
        if not envelope.get("ok", False) or not isinstance(status, int) or status >= 400:
            raise RuntimeError(f"浏览器 API 请求失败 (HTTP {status})")
        body = envelope.get("body")
        try:
            data = json.loads(body) if isinstance(body, str) else body
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("profile runs 返回了无效 JSON") from exc
        if isinstance(data, dict) and ("error" in data or "message" in data):
            raise RuntimeError("profile runs API 返回错误")
        if not isinstance(data, list):
            raise ValueError("profile runs response must be a JSON array")
        return data


class CollectorServerSession:
    """受保护任务 API；显式 base_url/token，且不继承系统代理。"""

    def __init__(self, base_url: str, token: str, *, session=None):
        if not base_url or not token:
            raise ValueError("server base_url 和 token 均不能为空")
        if session is None:
            import requests
            session = requests.Session()
        session.trust_env = False
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = self.session.post(
            self.base_url + path, json=payload or {},
            headers={"X-Ingest-Token": self.token}, timeout=30,
        )
        if response.status_code in (401, 403):
            raise ProfileRunsAuthError("collector server 认证失败")
        if response.status_code >= 400:
            try:
                reason = str(response.json().get('error', 'unknown'))[:300]
            except (ValueError, AttributeError):
                reason = 'invalid error response'
            reason = reason.replace(self.token, '[REDACTED]')
            raise RuntimeError(f"collector server 请求失败 (HTTP {response.status_code}): {reason}")
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise CollectorProtocolError("collector server 返回了无效 JSON") from exc

    def claim(self):
        payload = self._post("/api/zaza/collector/claim")
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise CollectorProtocolError("claim 响应必须包含 jobs 数组")
        return payload["jobs"]

    def complete(self, job, runs):
        return self._post("/api/zaza/collector/complete", {
            "id": job["id"], "lease_token": job["lease_token"], "runs": runs,
        })

    def failure(self, job, error):
        # error 不包含浏览器 token；调用方也不应把异常 repr 中的 token 传入。
        return self._post("/api/zaza/collector/failure", {
            "id": job["id"], "lease_token": job["lease_token"], "error": str(error),
        })


def process_jobs(server: CollectorServerSession, browser: BrowserProfileClient) -> int:
    """领取并处理全部 jobs；单 job 失败只 failure，绝不 complete。"""
    completed = 0
    for job in server.claim():
        if not isinstance(job, dict):
            raise CollectorProtocolError("job 必须是对象")
        required = ("id", "lease_token", "profile_id", "season", "phase", "mode",
                    "created_after", "created_before")
        missing = [key for key in required if key not in job]
        if missing:
            raise CollectorProtocolError("job 缺少字段: " + ",".join(missing))
        try:
            runs = collect_profile_runs(
                browser, job["profile_id"], mode=job["mode"],
                created_after=job["created_after"], created_before=job["created_before"],
            )
        except Exception as exc:
            server.failure(job, exc)
            continue
        try:
            server.complete(job, runs)
        except Exception as exc:
            from pathlib import Path
            evidence = Path(__file__).resolve().parent / 'zaza_collector_rejected.json'
            evidence.write_text(json.dumps({'profile_id': job['profile_id'], 'runs': runs}, ensure_ascii=False), encoding='utf-8')
            server.failure(job, str(exc))
            print(str(exc), flush=True)
            continue
        print(f"completed profile={job['profile_id']} mode={job['mode']} runs={len(runs)}", flush=True)
        completed += 1
    return completed


def extract_token_from_performance_log(driver) -> str | None:
    """从 Network.requestWillBeSent 提取 /api/run 的 t，不输出 token。"""
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
            if message.get("method") != "Network.requestWillBeSent":
                continue
            url = message["params"]["request"]["url"]
            if "/api/run" not in url:
                continue
            match = re.search(r"[?&]t=([^&]+)", url)
            if match:
                return match.group(1)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows Zaza profile-runs collector")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--server-token", default=None)
    parser.add_argument("--run-url", default="https://bazaardb.gg/run")
    parser.add_argument("--chrome-version", type=int, default=None,
                        help="undetected-chromedriver 主版本；不传则使用已安装 Chrome 主版本")
    parser.add_argument("--chrome-user-data-dir", default=None,
                        help="独立 Chrome 用户目录；避免复用正在运行的 Chrome 锁")
    parser.add_argument("--server-token-file", default=None,
                        help="从本机文件读取 server token，不在命令行中暴露")
    args = parser.parse_args(argv)
    server_token = args.server_token
    if args.server_token_file:
        server_token = open(args.server_token_file, encoding="ascii").read().strip()
    import undetected_chromedriver as uc
    options = uc.ChromeOptions()
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    if args.chrome_user_data_dir:
        options.add_argument(f"--user-data-dir={args.chrome_user_data_dir}")
        options.add_argument("--profile-directory=Default")
    chrome_kwargs = {"options": options}
    if args.chrome_version is not None:
        chrome_kwargs["version_main"] = args.chrome_version
    driver = uc.Chrome(**chrome_kwargs)
    try:
        driver.get(args.run_url)
        token = None
        for _ in range(30):
            token = extract_token_from_performance_log(driver)
            if token:
                break
            time.sleep(1)
        if not token:
            raise ProfileRunsAuthError("未能从 run 页面 performance log 获取认证 token")
        while True:
            process_jobs(CollectorServerSession(args.base_url, server_token),
                         BrowserProfileClient(driver, token))
            time.sleep(30)
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
