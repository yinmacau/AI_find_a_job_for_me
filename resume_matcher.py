#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简历技术栈与岗位技能 AI 匹配
============================
在 VSCode 终端中运行：
    python resume_matcher.py

流程：
1. 读取 Word 简历，提取文本
2. 调用 Deepseek API 提取候选人的技术栈/核心能力
3. 读取岗位列表（ai_filtered_nonoutsourced.json）
4. 对每个岗位调用 AI，比较岗位所需技能与候选人技能，给出匹配分数
5. 过滤低于分数阈值的岗位，按分数排序
6. 生成 resume_matched_jobs.json 和 resume_greeting_messages.json
"""

import argparse
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from docx import Document


CONFIG_FILE = Path("resume_matcher_config.json")
# 低分岗位留档文件：低于阈值的岗位 + AI 给出的欠缺理由
LOW_SCORE_FILE = Path("resume_low_score_jobs.json")
# Agent 策略顾问产出（agent_advisor.py）：阈值建议的生效区间，越界一律忽略
AGENT_STRATEGY_FILE = Path("agent_strategy.json")
AGENT_THRESHOLD_MIN = 50
AGENT_THRESHOLD_MAX = 80


def apply_agent_threshold(default_threshold: int) -> int:
    """应用 Agent 顾问建议的匹配阈值（仅 50-80 之间的整数生效，其余用配置默认）。

    护栏：此值只影响筛岗打分，不影响 job_discovery / run_contact_sender 的任何节奏参数。
    """
    try:
        strategy = json.loads(AGENT_STRATEGY_FILE.read_text(encoding="utf-8"))
        suggested = strategy.get("score_threshold")
        if (
            isinstance(suggested, (int, float))
            and not isinstance(suggested, bool)
            and AGENT_THRESHOLD_MIN <= suggested <= AGENT_THRESHOLD_MAX
        ):
            suggested = int(suggested)
            if suggested != default_threshold:
                log(f"Agent 顾问阈值 {suggested} 生效（配置默认 {default_threshold}）")
            return suggested
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"读取 Agent 策略文件失败（用配置默认阈值）：{e}")
    return default_threshold

# 候选人默认具备的通用产品技能：匹配打分时不要因为 JD 里出现这些要求就扣分
DEFAULT_ASSUMED_SKILLS = [
    "PRD 撰写与需求文档",
    "Figma 等原型/设计协作工具",
    "AI 工具使用（ChatGPT、Claude、Coze、Kimi 等）",
]

# 每批送 AI 比对的岗位数：小批量可控制单次的 prompt 长度和输出预算，降低截断风险
MATCH_BATCH_SIZE = 3


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


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"找不到配置文件：{CONFIG_FILE.resolve()}")
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_resume_docx(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"找不到简历文件：{p.resolve()}")
    doc = Document(p)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def load_jobs(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"找不到岗位文件：{p.resolve()}")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def call_deepseek(config: dict, messages: list[dict], temperature: float | None = None, max_tokens: int | None = None, retries: int = 4) -> str:
    """调用 Deepseek Chat Completions API，返回 assistant 的文本内容。支持重试。

    空内容时读取 finish_reason 区分原因：
    - content_filter：内容触发安全过滤，重试无用，立即失败
    - length：输出被 max_tokens 截断，自动翻倍 max_tokens 重试（上限 MAX_TOKENS_CAP）
    - 其他（null/空）：服务端抖动/过载，等待后重试（参考官方文档建议等待后重试）
    """
    url = f"{config['api_base_url'].rstrip('/')}/chat/completions"
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature if temperature is not None else config.get("temperature", 0.5),
        "max_tokens": max_tokens if max_tokens is not None else config.get("max_tokens", 2000),
    }
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                choice = body["choices"][0]
                content = choice["message"]["content"]
                if not content or not content.strip():
                    finish = choice.get("finish_reason")
                    usage = body.get("usage", {})
                    if finish == "content_filter":
                        raise RuntimeError("DeepSeek 内容触发安全过滤（finish_reason=content_filter），重试无用，请检查输入文本")
                    if finish == "length":
                        # 输出被截断：翻倍 max_tokens 重试，给推理链留够预算
                        new_limit = min(payload["max_tokens"] * 2, MAX_TOKENS_CAP)
                        if new_limit <= payload["max_tokens"]:
                            raise RuntimeError(f"DeepSeek 输出被截断且 max_tokens 已达上限 {MAX_TOKENS_CAP}（usage={usage}）")
                        log(f"输出被 max_tokens 截断，翻倍至 {new_limit} 后重试（usage={usage}）")
                        payload["max_tokens"] = new_limit
                        last_error = RuntimeError(f"输出截断，已提升 max_tokens 至 {new_limit} 重试")
                        continue
                    raise RuntimeError(f"DeepSeek 返回空内容（finish_reason={finish}，usage={usage}），疑似服务端抖动/过载")
                return content
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            if e.code == 429:
                last_error = RuntimeError(f"Deepseek 触发限流（HTTP 429）: {err}")
            else:
                last_error = RuntimeError(f"Deepseek API 错误 {e.code}: {err}")
        except Exception as e:
            last_error = e

        # content_filter 重试无用，直接抛出；length 已在上面的分支处理（continue）
        if last_error and ("重试无用" in str(last_error)):
            raise last_error

        if attempt < retries - 1:
            wait = 10 * (2 ** attempt)  # 10s / 20s / 40s / 80s，给服务端足够恢复时间
            log(f"API 调用失败，{wait} 秒后重试（{attempt + 1}/{retries}）：{last_error}")
            time.sleep(wait)

    raise RuntimeError(f"Deepseek API 调用失败（已重试 {retries} 次）: {last_error}")


# finish_reason=length（输出被截断）时自动翻倍 max_tokens 重试的上限
MAX_TOKENS_CAP = 16000


def extract_skills(config: dict, resume_text: str) -> dict:
    """从简历中提取技术栈和核心能力。"""
    log("调用 AI 提取简历技术栈...")
    system_prompt = (
        "你是一名专业的人才分析助手。请仔细阅读候选人的简历，提取其技术栈、工具、方法论和核心能力。"
        "输出必须是严格 JSON，不要包含 Markdown 代码块或额外说明；解释和总结务必精简。"
    )
    user_prompt = f"""【简历内容】
{resume_text}

