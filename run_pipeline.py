#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-SUM 串行流水线调度器
解决多个引擎并发启动重叠而引发的 OOM 共振，保证同一时间仅有一个任务在跑
"""
import subprocess
import time
import sys
import os

STEPS = [
    ("master-scan", ["python3", "master-scan/run.py"]),
    ("opus-scan", ["python3", "opus-scan/run.py", "--offline"]),
    ("unified-scan", ["python3", "unified-scan/run.py"]),
    ("bigcoin", ["python3", "bigcoin/run.py"]),
    ("cost-basis-scan", ["python3", "cost-basis-scan/run.py"]),
    ("meta-verdict", ["python3", "meta-verdict/run.py"]),
    ("history-report", ["python3", "meta-verdict/history_report.py"]),
    ("materialize-surge", ["python3", "meta-verdict/materialize_surge.py"]),
    ("anomaly-surge-report", ["python3", "meta-verdict/anomaly_surge_report.py"])
]

def run_pipeline():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(root_dir, "log")
    os.makedirs(log_dir, exist_ok=True)
    
    pipeline_log = os.path.join(log_dir, "pipeline.log")
    
    print(f"=== 开始执行 AI-SUM 串行流水线 ===\n工作目录: {root_dir}\n日志文件: {pipeline_log}")
    
    t_start = time.time()
    
    with open(pipeline_log, "a", encoding="utf-8") as log_file:
        log_file.write(f"\n\n========================================\n")
        log_file.write(f"Pipeline started at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"========================================\n")
        
        for name, cmd in STEPS:
            print(f"-> 正在执行: {name} ... ", end="", flush=True)
            log_file.write(f"\n[{name}] START: {' '.join(cmd)}\n")
            log_file.flush()
            
            step_start = time.time()
            try:
                # 显式指定 cwd，防止环境变量和路径混乱
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
                sys.exit(e.returncode)
            except Exception as e:
                elapsed = time.time() - step_start
                print(f"异常! ({elapsed:.1f}s)")
                log_file.write(f"[{name}] EXCEPTION: {str(e)}. Elapsed: {elapsed:.1f}s\n")
                print(f"❌ 流水线在步骤 [{name}] 异常中断: {e}")
                sys.exit(1)
                
        total_elapsed = time.time() - t_start
        print(f"\n✓ 流水线全部执行成功！总耗时: {total_elapsed:.1f}s")
        log_file.write(f"========================================\n")
        log_file.write(f"Pipeline completed successfully. Total elapsed: {total_elapsed:.1f}s\n")

if __name__ == "__main__":
    run_pipeline()
