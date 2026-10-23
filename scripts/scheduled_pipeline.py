#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时流水线驱动（由 Windows 任务计划程序调用）
==============================================
早 9:00（--mode morning）：
    登录刷新 → 批量投递 → 若当天成功数不足 --target，
    自动循环「冷却 → 发现新岗位 → 简历匹配 → 再投递」，直到达标 / 岗位池枯竭 / 熔断 / 轮数上限。

晚 20:00（--mode evening）：
    登录刷新 → 岗位发现 + 简历匹配，为次日投递准备岗位池。

设计要点：
- 当天成功数通过解析 run_contact_sender.py 每轮输出的「投递完成：成功 N」累计，
  contact_results.json 无时间戳，不能直接按天统计。
- 岗位池枯竭判定：连续两轮「本次实际投递 0 个」即停止，避免空转一整天。
- 风控保护：每轮发现前随机冷却 5-10 分钟；投递脚本自身熔断（连续失败 >=2）时立即终止当日循环。
- BOSS 登录态过期需扫码时，login_boss.py 3 分钟超时退出，本驱动记录日志并终止，
  人工扫码后双击对应 .bat 即可补跑（已投递岗位自动去重）。

所有输出追加到 logs/schedule_pipeline.log。
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ============ 配置 ============
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_API = "http://localhost:8081"
SIDECAR_API = "http://localhost:17321"

PYTHON = r"D:\miniconda\python.exe"
JAVA = r"C:\Users\YHR\.jdks\ms-21.0.8\bin\java.exe"
JAR = PROJECT_ROOT / "target" / "npe_get_jobs-v1.1.0.jar"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "schedule_pipeline.log"
BACKEND_LOG = LOG_DIR / "backend_stdout.log"

SUMMARY_PATTERN = re.compile(r"投递完成：成功 (\d+)，失败 (\d+)，跳过 (\d+)")
ACTUAL_PATTERN = re.compile(r"本次实际投递 (\d+) 个")

# 子进程统一 UTF-8，避免 Windows 管道编码乱码
SUB_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_step(name: str, cmd: list[str], timeout: int | None = None) -> tuple[int, str]:
    """运行一个流水线步骤，完整输出写入日志，返回 (退出码, 合并输出)。"""
    log(f"----- 步骤开始：{name}：{' '.join(cmd)} -----")
    try:
        proc = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            env=SUB_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        for line in output.splitlines():
            log(f"  | {line}")
        log(f"----- 步骤结束：{name}（退出码 {proc.returncode}）-----")
        return proc.returncode, output
    except subprocess.TimeoutExpired as e:
        log(f"----- 步骤超时：{name}（>{timeout} 秒），终止当日流水线 -----")
        return 124, (e.stdout or "") + (e.stderr or "")


