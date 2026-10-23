#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOSS 直聘登录态刷新脚本
========================
当 job_discovery / run_contact_sender 报「platform session is not authenticated」时运行：
    python login_boss.py

流程：打开浏览器会话 → 等待扫码登录 → 登录态写入 .profiles/boss/default → 长期有效
"""

import sys
import time
import urllib.request
import urllib.error

MAIN_API = "http://localhost:8081"
PLATFORM = "boss"
PROFILE = "default"


def log(msg: str) -> None:
    from datetime import datetime
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def call_api(method: str, url: str, data: dict | None = None, timeout: int = 30) -> dict:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        raise RuntimeError(f"HTTP {e.code}: {err_body}") from e


import json  # noqa: E402


def main() -> None:
    log("检查服务状态...")
    call_api("GET", f"{MAIN_API}/actuator/health", timeout=5)
    log("服务正常")

    log("打开 BOSS 直聘浏览器会话...")
    resp = call_api(
        "POST",
        f"{MAIN_API}/api/recruitment/platforms/{PLATFORM}/sessions/open",
        {"profile": PROFILE, "headless": False},
        timeout=60,
    )
    session_id = resp.get("sessionId")
    if not session_id:
        raise RuntimeError(f"打开会话失败：{resp}")
    log(f"会话 ID：{session_id}")
    log("请在弹出的 Chrome 窗口中完成登录（扫码或手机号）...")
    log("登录态会自动保存，以后运行不需要重新登录")

    max_wait = 180
    for waited in range(max_wait):
        try:
            status = call_api(
                "GET",
                f"{MAIN_API}/api/recruitment/platforms/{PLATFORM}/sessions/{session_id}/status",
                timeout=10,
            )
        except Exception as e:
            log(f"状态检测请求失败（稍后重试）：{e}")
            time.sleep(2)
            continue
        if status.get("authenticated"):
            log(f"✓ 登录成功！当前页面：{status.get('currentUrl')}")
            log("现在可以重新运行 job_discovery.py 或 run_contact_sender.py")
            return
        if waited > 0 and waited % 30 == 0:
            log(f"已等待 {waited} 秒，还在等扫码...")
        time.sleep(1)

    raise RuntimeError("等待 3 分钟仍未登录，请重跑本脚本再试一次")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"脚本异常退出：{e}")
        sys.exit(1)