请提取以下信息，按 JSON 格式返回：
{{
  "tech_stack": ["技术/工具名1", "技术/工具名2", ...],
  "methodologies": ["方法论/能力1", "方法论/能力2", ...],
  "domains": ["领域1", "领域2", ...],
  "summary": "50字以内的候选人核心优势总结"
}}
"""
    content = call_deepseek(config, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    # 清理可能的 ```json 包裹
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1].strip()
    return json.loads(cleaned)


def match_job(config: dict, candidate_skills: dict, job: dict) -> dict:
    """单个岗位与候选人技能做 AI 匹配，返回分数和理由。"""
    job_title = job.get("title", "")
    job_description = job.get("description", "")
    job_skills = job.get("facts", {}).get("skills", [])
    job_labels = job.get("facts", {}).get("labels", [])

    system_prompt = (
        "你是一名专业的招聘匹配顾问。请根据候选人的技术栈和岗位的技能要求，判断匹配程度。"
        "输出必须是严格 JSON，不要包含 Markdown 代码块或额外说明；匹配理由务必精简。"
    )
    user_prompt = f"""【候选人技能】
技术栈：{', '.join(candidate_skills.get('tech_stack', []))}
方法论/能力：{', '.join(candidate_skills.get('methodologies', []))}
领域：{', '.join(candidate_skills.get('domains', []))}
核心优势：{candidate_skills.get('summary', '')}

【默认具备的通用技能】
候选人默认具备以下通用产品技能，JD 中出现这些要求时视为已满足，不要因此扣分或列入 missing_skills：
{', '.join(DEFAULT_ASSUMED_SKILLS)}

【岗位信息】
岗位名称：{job_title}
岗位描述/标签：{job_description} {' '.join(job_labels)}
岗位要求技能：{', '.join(job_skills) if job_skills else '未明确列出'}

