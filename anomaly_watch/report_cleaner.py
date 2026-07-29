"""
AI-SUM 物理 Markdown 专报全分类最多保留 60 份清理引擎 (Report Cleaner)
包含 latest_*.md 保护锁与递归全分类清理支持
"""
from __future__ import annotations
import os
import glob
import logging

logger = logging.getLogger("report_cleaner")

def prune_category_reports(dir_path: str, max_keep: int = 60) -> int:
    """
    对指定目录下的 *.md 专报执行清理，只保留最新的 max_keep (60) 份。
    强制保护 latest_*.md 白名单。
    """
    if not os.path.isdir(dir_path):
        return 0

    # 匹配目录下所有 *.md 文件
    md_files = glob.glob(os.path.join(dir_path, "*.md"))
    
    # 过滤出非 latest_ 命名的历史专报文件
    history_files = [f for f in md_files if not os.path.basename(f).startswith("latest_")]

    # 按文件修改时间 mtime 降序排列 (最新的排前面)
    history_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    deleted_count = 0
    # 若超出 max_keep 份数，将多余的旧文件物理删除
    if len(history_files) > max_keep:
        to_delete = history_files[max_keep:]
        for fpath in to_delete:
            try:
                os.remove(fpath)
                deleted_count += 1
            except Exception as e:
                logger.error(f"[ReportCleaner] 删除旧报告失败: {fpath}, err: {e}")
        logger.info(f"[ReportCleaner] 目录 {dir_path} 物理清理完毕: 保留最新 {max_keep} 份，已删除 {deleted_count} 份旧专报")

    return deleted_count

def prune_all_aisum_reports(base_report_dir: str = "/opt/AI-SUM/report", max_keep: int = 60) -> int:
    """
    递归清理 /opt/AI-SUM/report 下所有子目录
    """
    total_deleted = 0
    for root, dirs, files in os.walk(base_report_dir):
        has_md = any(f.endswith(".md") for f in files)
        if has_md:
            total_deleted += prune_category_reports(root, max_keep=max_keep)
    return total_deleted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("开始对 /opt/AI-SUM/report/ 执行 60 份上限全局物理清理打靶...")
    cleaned = prune_all_aisum_reports()
    print(f"全局打靶清理完成，共物理剔除旧专报: {cleaned} 份！")
