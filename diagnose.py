import sqlite3

c = sqlite3.connect('/opt/AI-SUM/data/select-sum.db')
c.row_factory = sqlite3.Row

# 1. DIAMOND 代币在 opus 中的命中情况
print("=== DIAMOND vs opus ===")
rows = c.execute("""
    SELECT w.token_symbol, substr(w.token_address,1,12) as addr,
           o.acc_confidence, o.verdict
    FROM watchlist w
    LEFT JOIN opus_snapshots o ON lower(w.token_address)=lower(o.token_address)
    WHERE w.signal_level='DIAMOND' LIMIT 8
""").fetchall()
for r in rows:
    print(dict(r))

# 2. opus 中 COLLECT 的地址
print("\n=== COLLECT in opus ===")
rows2 = c.execute("SELECT token_symbol, token_address, acc_confidence FROM opus_snapshots WHERE token_symbol='COLLECT'").fetchall()
for r in rows2:
    print(dict(r))

# 3. COLLECT 在 watchlist 中的地址
print("\n=== COLLECT in watchlist ===")
rows3 = c.execute("SELECT token_symbol, token_address, signal_level FROM watchlist WHERE token_symbol='COLLECT'").fetchall()
for r in rows3:
    print(dict(r))