请按以下 JSON 格式返回匹配结果：
{{
  "score": 0-100 的整数,
  "reason": "30-60字的精简匹配理由，说明哪些技能对口、哪些有差距，不要展开论述",
  "matched_skills": ["匹配上的技能1", "匹配上的技能2"],
  "missing_skills": ["岗位需要但候选人未明确体现的技能1", ...],
  "recommendation": "推荐投递" 或 "谨慎投递" 或 "不推荐"
}}
"""
    content = call_deepseek(config, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1].strip()
    return json.loads(cleaned)


def match_jobs_batch(config: dict, candidate_skills: dict, jobs_batch: list[dict]) -> list[dict]:
    """一批岗位（MATCH_BATCH_SIZE 个）与候选人技能一次性 AI 比对，返回与输入等长的结果列表。"""
    system_prompt = (
        "你是一名专业的招聘匹配顾问。请根据候选人的技术栈，逐一判断每个岗位的匹配程度。"
        "输出必须是严格 JSON 数组，不要包含 Markdown 代码块或额外说明；匹配理由务必精简。"
    )
    jobs_text = "\n\n".join(
        f"【岗位{i}】\n名称：{j.get('title', '')}\n公司：{j.get('company', '')}\n描述/标签：{j.get('description', '')}\n要求技能：{', '.join(j.get('facts', {}).get('skills', [])) or '未明确列出'}"
        for i, j in enumerate(jobs_batch)
    )
    user_prompt = f"""【候选人技能】
技术栈：{', '.join(candidate_skills.get('tech_stack', []))}
方法论/能力：{', '.join(candidate_skills.get('methodologies', []))}
领域：{', '.join(candidate_skills.get('domains', []))}
核心优势：{candidate_skills.get('summary', '')}

【默认具备的通用技能】
候选人默认具备以下通用产品技能，JD 中出现这些要求时视为已满足，不要因此扣分或列入 missing_skills：
{', '.join(DEFAULT_ASSUMED_SKILLS)}

{jobs_text}

请逐一评估每个岗位，按以下 JSON 数组格式返回（数组长度必须与岗位数量一致，index 与岗位编号对应）：
[
  {{
    "index": 0,
    "score": 0-100 的整数,
    "reason": "30-60字的精简匹配理由，说明哪些技能对口、哪些有差距，不要展开论述",
    "matched_skills": ["匹配上的技能1", "匹配上的技能2"],
    "missing_skills": ["岗位需要但候选人未明确体现的技能1"],
    "recommendation": "推荐投递" 或 "谨慎投递" 或 "不推荐"
  }},
  ...
]"""
    content = call_deepseek(config, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1].strip()
    results = json.loads(cleaned)
    if not isinstance(results, list):
        raise RuntimeError("AI 返回的不是列表")

    # 按 index 对齐到输入岗位；AI 漏掉的岗位兜底为 0 分，不静默丢失
    by_index = {}
    for position, item in enumerate(results):
        if isinstance(item, dict):
            by_index[item.get("index", position)] = item
    return [
        by_index.get(i, {
            "score": 0,
            "reason": "AI 未返回该岗位的比对结果",
            "matched_skills": [],
            "missing_skills": [],
            "recommendation": "不推荐",
        })
        for i in range(len(jobs_batch))
    ]


def generate_greeting(config: dict, candidate_skills: dict, job: dict, match_result: dict) -> str:
    """为匹配岗位生成详细打招呼语：对比任职要求，把候选人具备的相关技术栈写进去。"""
    job_skills = job.get("facts", {}).get("skills", [])
    job_labels = job.get("facts", {}).get("labels", [])
    matched_skills = match_result.get("matched_skills", [])
    missing_skills = match_result.get("missing_skills", [])

    system_prompt = (
        "你是一名求职顾问。请根据候选人技术栈与岗位任职要求的比对结果，写一段详细、专业的中文打招呼语。"
        "语气自然真诚、不夸张，像求职者本人写的，不要模板腔。"
        "只输出纯文本招呼语本身，不要 JSON、Markdown、称呼落款或任何额外说明。"
    )
    user_prompt = f"""【候选人技能清单】
{json.dumps(candidate_skills, ensure_ascii=False, indent=2)}

【岗位信息】
岗位：{job.get('title', '')}
公司：{job.get('company', '')}
城市：{job.get('city', '')}
薪资：{job.get('salary', '')}

