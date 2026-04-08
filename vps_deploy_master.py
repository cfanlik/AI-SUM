import os
import sys
import paramiko

sys.stdout.reconfigure(encoding='utf-8')

host = '118.193.33.162'
user = 'root'
pwd = 'Cfanvps@2026'

print("Connecting to VPS...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=pwd)

ssh.exec_command('mkdir -p /opt/AI-SUM/master-scan')
sftp = ssh.open_sftp()

local_dir = r"E:\AI-SUM\master-scan"
remote_dir = "/opt/AI-SUM/master-scan"

files = ["config.py", "db_loader.py", "engine.py", "pattern_detector.py",
         "report_generator.py", "time_series_aligner.py", "watchlist_tracker.py",
         "run.py", "__init__.py"]

for fn in files:
    local_p = os.path.join(local_dir, fn)
    if os.path.exists(local_p):
        print(f"  Upload {fn}")
        sftp.put(local_p, f"{remote_dir}/{fn}")

# 写入干净的 .env
env = """SRC_DB_PATH=/opt/select-coin/data/select.db
SUM_DB_PATH=/opt/AI-SUM/select-sum.db
DIAMOND_INST_THRESHOLD=90.0
DIAMOND_DEX_THRESHOLD=90.0
HIDDEN_WHALE_HOLD_THRESHOLD=2.0
"""
with sftp.open("/opt/AI-SUM/.env", "w") as f:
    f.write(env)

# 清除旧 watchlist 脏数据
print("Cleaning watchlist...")
ssh.exec_command("python3 -c \"import sqlite3; c=sqlite3.connect('/opt/AI-SUM/select-sum.db'); c.execute('DELETE FROM watchlist'); c.commit(); c.close()\"")

import time; time.sleep(1)

# 清除旧 diff 缓存（强制重算）
ssh.exec_command("python3 -c \"import sqlite3; c=sqlite3.connect('/opt/AI-SUM/select-sum.db'); c.execute('DELETE FROM snapshot_diff_cache'); c.commit(); c.close()\"")

import time; time.sleep(1)

print("Running scan on VPS...")
stdin, stdout, stderr = ssh.exec_command("cd /opt/AI-SUM && python3 master-scan/run.py")
out = stdout.read().decode('utf-8')
err = stderr.read().decode('utf-8')

# 只显示到报告保存位置
cut = out.split("--- 最新 Watchlist ---")[0] if "--- 最新 Watchlist ---" in out else out
print(cut)
if err:
    print("=== STDERR ===")
    print(err[-500:])

# 下载最新报告
try:
    remote_report_dir = "/opt/AI-SUM/report/v5"
    local_report_dir = r"E:\AI-SUM\report\v5"
    os.makedirs(local_report_dir, exist_ok=True)
    rfiles = sftp.listdir_attr(remote_report_dir)
    md_files = [f for f in rfiles if f.filename.startswith("radar_") and f.filename.endswith(".md")]
    md_files.sort(key=lambda x: x.st_mtime, reverse=True)
    if md_files:
        latest = md_files[0].filename
        sftp.get(f"{remote_report_dir}/{latest}", os.path.join(local_report_dir, latest))
        print(f"\n  报告已下载: E:\\AI-SUM\\report\\v5\\{latest}")
except Exception as e:
    print(f"Download error: {e}")

sftp.close()
ssh.close()
print("Done.")
