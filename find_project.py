#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测 npe_get_jobs 项目位置
==========================
在 VSCode 终端中运行：
    python find_project.py

会在常见位置搜索项目根目录（判断依据：包含 target/npe_get_jobs-*.jar 或 run_contact_sender.py）。
"""

import os
import glob
from pathlib import Path


def is_project_root(path: Path) -> bool:
    """判断一个目录是否是 npe_get_jobs 项目根目录。"""
    if not path.is_dir():
        return False
    # 判断依据 1：包含 run_contact_sender.py
    if (path / "run_contact_sender.py").exists():
        return True
    # 判断依据 2：target 目录下有 npe_get_jobs-*.jar
    if list(path.glob("target/npe_get_jobs-*.jar")):
        return True
    return False


def find_project() -> list[Path]:
    """在常见位置搜索项目。"""
    candidates = []

    # 1. 当前工作目录
    candidates.append(Path.cwd())

    # 2. 当前工作目录的父目录（可能项目放在某个文件夹里）
    candidates.append(Path.cwd().parent)

    # 3. 用户主目录
    home = Path.home()
    candidates.append(home)

    # 4. 用户主目录下的一级子目录
    if home.is_dir():
        for child in home.iterdir():
            if child.is_dir():
                candidates.append(child)

    # 去重并检测
    found = []
    seen = set()
    for cand in candidates:
        resolved = cand.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_project_root(resolved):
            found.append(resolved)

    return found


def main():
    print("正在检测 npe_get_jobs 项目位置...")
    found = find_project()

    if found:
        print(f"\n找到 {len(found)} 个项目位置：")
        for idx, path in enumerate(found, 1):
            print(f"  {idx}. {path}")
    else:
        print("\n未在常见位置找到 npe_get_jobs 项目。")
        print("请把脚本放到项目根目录运行，或手动指定路径。")


if __name__ == "__main__":
    main()