【岗位任职要求/描述】
{job.get('description', '')} {' '.join(job_labels)}
岗位要求技能：{', '.join(job_skills) if job_skills else '未明确列出'}

【简历与岗位比对结果】
匹配分数：{match_result.get('score', 0)}
已匹配上的技能：{', '.join(matched_skills) if matched_skills else '无'}
岗位要求但候选人未明确体现的技能（写招呼语时不要提及这些短板）：{', '.join(missing_skills) if missing_skills else '无'}
匹配分析：{match_result.get('reason', '')}

请写一段 120-200 字的打招呼语，要求：
1. 开头说明对该岗位感兴趣，并体现出你理解这个岗位的核心要求
2. 中间自然带出 2-4 个与任职要求对应的技术栈/能力（从「已匹配上的技能」和候选人技能清单中选最相关的），说明自己能直接上手
3. 结尾表达希望进一步沟通的意愿
4. 不要提及任何欠缺的技能，不要夸大，不要堆砌所有技能"""
    return call_deepseek(config, [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]).strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="简历技术栈与岗位技能 AI 匹配")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多匹配多少个岗位（默认 0 表示全部），用于测试",
    )
    parser.add_argument(
        "--no-greeting",
        action="store_true",
        help="只生成匹配分数，不生成招呼语（节省 Token）",
    )
    parser.add_argument(
        "--exclude-keywords",
        type=str,
        default="实习生,实习",
        help="排除包含这些关键词的岗位，逗号分隔（默认：实习生,实习）",
    )
    parser.add_argument(
        "--rescore-missing",
        action="store_true",
        help="补评模式：读取已有的匹配/低分/招呼语结果，只对之前没评上分的岗位调用 AI，完成后合并写回",
    )
    return parser.parse_args()


def load_previous_results(config: dict) -> tuple[list[dict], list[dict], list[dict], set[str]]:
    """读取已有的匹配结果、低分留档和招呼语，返回 (matched_jobs, low_score_jobs, greetings, 已评分 href 集合)。"""
    matched, low, greetings, scored = [], [], [], set()
    try:
        with Path(config["output_file"]).open("r", encoding="utf-8") as f:
            matched = json.load(f)
        scored.update(j["href"] for j in matched if j.get("href"))
    except Exception as e:
        log(f"读取已有匹配结果失败（将全量重评）：{e}")
    try:
        with Path(LOW_SCORE_FILE).open("r", encoding="utf-8") as f:
            low = json.load(f)
        scored.update(j["href"] for j in low if j.get("href"))
    except Exception:
        pass
    try:
        with Path(config["greetings_output_file"]).open("r", encoding="utf-8") as f:
            greetings = json.load(f)
    except Exception:
        pass
    return matched, low, greetings, scored


def main() -> None:
    args = parse_args()

    log("加载配置...")
    config = load_config()

    log("读取简历...")
    resume_text = read_resume_docx(config["resume_path"])
    log(f"简历读取完成，共 {len(resume_text)} 字")

    log("读取岗位列表...")
    jobs = load_jobs(config["jobs_file"])
    log(f"共 {len(jobs)} 个岗位")

    exclude_keywords = [kw.strip() for kw in args.exclude_keywords.split(",") if kw.strip()]
    jobs, skipped_count = filter_excluded_jobs(jobs, exclude_keywords)
    if skipped_count:
        log(f"因含排除关键词跳过 {skipped_count} 个，剩余 {len(jobs)} 个")
    if not jobs:
        raise RuntimeError("过滤后没有可用岗位")

    log("提取候选人技术栈...")
    candidate_skills = extract_skills(config, resume_text)
    log(f"技术栈：{', '.join(candidate_skills.get('tech_stack', []))}")
    log(f"方法论：{', '.join(candidate_skills.get('methodologies', []))}")

    threshold = apply_agent_threshold(config.get("score_threshold", 60))
    matched_jobs = []
    low_score_jobs = []
    greetings = []
    scored_hrefs: set[str] = set()

    if args.rescore_missing:
        matched_jobs, low_score_jobs, greetings, scored_hrefs = load_previous_results(config)
        log(f"补评模式：已有 {len(matched_jobs)} 个匹配、{len(low_score_jobs)} 个低分留档，只评剩余岗位")

    jobs_to_process = [j for j in jobs if j.get("href") not in scored_hrefs] if args.rescore_missing else jobs
    jobs_to_process = jobs_to_process[: args.limit] if args.limit > 0 else jobs_to_process
    if not jobs_to_process:
        log("没有需要补评的岗位")
    total = len(jobs_to_process)
    total_batches = (total + MATCH_BATCH_SIZE - 1) // MATCH_BATCH_SIZE

    for batch_start in range(0, total, MATCH_BATCH_SIZE):
        batch = jobs_to_process[batch_start:batch_start + MATCH_BATCH_SIZE]
        batch_no = batch_start // MATCH_BATCH_SIZE + 1
        log(f"===== 第 {batch_no}/{total_batches} 批（{len(batch)} 个岗位）=====")
        for offset, job in enumerate(batch):
            log(f"[{batch_start + offset + 1}/{total}] 匹配岗位：{job.get('title')} @ {job.get('company')}")

        try:
            batch_results = match_jobs_batch(config, candidate_skills, batch)
        except Exception as e:
            log(f"本批批量匹配失败（{e}），降级为逐个岗位匹配")
            batch_results = []
            for job in batch:
                try:
                    batch_results.append(match_job(config, candidate_skills, job))
                except Exception as e2:
                    log(f"  岗位匹配失败：{job.get('title')} @ {job.get('company')}（{e2}）")
                    batch_results.append({
                        "score": 0,
                        "recommendation": "不推荐",
                        "reason": f"AI 匹配失败: {e2}",
                        "matched_skills": [],
                        "missing_skills": [],
                    })
            time.sleep(2)

        for offset, (job, match_result) in enumerate(zip(batch, batch_results)):
            idx = batch_start + offset + 1
            score = match_result.get("score", 0)
            log(f"[{idx}/{total}] 匹配分数：{score}，建议：{match_result.get('recommendation', '')}")

            if score >= threshold:
                if not args.no_greeting:
                    greeting = generate_greeting(config, candidate_skills, job, match_result)
                else:
                    greeting = ""
                job_with_match = {**job, "_match": match_result}
                matched_jobs.append(job_with_match)
                greetings.append({
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "city": job.get("city"),
                    "salary": job.get("salary"),
                    "href": job.get("href"),
                    "message": greeting,
                })
            else:
                log(f"[{idx}/{total}] 分数低于 {threshold}，跳过")
                low_score_jobs.append({**job, "_match": match_result})

        # 两次 AI 调用之间稍作停顿，降低触发频率限制的概率
        time.sleep(1)

    # 按分数降序
    matched_jobs.sort(key=lambda x: x.get("_match", {}).get("score", 0), reverse=True)

    # 保存结果
    output_file = Path(config["output_file"])
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(matched_jobs, f, ensure_ascii=False, indent=2)
    log(f"匹配结果已保存：{output_file.resolve()}")

    greetings_file = Path(config["greetings_output_file"])
    with greetings_file.open("w", encoding="utf-8") as f:
        json.dump(greetings, f, ensure_ascii=False, indent=2)
    log(f"招呼语已保存：{greetings_file.resolve()}")

    # 低分岗位留档：按分数升序（最低分在最前），便于复盘调阈值
    low_score_jobs.sort(key=lambda x: x.get("_match", {}).get("score", 0))
    with LOW_SCORE_FILE.open("w", encoding="utf-8") as f:
        json.dump(low_score_jobs, f, ensure_ascii=False, indent=2)
    log(f"低分岗位留档已保存：{LOW_SCORE_FILE.resolve()}（共 {len(low_score_jobs)} 个）")

    log("=" * 40)
    log(f"匹配完成：共 {len(matched_jobs)} 个岗位达到 {threshold} 分阈值，{len(low_score_jobs)} 个低于阈值已留档")
    for job in matched_jobs:
        match = job.get("_match", {})
        log(f"  {match.get('score', 0)}分 | {job.get('title')} @ {job.get('company')}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"脚本异常退出：{e}")
        raise
