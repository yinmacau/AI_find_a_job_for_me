#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 策略顾问（混合形态 Agent 层）
=====================================
在 VSCode 终端中运行：
    python agent_advisor.py            # 分析最近结果，产出/更新 agent_strategy.json
    python agent_advisor.py --dry-run  # 只打印建议，不写文件

定位（混合形态的核心边界）：
- 本模块是整条流水线里唯一由 LLM 做「策略决策」的地方，但它只做三件事：
  分析最近投递结果 → 给出建议 → 经白名单 + 有界校验后写入 agent_strategy.json。
- 执行层（job_discovery.py / run_contact_sender.py）的防风控节奏（45-90s 间隔、
  翻页上限、投递 10-15s+抖动、熔断阈值）与投递去重（isContacted）完全不经过本模块，
  Agent 无权读取更无权修改这些参数——这是硬护栏，不是提示词约束。
- Agent 建议只有两类有界生效项：
  1. score_threshold：匹配打分阈值，仅 50~80 之间生效（resume_matcher.py 消费）；
  2. direction_weights：预设方向权重，0.5~2.0（job_discovery.py 消费：
     >=1.3 该方向在本周期轮换池出现两次，<=0.7 本周期剔除）。
- 其余输出（summary / exception_analysis / notes_for_human）仅供人工阅读，不驱动任何代码。

数据流：
    contact_results*.json ─┐
    resume_matched_jobs.json ─┼→ 证据包 → DeepSeek（严格 JSON）→ 校验/收敛 → agent_strategy.json
    resume_low_score_jobs.json ─┤
    discovery_rotation_state.json ─┤
    logs/schedule_pipeline.log ────┘

运行时机：scheduled_pipeline.py 在早/晚流水线末尾自动调用（失败不影响主流程）；
也可随时手动运行。产出文件可人工审阅后直接编辑，消费端会重新校验。
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

STRATEGY_FILE = Path("agent_strategy.json")

# ============ 有界生效区间（消费端会再次校验，双重防线）============
THRESHOLD_MIN = 50
THRESHOLD_MAX = 80
WEIGHT_MIN = 0.5
WEIGHT_MAX = 2.0
WEIGHT_BOOST = 1.3    # >=：方向在本周期轮换池中出现两次（job_discovery.py）
WEIGHT_SUPPRESS = 0.7  # <=：方向本周期不进入轮换池（job_discovery.py）

# 证据包体量上限：控制 prompt 长度与单次成本
TOP_FAILURE_REASONS = 8
TOP_LOW_SCORE_REASONS = 5
LOG_SIGNAL_LINES = 15
LOG_SIGNAL_TAIL_LINES = 200

PIPELINE_LOG = Path("logs/schedule_pipeline.log")
LOG_SIGNAL_PATTERN = re.compile(r"熔断|失败|异常|风控|错误|登录|超时")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def load_json(path: Path, default):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log(f"读取 {path} 失败（按空处理）：{e}")
    return default


def known_directions() -> list[str]:
    """预设方向清单（与 job_discovery.py 单一来源，惰性导入避免循环依赖）。"""
    from job_discovery import DEFAULT_DIRECTIONS
    return list(DEFAULT_DIRECTIONS)


def collect_delivery_stats() -> dict:
    """汇总所有 contact_results*.json：状态计数、失败原因 TopN、按方向的成功/失败数。"""
    totals = Counter()
    failure_reasons = Counter()
    per_direction: dict[str, Counter] = {}
    id_to_title: dict[str, str] = {}

    # 用岗位档案把 platformJobId 映射回标题，再按标题子串归入方向
    for archive in ("resume_matched_jobs.json", "ai_filtered_nonoutsourced.json"):
        for job in load_json(Path(archive), []):
            pid = job.get("platformJobId")
            if pid and job.get("title"):
                id_to_title.setdefault(str(pid), str(job["title"]))

    for result_file in sorted(Path(".").glob("contact_results*.json")):
        for rec in load_json(result_file, []):
            status = str(rec.get("status") or "UNKNOWN")
            totals[status] += 1
            reason = str(rec.get("reason") or "")
            if status == "FAILED" and reason:
                failure_reasons[reason] += 1
            if status in ("SUCCEEDED", "FAILED"):
                title = id_to_title.get(str(rec.get("platformJobId") or ""), "")
                for direction in known_directions():
                    if direction.lower() in title.lower():
                        per_direction.setdefault(direction, Counter())[status] += 1

    return {
        "totals": dict(totals),
        "top_failure_reasons": dict(failure_reasons.most_common(TOP_FAILURE_REASONS)),
        "per_direction": {d: dict(c) for d, c in per_direction.items()},
    }


