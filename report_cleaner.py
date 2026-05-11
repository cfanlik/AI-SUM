#!/usr/bin/env python3
"""AI-SUM 报告清理器 — 各类别保留上限，删除多余旧文件"""
import os, sys, shutil, argparse, re
from pathlib import Path
from datetime import datetime

REPORT_DIR = "/opt/AI-SUM/report"

# 清理规则: (目录, glob模式, 保留份数)
RULES = [
    ("v5",         "radar_*.md",     60),
    ("opus",       "opus_*.md",      60),
    ("unified",    "unified_*.md",   60),
    ("whale",      "whale_*.md",     60),
    ("meta",       "meta_*.md",      60),
    ("cost-basis", "cb_*.md",        60),
    ("pump",       "pump_*.md",      60),
    ("history",    "history_*.md",   60),
    ("history",    "tracking_*.md",  60),
    ("weekly",     "week_*.md",      60),
    ("monthly",    "month_*.md",     24),
]


def clean_category(subdir, pattern, keep, dry_run=False, verbose=False):
    """清理一个类别，保留最新 keep 份"""
    dirpath = Path(REPORT_DIR) / subdir
    if not dirpath.is_dir():
        return 0
    files = sorted(dirpath.glob(pattern), reverse=True)  # 文件名含日期，倒序=最新在前
    to_delete = files[keep:]
    for f in to_delete:
        if verbose or dry_run:
            print(f"  {'[DRY] ' if dry_run else ''}删除 {subdir}/{f.name}")
        if not dry_run:
            f.unlink()
    return len(to_delete)


def clean_legacy(dry_run=False, verbose=False):
    """清理旧格式时间戳目录 + 非标准前缀文件"""
    deleted = 0
    # 旧格式目录: report/20260407_1840/ 等
    for item in Path(REPORT_DIR).iterdir():
        if item.is_dir() and re.match(r'^\d{8}_\d{4}$', item.name):
            if verbose or dry_run:
                print(f"  {'[DRY] ' if dry_run else ''}删除目录 {item.name}/")
            if not dry_run:
                shutil.rmtree(item)
            deleted += 1
    # 非标准前缀文件(opus目录的 AGT_*, IRYS_* 等)
    for subdir in ["opus", "whale"]:
        dirpath = Path(REPORT_DIR) / subdir
        if not dirpath.is_dir():
            continue
        for f in dirpath.iterdir():
            if f.suffix == ".md" and not any(
                f.name.startswith(p) for p in ["opus_", "whale_", "cb_", "meta_",
                    "radar_", "unified_", "pump_", "history_", "tracking_", "week_", "month_"]
            ):
                if verbose or dry_run:
                    print(f"  {'[DRY] ' if dry_run else ''}删除非标准 {subdir}/{f.name}")
                if not dry_run:
                    f.unlink()
                deleted += 1
    return deleted


def main():
    parser = argparse.ArgumentParser(description="AI-SUM 报告清理器")
    parser.add_argument("--dry-run", action="store_true", help="仅打印不删除")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    print(f"[{ts}] report_cleaner 开始 {'(dry-run)' if args.dry_run else ''}")

    # 按规则清理
    for subdir, pattern, keep in RULES:
        n = clean_category(subdir, pattern, keep, args.dry_run, args.verbose)
        if n > 0:
            print(f"  {subdir}/{pattern}: 删除 {n} 份 (保留 {keep})")
        total += n

    # 清理旧格式
    n = clean_legacy(args.dry_run, args.verbose)
    total += n

    print(f"[{ts}] 清理完成: 共删除 {total} 项")


if __name__ == "__main__":
    main()
