#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOSS 直聘批量投递脚本
=====================
在 VSCode 终端中运行：
    python run_contact_sender.py

前置条件：
1. Java 后端已启动（start.bat 或手动 java -jar）
2. 已在 BOSS 直聘登录（登录态保存在 .profiles/boss/default 目录）
3. 存在 ai_filtered_nonoutsourced.json（岗位列表）和 greeting_messages.json（招呼语）
"""

import argparse
import json
import random
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ============ 配置 ============
MAIN_API = "http://localhost:8081"
SIDECAR_API = "http://localhost:17321"
PLATFORM = "boss"
PROFILE = "default"

JOBS_FILE = Path("ai_filtered_nonoutsourced.json")
GREETINGS_FILE = Path("greeting_messages.json")
RESULTS_FILE = Path("contact_results.json")

# 每次发送之间的间隔（秒），建议 10-15 秒
DELAY_SECONDS = 10
# 单条请求超时（秒）
REQUEST_TIMEOUT = 120

# 已投递记录文件通配（会读取所有 contact_results*.json 中 status=SUCCEEDED 的岗位）
SENT_RESULTS_GLOB = "contact_results*.json"

# 连续失败熔断阈值：一轮内失败岗位数达到该值即停止整个投递并报错
# （通常意味着触发风控或服务异常，继续投递无意义）
FAIL_ABORT_THRESHOLD = 2

# 已沟通过导致的失败不计入熔断次数、也不自动重投（岗位此前已投递过）
ALREADY_CONTACTED_MARKERS = ("ALREADY_CONTACTED", "已沟通")

# sidecar 上报的风控/验证信号标记（命中后交给 LLM 裁决是否暂停本轮）
RISK_SIGNAL_MARKER = "RISK_SIGNAL"
# 强风控信号：LLM 裁决不可用时据此保守暂停（继续投递只会逐个岗位重复撞击验证）
STRONG_RISK_MARKERS = ("安全验证", "滑块", "异常访问", "环境异常")

# 排除要求工作年限过长的岗位：3 年以内（含）可投递，只剔除「4年/5年…及以上」这类写法
EXPERIENCE_REQUIRED_PATTERN = re.compile(r"([4-9]|\d{2,})\s*年(以上|及以上)?")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def contains_excluded_keywords(job: dict, keywords: list[str]) -> bool:
    """检查岗位标题或描述中是否包含排除关键词。"""
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    return any(kw.lower() in text for kw in keywords)


def filter_excluded_jobs(jobs: list[dict], keywords: list[str]) -> tuple[list[dict], int]:
    """过滤掉包含排除关键词的岗位。"""
    if not keywords:
        return jobs, 0
    filtered = []
    skipped = 0
    for job in jobs:
        if contains_excluded_keywords(job, keywords):
            skipped += 1
            log(f"跳过含排除关键词岗位：{job.get('title')} @ {job.get('company')}")
            continue
        filtered.append(job)
    return filtered, skipped


def filter_experience_required(jobs: list[dict]) -> tuple[list[dict], int]:
    """过滤掉要求工作年限过长的岗位（4 年及以上；3 年以内可投递）。"""
    filtered = []
    skipped = 0
    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')}"
        if EXPERIENCE_REQUIRED_PATTERN.search(text):
            skipped += 1
            log(f"跳过年限要求过长的岗位：{job.get('title')} @ {job.get('company')}")
            continue
        filtered.append(job)
    return filtered, skipped


def call_api(method: str, url: str, data: dict | None = None, timeout: int = 30) -> dict:
    """发送 HTTP 请求并解析 JSON 响应。"""
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


def health_check() -> None:
    """检查后端和 sidecar 是否存活。"""
    try:
        call_api("GET", f"{MAIN_API}/actuator/health", timeout=5)
    except Exception as e:
        raise RuntimeError(f"Java 后端未启动，请先运行 start.bat 或 java -jar（{e}）")

    try:
        call_api("GET", f"{SIDECAR_API}/health", timeout=5)
    except Exception as e:
        raise RuntimeError(f"Patchright sidecar 未启动（{e}）")


def open_boss_session() -> str:
    """打开 BOSS 直聘浏览器会话，返回 sessionId。"""
    resp = call_api(
        "POST",
        f"{MAIN_API}/api/recruitment/platforms/{PLATFORM}/sessions/open",
        {"profile": PROFILE, "headless": False},
        timeout=60,
    )
    session_id = resp.get("sessionId")
    if not session_id:
        raise RuntimeError(f"打开会话失败：{resp}")
    return session_id


def wait_authenticated(session_id: str, max_wait: int = 30) -> bool:
    """等待会话完成登录检测。单次请求超时/失败不算致命，继续重试。"""
    for _ in range(max_wait):
        try:
            status = call_api(
                "GET",
                f"{MAIN_API}/api/recruitment/platforms/{PLATFORM}/sessions/{session_id}/status",
                timeout=10,
            )
        except Exception as e:
            log(f"登录状态检测请求失败（稍后重试）：{e}")
            time.sleep(2)
            continue
        if status.get("authenticated"):
            log(f"已登录，当前页面：{status.get('currentUrl')}")
            return True
        time.sleep(1)
    return False


def send_contact(session_id: str, job: dict, greeting: str) -> dict:
    """向单个岗位发送打招呼语。"""
    payload = {
        "sessionId": session_id,
        "platformId": PLATFORM,
        "action": "sendContact",
        "input": {
            "confirmContact": True,
            "jobs": [
                {
                    "platformJobId": job["platformJobId"],
                    "href": job["href"],
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "city": job.get("city"),
                    "salary": job.get("salary"),
                    "facts": job.get("facts", {}),
                }
            ],
            "greeting": greeting,
        },
    }
    resp = call_api("POST", f"{SIDECAR_API}/v1/actions", payload, timeout=REQUEST_TIMEOUT)
    results = resp.get("results", [])
    return results[0] if results else {"platformJobId": job["platformJobId"], "status": "UNKNOWN", "reason": "NO_RESULT"}


def load_jobs_and_greetings(jobs_file: Path, greetings_file: Path) -> tuple[list[dict], dict[str, str]]:
    """加载岗位列表和招呼语映射。"""
    if not jobs_file.exists():
        raise RuntimeError(f"找不到岗位文件：{jobs_file.resolve()}")
    if not greetings_file.exists():
        raise RuntimeError(f"找不到招呼语文件：{greetings_file.resolve()}")

    with jobs_file.open("r", encoding="utf-8") as f:
        jobs = json.load(f)
    with greetings_file.open("r", encoding="utf-8") as f:
        greetings = json.load(f)

    greeting_by_href = {g["href"]: g["message"] for g in greetings}
    return jobs, greeting_by_href


def load_sent_job_ids() -> tuple[set[str], set[str]]:
    """读取所有已投递结果文件，返回 (已投递成功的 platformJobId 集合, 对应的 href 集合)。

    判定为"已投递、不再进聊天"的记录：
    - status == SUCCEEDED：本次工具投递成功
    - reason 含 ALREADY_CONTACTED / 已沟通：此前已在 BOSS 上沟通过（无需重投）
    SKIPPED 但可重试的类型（CONTACT_ACTION_UNAVAILABLE、NO_GREETING）不计入。
    """
    sent: set[str] = set()
    sent_hrefs: set[str] = set()
    for result_file in Path(".").glob(SENT_RESULTS_GLOB):
        try:
            with result_file.open("r", encoding="utf-8") as f:
                records = json.load(f)
            for record in records:
                reason_text = str(record.get("reason") or "")
                contacted_before = record.get("status") == "SKIPPED" and any(
                    m in reason_text for m in ALREADY_CONTACTED_MARKERS
                )
                if record.get("status") == "SUCCEEDED" or contacted_before:
                    if record.get("platformJobId"):
                        sent.add(record["platformJobId"])
                    if record.get("href"):
                        sent_hrefs.add(record["href"])
        except Exception as e:
            log(f"读取已投递记录 {result_file} 失败：{e}")
    return sent, sent_hrefs


def fetch_db_job_id_map(platform: str) -> dict[str, int]:
    """分页拉取后端库岗位，建立 encryptJobId -> DB 主键 id 映射（投递结果写回用）。"""
    id_map: dict[str, int] = {}
    page = 0
    while True:
        resp = call_api("GET", f"{MAIN_API}/api/jobs?platform={platform}&page={page}&size=200", timeout=30)
        content = resp.get("content") or []
        for row in content:
            if row.get("encryptJobId") and row.get("id") is not None:
                id_map[row["encryptJobId"]] = row["id"]
        if not content or resp.get("last", True):
            break
        page += 1
    return id_map


def mark_job_contacted(db_job_id: int) -> None:
    """把后端库中对应岗位标记为已联系（写回 isContacted，供工作流选择和旧链待投递池去重）。"""
    call_api("PUT", f"{MAIN_API}/api/jobs/{db_job_id}/contacted", {"isContacted": True}, timeout=15)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BOSS 直聘批量投递脚本")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查服务、加载数据和打开会话，不实际发送招呼语",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=1,
        help="从第几个岗位开始投递（默认 1）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多投递多少个岗位（默认 0 表示全部）",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=DELAY_SECONDS,
        help=f"每次发送间隔秒数（默认 {DELAY_SECONDS}）",
    )
    parser.add_argument(
        "--no-exclude-sent",
        action="store_true",
        help="默认会跳过已投递成功的岗位，加此参数可强制重新投递",
    )
    parser.add_argument(
        "--jobs-file",
        type=str,
        default="resume_matched_jobs.json",
        help="岗位列表 JSON 文件（默认 resume_matched_jobs.json，即简历匹配后的投递池）",
    )
    parser.add_argument(
        "--greetings-file",
        type=str,
        default="resume_greeting_messages.json",
        help="招呼语 JSON 文件（默认 resume_greeting_messages.json，即结合简历+JD 生成的招呼语）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="首轮投递失败后自动重投的轮数（默认 2，即最多投递 3 轮）",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=30,
        help="每轮重投之间的等待秒数（默认 30）",
    )
    parser.add_argument(
        "--exclude-keywords",
        type=str,
        default="实习生,实习",
        help="排除包含这些关键词的岗位，逗号分隔（默认：实习生,实习）",
    )
    return parser.parse_args()


def wait_for_services(max_wait: int = 180) -> bool:
    """等待后端和 sidecar 恢复（连接被拒等瞬时故障时用），返回是否恢复。"""
    waited = 0
    while waited < max_wait:
        try:
            call_api("GET", f"{MAIN_API}/actuator/health", timeout=5)
            call_api("GET", f"{SIDECAR_API}/health", timeout=5)
            return True
        except Exception:
            time.sleep(10)
            waited += 10
    return False


def adjudicate_risk_with_llm(reason_text: str) -> bool:
    """风控信号 LLM 裁决：True=安全验证/风控，暂停本轮；False=普通故障，可继续。

    sidecar 只负责采集证据（RISK_SIGNAL ...），是否暂停的决策权交给 LLM；
    LLM 调用失败或输出无法解析时，命中强信号（安全验证/滑块/异常访问等）一律保守暂停——
    此时继续投递只会逐个岗位重复撞击验证，持续给风控喂异常行为。
    """
    fallback = any(m in reason_text for m in STRONG_RISK_MARKERS)
    try:
        from resume_matcher import call_deepseek, load_config
        config = load_config()
        messages = [
            {"role": "system", "content": (
                "你是求职自动化系统的风控裁决器。根据投递失败时采集到的页面信号，"
                "裁决当前是否遭遇了平台安全验证/风控。只输出 JSON：{\"action\":\"PAUSE\"} 或 {\"action\":\"CONTINUE\"}，不要输出任何其他内容。"
            )},
            {"role": "user", "content": (
                f"投递失败信号：{reason_text[:800]}\n\n"
                "规则：安全验证、滑块、拖动验证、异常访问、环境异常、要求重新登录 → PAUSE；"
                "普通页面故障、元素未找到、网络抖动 → CONTINUE。"
            )},
        ]
        reply = call_deepseek(config, messages, temperature=0.0, max_tokens=300).upper()
        if "PAUSE" in reply:
            return True
        if "CONTINUE" in reply:
            return False
        raise ValueError(f"裁决输出无法解析: {reply[:100]}")
    except Exception as e:
        log(f"风控 LLM 裁决不可用（{e}），按强信号保守处理：{'暂停' if fallback else '继续'}")
        return fallback


def run_pass(
    session_id: str,
    jobs_to_send: list[dict],
    greeting_by_href: dict[str, str],
    total_jobs: int,
    start_index: int,
    delay: int,
    db_id_map: dict[str, int] | None = None,
) -> tuple[list[dict], list[dict], bool]:
    """投递一轮，返回 (本轮结果, 本轮失败的岗位列表, 是否触发熔断中止)。"""
    results = []
    failed_jobs = []
    failed_count = 0
    aborted = False
    for offset, job in enumerate(jobs_to_send):
        idx = start_index + offset
        href = job.get("href")
        platform_job_id = job.get("platformJobId")
        greeting = greeting_by_href.get(href)

        if not greeting:
            log(f"[{idx}/{total_jobs}] 跳过：未找到招呼语 {platform_job_id}")
            results.append({"platformJobId": platform_job_id, "status": "SKIPPED", "reason": "NO_GREETING"})
            continue

        log(f"[{idx}/{total_jobs}] 投递：{job.get('title')} @ {job.get('company')}")
        failed = False
        try:
            result = send_contact(session_id, job, greeting)
            log(f"[{idx}/{total_jobs}] 结果：{result['status']}" + (f" ({result.get('reason')})" if result.get("reason") else ""))
            # 已沟通过导致的失败：标记为跳过，不计入熔断、不重投
            reason_text = str(result.get("reason") or "")
            if result.get("status") == "FAILED" and any(m in reason_text for m in ALREADY_CONTACTED_MARKERS):
                result["status"] = "SKIPPED"
            results.append(result)

            # 风控信号：交给 LLM 裁决。判为安全验证/风控则暂停本轮——当前岗位与剩余岗位
            # 全部标记跳过（不计失败、不进已投递去重，下轮自动续投），人工完成验证后再跑
            if result.get("status") == "FAILED" and RISK_SIGNAL_MARKER in reason_text:
                if adjudicate_risk_with_llm(reason_text):
                    result["status"] = "SKIPPED"
                    result["reason"] = "SECURITY_VERIFICATION_PAUSE"
                    remaining = jobs_to_send[offset + 1:]
                    for rest in remaining:
                        results.append({"platformJobId": rest.get("platformJobId"), "status": "SKIPPED", "reason": "SECURITY_VERIFICATION_PAUSE_BATCH"})
                    log(f"[风控暂停] LLM 裁决命中安全验证/风控：{reason_text[:120]}")
                    log(f"[风控暂停] 本轮剩余 {len(remaining)} 个岗位已跳过，建议人工在浏览器完成验证后再跑")
                    aborted = True
                    break

            failed = result.get("status") == "FAILED"

            # 投递结果写回后端库：成功或已沟通过的岗位标记 isContacted，
            # 让 Java 侧工作流选择和旧链待投递池能去重（Python↔Java 数据公用）
            if result.get("status") == "SUCCEEDED" or any(m in reason_text for m in ALREADY_CONTACTED_MARKERS):
                db_id = (db_id_map or {}).get(platform_job_id)
                if db_id is not None:
                    try:
                        mark_job_contacted(db_id)
                    except Exception as e:
                        log(f"[{idx}/{total_jobs}] 写回已联系状态失败（不影响投递结果）：{e}")
        except Exception as e:
            log(f"[{idx}/{total_jobs}] 失败：{e}")
            results.append({"platformJobId": platform_job_id, "status": "FAILED", "reason": str(e)})
            failed = True

        if failed:
            failed_jobs.append(job)
            failed_count += 1
            if failed_count >= FAIL_ABORT_THRESHOLD:
                log(f"[中止] 本轮失败岗位数已达 {FAIL_ABORT_THRESHOLD} 个，疑似触发风控或服务异常，停止投递！")
                aborted = True
                break

        if offset < len(jobs_to_send) - 1:
            wait_seconds = delay + random.randint(0, 5)  # 间隔加 0-5 秒随机抖动，避免固定节奏
            log(f"等待 {wait_seconds} 秒...")
            time.sleep(wait_seconds)
    return results, failed_jobs, aborted


def main() -> None:
    args = parse_args()

    log("检查服务状态...")
    health_check()
    log("服务正常")

    jobs_file = Path(args.jobs_file)
    greetings_file = Path(args.greetings_file)
    # 默认使用简历匹配产物；文件缺失时给出明确指引，绝不静默回退到未比对简历的通用招呼语
    default_pair = (Path("resume_matched_jobs.json"), Path("resume_greeting_messages.json"))
    if (jobs_file, greetings_file) == default_pair and (not jobs_file.exists() or not greetings_file.exists()):
        raise RuntimeError(
            "未找到简历匹配结果（resume_matched_jobs.json / resume_greeting_messages.json）。"
            "请按顺序先运行：python resume_matcher.py（或 python job_discovery.py --match-resume），"
            "生成结合简历+JD 分析的招呼语后再投递。"
        )
    log(f"加载岗位和招呼语：{jobs_file.name} + {greetings_file.name}")
    jobs, greeting_by_href = load_jobs_and_greetings(jobs_file, greetings_file)
    log(f"共加载 {len(jobs)} 个岗位")

    # 排除含特定关键词的岗位
    exclude_keywords = [kw.strip() for kw in args.exclude_keywords.split(",") if kw.strip()]
    jobs, excluded_count = filter_excluded_jobs(jobs, exclude_keywords)
    if excluded_count:
        log(f"因含排除关键词跳过 {excluded_count} 个，剩余 {len(jobs)} 个")

    # 排除年限要求过长的岗位（4 年及以上不投递，3 年以内可投递）
    jobs, exp_skipped = filter_experience_required(jobs)
    if exp_skipped:
        log(f"因年限要求过长（4年+）跳过 {exp_skipped} 个，剩余 {len(jobs)} 个")

    # 排除已投递（SUCCEEDED + 已沟通过），进聊天前就完成比对
    if args.no_exclude_sent:
        log("已禁用投递去重")
        sent_ids: set[str] = set()
        sent_hrefs: set[str] = set()
    else:
        sent_ids, sent_hrefs = load_sent_job_ids()
        log(f"已投递/已沟通过去重：{len(sent_ids)} 个岗位将跳过")

    # 切片 + 去重
    start_index = max(1, args.start)
    end_index = len(jobs) + 1
    if args.limit > 0:
        end_index = min(start_index + args.limit, end_index)
    selected_jobs = []
    skipped_already_sent = 0
    for idx, job in enumerate(jobs[start_index - 1 : end_index - 1], start=start_index):
        # platformJobId 优先匹配，缺失时用 href 兜底（防御不同来源文件的 id 格式差异）
        if job.get("platformJobId") in sent_ids or (job.get("href") and job["href"] in sent_hrefs):
            skipped_already_sent += 1
            log(f"[{idx}/{len(jobs)}] 已投递过，跳过：{job.get('title')} @ {job.get('company')}")
            continue
        selected_jobs.append(job)

    if skipped_already_sent:
        log(f"因已投递而跳过 {skipped_already_sent} 个，本次实际投递 {len(selected_jobs)} 个")
    else:
        log(f"本次投递范围：第 {start_index} 到第 {start_index + len(selected_jobs) - 1}，共 {len(selected_jobs)} 个")

    if args.dry_run:
        log("DRY-RUN 模式：不打开会话，不发送消息")
        for idx, job in enumerate(selected_jobs, start=start_index):
            href = job.get("href")
            greeting = greeting_by_href.get(href, "")
            log(f"[{idx}] 将投递：{job.get('title')} @ {job.get('company')} | 招呼语长度：{len(greeting)}")
        return

    if not selected_jobs:
        log("没有需要投递的岗位，跳过打开浏览器会话")
        return

    log("加载后端库岗位 ID 映射（用于投递结果写回）...")
    try:
        db_id_map = fetch_db_job_id_map(PLATFORM)
    except Exception as e:
        log(f"后端库 ID 映射加载失败，写回将跳过（{e}）")
        db_id_map = {}
    log(f"后端库 ID 映射：{len(db_id_map)} 个岗位")

    log("打开 BOSS 直聘会话...")
    session_id = open_boss_session()
    log(f"会话 ID：{session_id}")

    log("等待登录状态检测...")
    if not wait_authenticated(session_id):
        raise RuntimeError("未检测到 BOSS 直聘登录状态，请在弹出的 Chrome 窗口中完成登录")

    results = []
    all_results = []
    pending_jobs = selected_jobs
    attempt = 0
    aborted = False
    while pending_jobs and attempt <= args.retries:
        attempt += 1
        if attempt > 1:
            log(f"开始第 {attempt}/{args.retries + 1} 轮投递，共 {len(pending_jobs)} 个岗位")
            if not wait_for_services(max_wait=180):
                log("服务未恢复，放弃本轮重投")
                break
        results, pending_jobs, aborted = run_pass(
            session_id, pending_jobs, greeting_by_href, len(jobs), start_index, args.delay, db_id_map
        )
        all_results.extend(results)
        if aborted:
            break
        if pending_jobs and attempt <= args.retries:
            log(f"本轮 {len(pending_jobs)} 个岗位投递失败，{args.retry_delay} 秒后自动重投...")
            time.sleep(args.retry_delay)

    results = all_results

    # 保存结果：与已有记录合并（同岗位新结果覆盖旧结果），避免覆盖写丢失历史投递记录
    merged: dict[str, dict] = {}
    if RESULTS_FILE.exists():
        try:
            with RESULTS_FILE.open("r", encoding="utf-8") as f:
                for record in json.load(f):
                    if record.get("platformJobId"):
                        merged[record["platformJobId"]] = record
        except Exception as e:
            log(f"读取已有结果文件失败（将直接覆盖）：{e}")
    for record in results:
        if record.get("platformJobId"):
            merged[record["platformJobId"]] = record

    with RESULTS_FILE.open("w", encoding="utf-8") as f:
        json.dump(list(merged.values()), f, ensure_ascii=False, indent=2)

    # 汇总
    succeeded = sum(1 for r in results if r.get("status") == "SUCCEEDED")
    failed = sum(1 for r in results if r.get("status") == "FAILED")
    skipped = sum(1 for r in results if r.get("status") == "SKIPPED")

    log("=" * 40)
    log(f"投递完成：成功 {succeeded}，失败 {failed}，跳过 {skipped}")
    log(f"详细结果：{RESULTS_FILE.resolve()}")

    if aborted:
        raise RuntimeError(
            f"检测到投递失败岗位数 >= {FAIL_ABORT_THRESHOLD} 个，已停止投递。"
            f"请检查 BOSS 登录状态 / 网络 / 服务日志后重跑（已成功的岗位会自动去重跳过）"
        )
    if pending_jobs:
        log(f"仍有 {len(pending_jobs)} 个岗位投递失败，可直接重跑本脚本，已投递成功的岗位会自动跳过")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"脚本异常退出：{e}")
        raise
