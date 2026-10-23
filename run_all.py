#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
求职自动化流水线（一键按顺序执行）
====================================
执行顺序：
1. job_discovery.py --match-resume   （岗位发现 + AI 简历匹配）
2. run_contact_sender.py             （批量打招呼投递）

每个阶段跑完后会输出「请按顺序执行下一个文件：xxx」并暂停，
按回车继续，输入 q 退出。加 --auto 则全自动不暂停。
"""

import argparse
import subprocess
import sys
from pathlib import Path

STEPS = [
    {
        "name": "岗位发现 + 简历匹配",
        "file": "job_discovery.py",
        "cmd": [sys.executable, "job_discovery.py", "--match-resume"],
    },
    {
        "name": "批量投递",
        "file": "run_contact_sender.py",
        "cmd": [
            sys.executable, "run_contact_sender.py",
            "--jobs-file", "resume_matched_jobs.json",
            "--greetings-file", "resume_greeting_messages.json",
        ],
    },
]


def run_step(step: dict) -> bool:
    """执行一个阶段，返回是否成功。"""
    print("=" * 50)
    print(f"当前执行 [{step['name']}]：{step['file']}")
    print("=" * 50)
    result = subprocess.run(step["cmd"], cwd=Path.cwd())
    return result.returncode == 0


def confirm_next(next_step: dict | None) -> bool:
    """暂停并提示下一个文件；返回 False 表示用户退出。"""
    if next_step is None:
        return True
    try:
        answer = input(
            f"\n请按顺序执行下一个文件：{next_step['file']}（{next_step['name']}）\n"
            f"按回车继续，输入 q 退出："
        )
    except EOFError:
        return True
    return answer.strip().lower() != "q"


def main() -> None:
    parser = argparse.ArgumentParser(description="求职自动化流水线")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="全自动模式：阶段之间不暂停，一口气跑完",
    )
    args = parser.parse_args()

    print("求职自动化流水线启动")
    print(f"共 {len(STEPS)} 个阶段，按顺序执行：")
    for index, step in enumerate(STEPS, start=1):
        print(f"  {index}. {step['file']}（{step['name']}）")
    print()

    for index, step in enumerate(STEPS):
        if not run_step(step):
            print(f"\n✗ 阶段「{step['name']}」失败，流水线中止。修复问题后可重跑本脚本（已完成的进度会保留）")
            sys.exit(1)

        # 阶段 1 完成后检查是否真的有岗位可投
        if index == 0:
            matched_file = Path("resume_matched_jobs.json")
            if not matched_file.exists():
                print("\n未生成 resume_matched_jobs.json，没有可投递岗位，流水线结束")
                return
            try:
                import json
                if not json.load(matched_file.open("r", encoding="utf-8")):
                    print("\n本轮没有发现可投递的新岗位，流水线结束")
                    return
            except Exception:
                pass

        next_step = STEPS[index + 1] if index + 1 < len(STEPS) else None
        if next_step is not None and not args.auto:
            if not confirm_next(next_step):
                print(f"\n已手动退出。之后想继续，直接运行：python {next_step['file']}")
                return

    print("\n✓ 流水线全部完成：发现 → 匹配 → 投递")


if __name__ == "__main__":
    main()