def collect_pool_stats() -> dict:
    """投递池与低分留档概况：匹配分分布、低分原因 TopN。"""
    matched = load_json(Path("resume_matched_jobs.json"), [])
    bands = Counter()
    for job in matched:
        score = (job.get("_match") or {}).get("score")
        if isinstance(score, (int, float)):
            bands["90+" if score >= 90 else f"{int(score) // 10 * 10}-{int(score) // 10 * 10 + 9}"] += 1
        else:
            bands["无分数"] += 1

    low_reasons = Counter()
    for job in load_json(Path("resume_low_score_jobs.json"), []):
        reason = str((job.get("_match") or {}).get("reason") or "").strip()
        if reason:
            low_reasons[reason[:120]] += 1

    return {
        "matched_pool_size": len(matched),
        "score_bands": dict(sorted(bands.items())),
        "low_score_count": sum(low_reasons.values()) or len(load_json(Path("resume_low_score_jobs.json"), [])),
        "top_low_score_reasons": dict(low_reasons.most_common(TOP_LOW_SCORE_REASONS)),
    }


def collect_rotation_state() -> dict:
    state = load_json(Path("discovery_rotation_state.json"), {})
    return {
        "cycle": state.get("cycle", 0),
        "remaining_directions": len(state.get("remaining", [])),
    }


def collect_log_signals() -> list[str]:
    """从流水线日志尾部抽取异常信号行，供 Agent 做异常归因（只读、有界）。"""
    if not PIPELINE_LOG.exists():
        return []
    try:
        lines = PIPELINE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        log(f"读取流水线日志失败（按无信号处理）：{e}")
        return []
    signals = [ln for ln in lines[-LOG_SIGNAL_TAIL_LINES:] if LOG_SIGNAL_PATTERN.search(ln)]
    return signals[-LOG_SIGNAL_LINES:]


def collect_evidence() -> dict:
    strategy = load_json(STRATEGY_FILE, {})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "current_threshold": strategy.get("score_threshold"),
        "current_direction_weights": strategy.get("direction_weights", {}),
        "delivery": collect_delivery_stats(),
        "pool": collect_pool_stats(),
        "rotation": collect_rotation_state(),
        "recent_log_signals": collect_log_signals(),
    }


# ============ LLM 顾问 ============