def services_up() -> bool:
    for url in (f"{MAIN_API}/actuator/health", f"{SIDECAR_API}/health"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status != 200:
                    return False
        except Exception:
            return False
    return True


def ensure_backend(max_wait: int = 300) -> bool:
    """后端+sidecar 存活则直接返回；否则拉起 jar 并等待就绪。"""
    if services_up():
        log("后端与 sidecar 均已存活")
        return True
    if not JAR.exists():
        log(f"后端未运行且找不到 jar：{JAR}")
        return False
    log("后端未运行，启动 Java 后端...")
    BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BACKEND_LOG.open("ab") as backend_out:
        subprocess.Popen(
            [JAVA, "-jar", str(JAR)],
            cwd=PROJECT_ROOT,
            stdout=backend_out,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    waited = 0
    while waited < max_wait:
        if services_up():
            log(f"后端已就绪（等待 {waited} 秒）")
            return True
        time.sleep(10)
        waited += 10
    log(f"等待 {max_wait} 秒后端仍未就绪，终止")
    return False


def refresh_login() -> bool:
    rc, _ = run_step("刷新 BOSS 登录态", [PYTHON, "login_boss.py"], timeout=360)
    if rc != 0:
        log("登录刷新失败（可能需要扫码）：请人工在 Chrome 窗口扫码后，手动双击对应 .bat 补跑")
    return rc == 0


def run_advisor_step() -> None:
    """Agent 策略顾问：只分析建议、产出 agent_strategy.json；失败/超时不影响流水线主结果。"""
    rc, _ = run_step("Agent 策略顾问", [PYTHON, "agent_advisor.py"], timeout=900)
    if rc != 0:
        log("策略顾问运行失败（不影响本轮投递结果，可稍后手动运行 agent_advisor.py）")


def count_pool() -> tuple[int, int]:
    """返回 (投递池岗位数, 招呼语条数)，文件缺失返回 (0, 0)。"""
    jobs_file = PROJECT_ROOT / "resume_matched_jobs.json"
    greetings_file = PROJECT_ROOT / "resume_greeting_messages.json"
    jobs = greetings = 0
    try:
        jobs = len(json.loads(jobs_file.read_text(encoding="utf-8")))
    except Exception:
        pass
    try:
        greetings = len(json.loads(greetings_file.read_text(encoding="utf-8")))
    except Exception:
        pass
    return jobs, greetings


def discover_and_match() -> bool:
    """发现新岗位并重新匹配简历，产出新的投递池与招呼语。"""
    rc, _ = run_step("岗位发现", [PYTHON, "job_discovery.py"])
    if rc != 0:
        return False
    rc, _ = run_step("简历匹配与招呼语生成", [PYTHON, "resume_matcher.py"], timeout=7200)
    return rc == 0


def morning_pipeline(target: int, max_extra_rounds: int) -> None:
    log("=" * 60)
    log(f"早 9:00 流水线启动（当日目标：成功投递 {target} 个）")

    if not ensure_backend():
        return
    if not refresh_login():
        return

    total_sent = 0
    no_send_streak = 0
    round_no = 0
    max_rounds = 1 + max_extra_rounds  # 首轮 + 补足轮次

    while total_sent < target and round_no < max_rounds:
        round_no += 1
        remaining = target - total_sent
        log(f"===== 投递轮次 {round_no}/{max_rounds}（累计成功 {total_sent}/{target}，本轮上限 {remaining}）=====")

        rc, output = run_step(
            f"批量投递（第 {round_no} 轮）",
            [PYTHON, "run_contact_sender.py", "--limit", str(remaining)],
            timeout=14400,
        )

        succeeded = failed = skipped = 0
        m = SUMMARY_PATTERN.search(output)
        if m:
            succeeded, failed, skipped = (int(g) for g in m.groups())
            total_sent += succeeded
        else:
            log("未解析到投递汇总行，本轮按 0 成功计（详见上方日志）")

        actual = ACTUAL_PATTERN.search(output)
        actual_n = int(actual.group(1)) if actual else None
        log(f"第 {round_no} 轮结果：成功 {succeeded}，失败 {failed}，跳过 {skipped}（实际投递 {actual_n} 个），当日累计 {total_sent}/{target}")

        if rc != 0:
            log("投递脚本非零退出（触发熔断 / 登录失效 / 服务异常），终止当日循环，人工处理后补跑即可")
            break
        if total_sent >= target:
            log(f"已达成当日目标 {target} 个")
            break
        if actual_n == 0:
            no_send_streak += 1
            if no_send_streak >= 2:
                log("连续两轮无实际投递，岗位池已枯竭，结束当日循环")
                break
        else:
            no_send_streak = 0

        # 补足岗位池后再投
        cooldown = random.randint(300, 600)
        log(f"距目标还差 {target - total_sent} 个，冷却 {cooldown} 秒后进行新一轮发现+匹配...")
        time.sleep(cooldown)
        if not discover_and_match():
            log("发现/匹配失败，结束当日循环")
            break
        pool_jobs, pool_greetings = count_pool()
        log(f"新一轮岗位池：{pool_jobs} 个岗位 / {pool_greetings} 条招呼语")

    log(f"早 9:00 流水线结束：当日成功投递 {total_sent} 个（目标 {target}），"
        f"{'达标' if total_sent >= target else '未达标'}，详细日志见 {LOG_FILE}")
    run_advisor_step()
    log("=" * 60)


def evening_pipeline() -> None:
    log("=" * 60)
    log("晚 20:00 流水线启动（发现 + 匹配，为次日投递准备岗位池）")

    if not ensure_backend():
        return
    if not refresh_login():
        return
    if not discover_and_match():
        log("晚 20:00 流水线异常结束")
        return

    pool_jobs, pool_greetings = count_pool()
    log(f"晚 20:00 流水线结束：岗位池 {pool_jobs} 个岗位 / {pool_greetings} 条招呼语")
    run_advisor_step()
    log("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="定时流水线驱动（任务计划程序调用）")
    parser.add_argument("--mode", choices=["morning", "evening"], required=True)
    parser.add_argument("--target", type=int, default=80, help="早 9:00 当日成功投递目标（默认 80）")
    parser.add_argument(
        "--max-extra-rounds",
        type=int,
        default=4,
        help="首轮之外最多的「发现+匹配+投递」补足轮数（默认 4）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(PROJECT_ROOT)
    log("")
    if args.mode == "morning":
        morning_pipeline(args.target, args.max_extra_rounds)
    else:
        evening_pipeline()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"流水线异常退出：{e}")
        sys.exit(1)
