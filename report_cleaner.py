#!/usr/bin/env python3
"""
AI-SUM 报告清理器 — 确立 meta 仲裁总览为唯一 SSOT 面板，子报告全面瘦身
清理规则:
1. 子引擎 (v5, opus, unified, whale, cost-basis, pump, history): 降级为特征供能层，保留上限压缩至 5 份 (3~7 份区间)。
2. meta 总览驾驶舱: 作为全景唯一决策面板 SSOT，保留 60 份以支持回测与长效复盘；latest_meta_dashboard.md 物理锁保护。
3. 周期报告: weekly 保留 8 份，monthly 保留 12 份。
"""
import os, sys, shutil, argparse, re
from pathlib import Path
from datetime import datetime

REPORT_DIR = "/opt/AI-SUM/report"

# 清理规则: (目录, glob模式, 保留份数)
RULES = [
    ("v5",         "radar_*.md",     5),   # 压缩至 5 份
    ("opus",       "opus_*.md",      5),   # 压缩至 5 份
    ("unified",    "unified_*.md",   5),   # 压缩至 5 份
    ("whale",      "whale_*.md",     5),   # 压缩至 5 份
    ("meta",       "meta_*.md",      60),  # meta 驾驶舱 SSOT，保留 60 份支持回测
    ("cost-basis", "cb_*.md",        5),   # 压缩至 5 份
    ("pump",       "pump_*.md",      5),   # 压缩至 5 份
    ("history",    "history_*.md",   5),   # 压缩至 5 份
    ("history",    "tracking_*.md",  5),   # 压缩至 5 份
    ("weekly",     "week_*.md",      8),   # 保留 8 周
    ("monthly",    "month_*.md",     12),  # 保留 12 个月
]

PROTECTED_FILES = {
    "latest_meta_dashboard.md",
    "latest_meta_dashboard.md.bak"
}


def clean_category(subdir, pattern, keep, dry_run=False, verbose=False):
    """清理一个类别，保留最新 keep 份"""
    dirpath = Path(REPORT_DIR) / subdir
    if not dirpath.is_dir():
        return 0
    files = sorted(dirpath.glob(pattern), reverse=True)  # 文件名含日期，倒序=最新在前
    # 过滤受保护文件
    valid_files = [f for f in files if f.name not in PROTECTED_FILES]
    to_delete = valid_files[keep:]
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
            if f.name in PROTECTED_FILES:
                continue
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
    print(f"[{ts}] report_cleaner 开始 {'(dry-run)' if args.dry_run else ''} [SSOT: meta 驾驶舱]")

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