def build_prompt(evidence: dict) -> list[dict]:
    directions = known_directions()
    system_prompt = (
        "你是一条求职流水线的策略顾问。你只根据给定的运行数据做分析与建议，"
        "输出必须是严格 JSON（不要 Markdown 代码块、不要额外说明）。"
        "约束：score_threshold 只能给 50-80 的整数；direction_weights 只能调整给定的方向清单，"
        "权重范围 0.5-2.0（1.0=维持现状，>1=加大搜索投入，<1=减少）；"
        "不允许建议修改任何请求间隔、频率、重试、登录相关的参数。"
    )
    user_prompt = f"""【当前策略】
匹配阈值：{evidence['current_threshold']}
方向权重：{json.dumps(evidence['current_direction_weights'], ensure_ascii=False)}

【运行证据】（均为本项目真实数据）
{json.dumps({k: v for k, v in evidence.items() if k not in ('current_threshold', 'current_direction_weights')}, ensure_ascii=False, indent=1)}

请输出严格 JSON，字段如下：
{{
  "summary": "一句话总体判断（30 字内）",
  "score_threshold": 50-80 的整数（基于匹配池余量与近期投递成功率权衡）,
  "direction_weights": {{"方向名": 权重}},  // 只列你想调整的方向，没把握的不要列
  "exception_analysis": [{{"type": "异常类别", "evidence": "依据（引用日志/失败原因）", "suggestion": "给人工的处理建议"}}],
  "notes_for_human": ["值得人工关注的事项"]
}}
没有足够证据支撑的调整，宁可不调整。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_json_object(content: str) -> dict:
    """从 LLM 输出中提取 JSON 对象（容忍 ```json 围栏与首尾噪声）。"""
    cleaned = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("输出中找不到 JSON 对象")
        cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("输出不是 JSON 对象")
    return parsed


# ============ 校验与收敛（白名单 + 有界，拒绝项留痕）============

def validate_advice(raw: dict, directions: list[str]) -> tuple[dict, list[str]]:
    rejected: list[str] = []
    strategy: dict = {"direction_weights": {}}

    threshold = raw.get("score_threshold")
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        clamped = int(min(THRESHOLD_MAX, max(THRESHOLD_MIN, round(threshold))))
        if clamped != threshold:
            rejected.append(f"score_threshold={threshold} 越界，收敛为 {clamped}")
        strategy["score_threshold"] = clamped
    else:
        rejected.append(f"score_threshold={threshold!r} 非法，未采用")
        strategy["score_threshold"] = None

    weights = raw.get("direction_weights")
    if isinstance(weights, dict):
        for name, w in weights.items():
            if name not in directions:
                rejected.append(f"方向 {name!r} 不在预设清单，已丢弃")
                continue
            if not isinstance(w, (int, float)) or isinstance(w, bool):
                rejected.append(f"方向 {name!r} 权重 {w!r} 非法，已丢弃")
                continue
            clamped = round(min(WEIGHT_MAX, max(WEIGHT_MIN, float(w))), 2)
            if abs(clamped - float(w)) > 1e-9:
                rejected.append(f"方向 {name!r} 权重 {w} 越界，收敛为 {clamped}")
            if abs(clamped - 1.0) > 1e-9:  # 1.0 等于没建议，不落盘
                strategy["direction_weights"][name] = clamped
    elif weights is not None:
        rejected.append("direction_weights 不是对象，未采用")

    for key in ("summary", "exception_analysis", "notes_for_human"):
        value = raw.get(key)
        if key == "exception_analysis" and isinstance(value, list):
            strategy[key] = [v for v in value if isinstance(v, dict)][:10]
        elif key == "notes_for_human" and isinstance(value, list):
            strategy[key] = [str(v) for v in value][:10]
        elif isinstance(value, str):
            strategy[key] = value
        else:
            strategy[key] = [] if key != "summary" else ""

    return strategy, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 策略顾问（只建议、有界生效，不触碰执行层护栏）")
    parser.add_argument("--dry-run", action="store_true", help="只打印校验后的建议，不写 agent_strategy.json")
    args = parser.parse_args()

    from resume_matcher import call_deepseek, load_config

    config = load_config()
    evidence = collect_evidence()
    log(f"证据包：投递 {evidence['delivery']['totals']}，岗位池 {evidence['pool']['matched_pool_size']}，"
        f"周期 {evidence['rotation']['cycle']}，日志信号 {len(evidence['recent_log_signals'])} 条")

    log("调用 DeepSeek 生成策略建议...")
    content = call_deepseek(config, build_prompt(evidence), temperature=0.2, max_tokens=2000)
    raw = parse_json_object(content)

    strategy, rejected = validate_advice(raw, known_directions())
    strategy["updated_at"] = datetime.now().isoformat(timespec="seconds")
    strategy["rejected"] = rejected
    strategy["evidence_basis"] = {
        "delivery_totals": evidence["delivery"]["totals"],
        "pool_size": evidence["pool"]["matched_pool_size"],
        "log_signals": len(evidence["recent_log_signals"]),
    }

    log(f"建议摘要：{strategy['summary'] or '（无）'}")
    log(f"阈值建议：{strategy['score_threshold']}（生效区间 {THRESHOLD_MIN}-{THRESHOLD_MAX}）")
    log(f"方向权重：{strategy['direction_weights'] or '（无调整）'}")
    for item in rejected:
        log(f"已拒绝：{item}")

    if args.dry_run:
        log("--dry-run：未写文件")
        print(json.dumps(strategy, ensure_ascii=False, indent=2))
        return

    STRATEGY_FILE.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 {STRATEGY_FILE.resolve()}（供人工审阅；resume_matcher.py / job_discovery.py 消费时会再次校验）")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"策略顾问失败：{e}")
        sys.exit(1)
