"""
AI-SUM 物理 Markdown 专报全分类精确 60 份轮询覆盖引擎 (Report Cleaner)
实现每种报告类型物理只保留最新的 60 份专报，后生成的轮询覆盖淘汰最早旧报告。
"""
from __future__ import annotations
import os
import glob
import logging

logger = logging.getLogger("report_cleaner")

# 物理分类目录清单 (包含 AI-SUM 物理磁盘下的所有报告分类)
AI_SUM_CATEGORIES_PATHS = [
    "/opt/AI-SUM/report/meta",
    "/opt/AI-SUM/report/anomaly",
    "/opt/AI-SUM/report/anomaly/penetration",
    "/opt/AI-SUM/report/anomaly/pump_resonance",
    "/opt/AI-SUM/report/whale",
    "/opt/AI-SUM/report/unified",
    "/opt/AI-SUM/report/cost-basis",
    "/opt/AI-SUM/report/history",
    "/opt/AI-SUM/report/pump",
    "/opt/AI-SUM/report/weekly",
    "/opt/AI-SUM/report/monthly",
    "/opt/AI-SUM/report/opus",
    "/opt/AI-SUM/report/v5",
]

def prune_category_reports(dir_path: str, max_keep: int = 60) -> int:
    """
    对指定分类目录下的 *.md 专报执行精确 60 份轮询覆盖。
    只保留修改时间 mtime 最新 60 份历史专报。
    强力保留 latest_*.md。
    """
    if not os.path.isdir(dir_path):
        return 0

    # 仅获取该目录直接包含的 *.md 文件 (不包含深层子目录文件)
    md_files = [
        os.path.join(dir_path, f) for f in os.listdir(dir_path)
        if f.endswith(".md") and os.path.isfile(os.path.join(dir_path, f))
    ]
    
    # 过滤出非 latest_ 命名的历史专报文件
    history_files = [f for f in md_files if not os.path.basename(f).startswith("latest_")]

    # 按文件修改时间 mtime 降序排列 (最新的排前面，最旧的排后面)
    history_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)

    deleted_count = 0
    # 若历史文件数量超过 60 份，强制淘汰多出的最旧报告 (轮询覆盖)
    if len(history_files) > max_keep:
        to_delete = history_files[max_keep:]
        for fpath in to_delete:
            try:
                os.remove(fpath)
                deleted_count += 1
                # 同时也清除同名的 .json 文件以防残留堆积
                json_path = fpath.replace(".md", ".json")
                if os.path.exists(json_path):
                    os.remove(json_path)
            except Exception as e:
                logger.error(f"[ReportCleaner] 轮询删除旧报告失败: {fpath}, err: {e}")
        logger.info(f"[ReportCleaner] 分类 [{dir_path}] 轮询覆盖完毕: 当前保留 60 份，已淘汰物理覆盖 {deleted_count} 份旧专报")

    return deleted_count

def prune_all_aisum_reports(base_report_dir: str = "/opt/AI-SUM/report", max_keep: int = 60) -> int:
    """
    对所有独立的 AI-SUM 报告分类执行精确 60 份轮询覆盖
    """
    total_deleted = 0
    for cat_path in AI_SUM_CATEGORIES_PATHS:
        if os.path.exists(cat_path):
            total_deleted += prune_category_reports(cat_path, max_keep=max_keep)
    
    # 遍历可能遗漏的其它新子目录
    for root, dirs, files in os.walk(base_report_dir):
        if root not in AI_SUM_CATEGORIES_PATHS:
            has_md = any(f.endswith(".md") for f in files)
            if has_md:
                total_deleted += prune_category_reports(root, max_keep=max_keep)
                
    return total_deleted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("开始对 AI-SUM 所有分类专报执行【每种类型精确 60 份】轮询覆盖打靶...")
    cleaned = prune_all_aisum_reports()
    print(f"轮询覆盖打靶完成，共物理淘汰淘汰旧专报: {cleaned} 份！")
