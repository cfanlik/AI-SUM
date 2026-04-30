import sqlite3

c = sqlite3.connect("/opt/AI-SUM/select-sum.db")

# 1. 各表行数
tables = ["watchlist","cost_basis_snapshots","opus_snapshots","whale_snapshots",
          "meta_snapshots","token_lifecycle","unified_results","unified_snapshots"]
print("=== TABLE COUNTS ===")
for t in tables:
    try:
        r = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
        print(f"  {t}: {r[0]}")
    except:
        print(f"  {t}: NOT_EXISTS")

# 2. 时序覆盖
print("\n=== OPUS SCAN_TIMES ===")
for r in c.execute("SELECT scan_time, COUNT(*) FROM opus_snapshots GROUP BY scan_time ORDER BY scan_time"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== WHALE SCAN_TIMES ===")
for r in c.execute("SELECT scan_time, COUNT(*) FROM whale_snapshots GROUP BY scan_time ORDER BY scan_time"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== CB SCAN_TIMES ===")
for r in c.execute("SELECT scan_time, COUNT(*) FROM cost_basis_snapshots GROUP BY scan_time ORDER BY scan_time"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== META SCAN_TIMES ===")
for r in c.execute("SELECT scan_time, COUNT(*) FROM meta_snapshots GROUP BY scan_time ORDER BY scan_time"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== LIFECYCLE STAGES ===")
for r in c.execute("SELECT current_stage, COUNT(*) FROM token_lifecycle GROUP BY current_stage"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== WATCHLIST ===")
for r in c.execute("SELECT signal_level, COUNT(*) FROM watchlist GROUP BY signal_level"):
    print(f"  {r[0]}: {r[1]}")

# 3. unified_snapshots 是否有数据
try:
    r = c.execute("SELECT COUNT(*) FROM unified_snapshots").fetchone()
    print(f"\n=== UNIFIED_SNAPSHOTS: {r[0]} ===")
except:
    print("\n=== UNIFIED_SNAPSHOTS: NOT_EXISTS ===")

# 4. 趋势：同一代币在不同 scan_time 的积分变化
print("\n=== TRADOOR META TREND ===")
for r in c.execute("SELECT scan_time, meta_score, meta_verdict, master_signal, opus_verdict, whale_level, cb_verdict FROM meta_snapshots WHERE token_symbol='TRADOOR' ORDER BY scan_time"):
    print(f"  {r[0]} | score={r[1]} | verdict={r[2]} | m={r[3]} o={r[4]} w={r[5]} cb={r[6]}")

print("\n=== COLLECT META TREND ===")
for r in c.execute("SELECT scan_time, meta_score, master_signal, opus_verdict, whale_level, cb_verdict FROM meta_snapshots WHERE token_symbol='COLLECT' ORDER BY scan_time"):
    print(f"  {r[0]} | score={r[1]} | m={r[2]} o={r[3]} w={r[4]} cb={r[5]}")
