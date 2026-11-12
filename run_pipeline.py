#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-SUM 串行流水线调度器 (v6.0 新鲜度门禁与防重入锁增强版)
1. 单例排他文件锁 (/tmp/ai_sum_pipeline.lock) 杜绝多实例并发引发 OOM 共振
2. 前置新鲜度门禁 (Staleness Gate) 拦截无新批次的无效全量重算
3. 保证同一时间仅有一个任务在跑，上游事件驱动与固定 cron 保底协同
"""
import subprocess
import time
import sys
import os
import fcntl
import json
import sqlite3
import argparse

STEPS = [
    ("master-scan", ["python3", "master-scan/run.py"]),
    ("opus-scan", ["python3", "opus-scan/run.py", "--offline"]),
    ("unified-scan", ["python3", "unified-scan/run.py", "--offline"]),
    ("bigcoin", ["python3", "bigcoin/run.py"]),
    # Phase 0-A/C2 下线: 原依赖已删除的 GMGN gmgn_avg_price 均价数据
    ("meta-verdict", ["python3", "meta-verdict/run.py"]),
    ("history-report", ["python3", "meta-verdict/history_report.py"]),
    ("materialize-surge", ["python3", "meta-verdict/materialize_surge.py"]),
    ("anomaly-surge-report", ["python3", "meta-verdict/anomaly_surge_report.py"])
]

LOCK_FILE = "/tmp/ai_sum_pipeline.lock"

def acquire_lock():
    """获取进程级排他文件锁，非阻塞模式。"""
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except (BlockingIOError, OSError):
        return None

def check_stale_gate(root_dir: str, force: bool = False):
    """前置新鲜度门禁检测。
    读取上游 select.db 最新 batch_id，与上次成功处理批次比对。
    若无更新且未强制指定 --force，则拦截跳过。
    """
    if force:
        print("[STALE-GATE] ⚡ 强制执行模式 (--force)，跳过新鲜度门禁")
        return True, "force_run", ""

    # 1. 尝试定位上游 select.db
    candidate_dbs = [
        "/opt/select-coin/data/select.db",
        os.path.join(os.path.dirname(root_dir), "select-coin", "data", "select.db"),
    ]
    db_path = None
    for c in candidate_dbs:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            db_path = c
            break

    if not db_path:
        print("[STALE-GATE] ⚠️ 未发现上游 select.db，放行流水线")
        return True, "unknown_db", ""

    # 2. 查最新 batch_id (利用主键倒序索引极速查询)
    latest_batch_id = ""
    latest_snap_time = ""
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        c = conn.cursor()
        c.execute("SELECT batch_id, snapshot_time FROM bubblemap_holders ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            latest_batch_id = row[0]
            latest_snap_time = row[1]
    except Exception as e:
        print(f"[STALE-GATE] ⚠️ 读取最新批次异常: {e}，放行流水线")
        return True, "query_error", ""

    if not latest_batch_id:
        print("[STALE-GATE] ⚠️ 未读取到有效批次数据，放行流水线")
        return True, "empty_batch", ""

    # 3. 读取已处理历史记录
    record_file = os.path.join(root_dir, "log", "last_processed_batch.json")
    if os.path.exists(record_file):
        try:
            with open(record_file, "r", encoding="utf-8") as f:
                last_record = json.load(f)
            last_batch = last_record.get("batch_id")
            last_status = last_record.get("status")
            if last_batch == latest_batch_id and last_status == "success":
                msg = (f"[STALE-GATE] ⏭️ 上游最新批次 [{latest_batch_id}] ({latest_snap_time}) "
                       f"已在 {last_record.get('processed_at')} 成功分析过，数据未发生更新。跳过本次重复计算。")
                print(msg)
                return False, latest_batch_id, latest_snap_time
        except Exception as e:
            print(f"[STALE-GATE] 读取批次历史异常: {e}，放行")

    print(f"[STALE-GATE] ✅ 检测到待分析新批次 [{latest_batch_id}] ({latest_snap_time})，准予执行流水线")
    return True, latest_batch_id, latest_snap_time

def record_processed_batch(root_dir: str, batch_id: str, snap_time: str, status: str = "success"):
    """持久化成功处理的批次信息"""
    if not batch_id or batch_id in ("unknown_db", "query_error", "empty_batch"):
        return
    log_dir = os.path.join(root_dir, "log")
    os.makedirs(log_dir, exist_ok=True)
    record_file = os.path.join(log_dir, "last_processed_batch.json")
    try:
        data = {
            "batch_id": batch_id,
            "snapshot_time": snap_time,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status
        }
        with open(record_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[STALE-GATE] 写入批次记录异常: {e}")

def run_pipeline(force: bool = False, source: str = "auto"):
    root_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(root_dir, "log")
    os.makedirs(log_dir, exist_ok=True)
    pipeline_log = os.path.join(log_dir, "pipeline.log")

    # 1. 尝试获取排他文件锁
    lock_fd = acquire_lock()
    if lock_fd is None:
        msg = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ 检测到已有 AI-SUM 流水线正在运行中，拒绝并发重入退出 (source: {source})"
        print(msg)
        with open(pipeline_log, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n{msg}\n")
        sys.exit(0)

    try:
        # 2. 前置新鲜度门禁检测
        passed, current_batch, current_snap_time = check_stale_gate(root_dir, force=force)
        if not passed:
            with open(pipeline_log, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] [STALE-GATE] 批次 {current_batch} 已处理，跳过重复重算 (source: {source})\n")
            sys.exit(0)

        # 3. 开始执行 9 个核心分析步骤
        print(f"=== 开始执行 AI-SUM 串行流水线 ===\n工作目录: {root_dir}\n日志文件: {pipeline_log}\n批次ID: {current_batch}\n触发源: {source}")
        t_start = time.time()

        with open(pipeline_log, "a", encoding="utf-8") as log_file:
            log_file.write(f"\n\n========================================\n")
            log_file.write(f"Pipeline started at {time.strftime('%Y-%m-%d %H:%M:%S')} (source: {source}, batch: {current_batch})\n")
            log_file.write(f"========================================\n")

            for name, cmd in STEPS:
                print(f"-> 正在执行: {name} ... ", end="", flush=True)
                log_file.write(f"\n[{name}] START: {' '.join(cmd)}\n")
                log_file.flush()

                step_start = time.time()
                try:
                    res = subprocess.run(
                        cmd,
                        cwd=root_dir,
                        stdout=log_file,
                        stderr=log_file,
                        check=True
                    )
                    elapsed = time.time() - step_start
                    print(f"成功 ({elapsed:.1f}s)")
                    log_file.write(f"[{name}] SUCCESS. Elapsed: {elapsed:.1f}s\n")
                except subprocess.CalledProcessError as e:
                    elapsed = time.time() - step_start
                    print(f"失败! ({elapsed:.1f}s)")
                    log_file.write(f"[{name}] FAILED. Exit code: {e.returncode}. Elapsed: {elapsed:.1f}s\n")
                    print(f"❌ 流水线在步骤 [{name}] 中断。退出码: {e.returncode}。请检查 {pipeline_log} 日志。")
                    record_processed_batch(root_dir, current_batch, current_snap_time, status=f"failed_at_{name}")
                    sys.exit(e.returncode)
                except Exception as e:
                    elapsed = time.time() - step_start
                    print(f"异常! ({elapsed:.1f}s)")
                    log_file.write(f"[{name}] EXCEPTION: {str(e)}. Elapsed: {elapsed:.1f}s\n")
                    print(f"❌ 流水线在步骤 [{name}] 异常中断: {e}")
                    record_processed_batch(root_dir, current_batch, current_snap_time, status=f"exception_at_{name}")
                    sys.exit(1)

            total_elapsed = time.time() - t_start
            print(f"\n✓ 流水线全部执行成功！总耗时: {total_elapsed:.1f}s")
            log_file.write(f"========================================\n")
            log_file.write(f"Pipeline completed successfully. Total elapsed: {total_elapsed:.1f}s\n")

            # 4. 记录本次成功处理的批次
            record_processed_batch(root_dir, current_batch, current_snap_time, status="success")

    finally:
        # 释放排他文件锁
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:
            pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-SUM 串行流水线调度器")
    parser.add_argument("--force", action="store_true", help="强制执行流水线，忽略新鲜度门禁")
    parser.add_argument("--source", type=str, default="auto", help="触发源标识 (如 select-coin-hook, cron, manual)")
    args = parser.parse_args()

    run_pipeline(force=args.force, source=args.source)
