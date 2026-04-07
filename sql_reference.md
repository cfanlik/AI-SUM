# Accumulation Scan V3 — SQL 查询参考手册

> 便于 AI 和开发者快速查阅和复用的 SQL 模板集
> 更新: 2026-03-27

> ⚠️ **强制规定**: 每次功能修改后，必须同步更新本文件及 AI-SUM/ 目录下所有关联 md 文件，并在 `changelog.md` 中记录修改。

---

## 1. 核心聚合查询 — 代币级吸筹排名（优化版）

> ⚠️ **不要使用** `LOWER()` join — 会绕开索引，导致 22s+ 查询。
> 正确方式: 先 CTE 聚合，再按 `(chain, token_address)` 精确 join。

```sql
WITH token_agg AS (
    SELECT
        b.chain,
        b.token_address,
        COUNT(*) as total_holders,
        SUM(CASE WHEN b.is_cex=0 AND b.is_dex=0 AND b.is_contract=0
                 AND b.is_supernode=0 THEN 1 ELSE 0 END) as real_users,
        SUM(CASE WHEN b.is_cex=1 OR b.is_dex=1 OR b.is_contract=1
                 OR b.is_supernode=1 THEN 1 ELSE 0 END) as non_real,
        SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END) as acc_holders,
        ROUND(100.0 * SUM(CASE WHEN b.is_accumulating=1 THEN 1 ELSE 0 END) /
              NULLIF(SUM(CASE WHEN b.is_cex=0 AND b.is_dex=0 AND b.is_contract=0
                   AND b.is_supernode=0 THEN 1 ELSE 0 END), 0), 1) as acc_pct_real,
        ROUND(AVG(CASE WHEN b.is_accumulating=1 THEN b.acc_score
                  ELSE NULL END), 2) as avg_acc_score,
        ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.hold_percentage
                  ELSE 0 END), 2) as acc_hold_pct,
        ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.buy_amt_usd  ELSE 0 END), 2) as acc_buy,
        ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.sell_amt_usd ELSE 0 END), 2) as acc_sell,
        ROUND(SUM(CASE WHEN b.is_accumulating=1 THEN b.net_inflow   ELSE 0 END), 2) as acc_net,
        SUM(CASE WHEN b.is_accumulating=1 AND b.sell_amt_usd=0
                 AND b.buy_amt_usd>0 THEN 1 ELSE 0 END) as only_buy_cnt,
        MAX(b.snapshot_time) as latest_snapshot,
        COUNT(DISTINCT b.snapshot_time) as snap_count,
        ROUND(SUM(CASE WHEN b.is_cex=1 OR b.is_contract=1 THEN b.hold_percentage
                  ELSE 0 END), 2) as cex_contract_hold
    FROM bubblemap_holders b
    GROUP BY b.chain, b.token_address
    HAVING acc_holders >= 3
)
SELECT
    a.chain,
    a.token_address,
    COALESCE(t.name, '未知') as token_name,
    COALESCE(t.symbol, '?') as token_symbol,
    t.market_cap_usd,
    a.*
FROM token_agg a
LEFT JOIN token_names t
    ON a.chain = t.chain
   AND a.token_address = t.token_address
ORDER BY a.acc_pct_real DESC, a.avg_acc_score DESC;
```

---

## 2. 趋势查询 — 批量聚合（优化版）

> ⚠️ **不要逐代币查询** — 使用批量聚合 + Python 后处理。

```sql
-- 批量获取所有快照的吸筹地址数（Python 侧再按代币计算变异系数）
SELECT
    chain,
    token_address,
    snapshot_time,
    SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) as acc_count
FROM bubblemap_holders
GROUP BY chain, token_address, snapshot_time
ORDER BY chain, token_address, snapshot_time;

-- 单代币的快照趋势（调试用）
SELECT
    snapshot_time,
    SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END)  AS acc_count,
    COUNT(*)                                             AS total,
    ROUND(AVG(CASE WHEN is_accumulating=1 THEN acc_score ELSE NULL END), 2) AS avg_score
FROM bubblemap_holders
WHERE chain = :chain AND token_address = :target_address
GROUP BY snapshot_time
ORDER BY snapshot_time;
```

---

## 3. 信号密度查询 — 批量聚合

```sql
SELECT
    chain,
    token_address,
    ROUND(AVG(1 + LENGTH(acc_signals) - LENGTH(REPLACE(acc_signals, ',', ''))), 2)
        AS avg_signals
FROM bubblemap_holders
WHERE is_accumulating=1
  AND acc_signals IS NOT NULL
  AND acc_signals != ''
GROUP BY chain, token_address;
```

---

## 4. 最新快照结构 — 集中度分析

```sql
WITH latest_snapshot AS (
    SELECT chain, token_address, MAX(snapshot_time) AS latest_snapshot
    FROM bubblemap_holders
    GROUP BY chain, token_address
),
latest_rows AS (
    SELECT b.*
    FROM bubblemap_holders b
    JOIN latest_snapshot s
      ON b.chain = s.chain
     AND b.token_address = s.token_address
     AND b.snapshot_time = s.latest_snapshot
),
latest_stats AS (
    SELECT
        chain, token_address,
        COUNT(*) AS latest_total_holders,
        SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 AND is_supernode=0
                 THEN 1 ELSE 0 END) AS latest_real_users,
        SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS latest_acc_holders,
        ROUND(SUM(CASE WHEN is_accumulating=1 THEN hold_percentage ELSE 0 END), 2)
            AS latest_acc_hold,
        ROUND(AVG(CASE WHEN is_accumulating=1 THEN acc_score ELSE NULL END), 2)
            AS latest_avg_acc,
        ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 AND sell_amt_usd=0 AND buy_amt_usd>0
                               THEN 1 ELSE 0 END) /
              NULLIF(SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END), 0), 1)
            AS latest_only_buy_pct
    FROM latest_rows
    GROUP BY chain, token_address
),
ranked_acc AS (
    SELECT
        chain, token_address, hold_percentage,
        ROW_NUMBER() OVER (
            PARTITION BY chain, token_address
            ORDER BY hold_percentage DESC, wallet_address
        ) AS rn
    FROM latest_rows
    WHERE is_accumulating=1
),
acc_concentration AS (
    SELECT
        chain, token_address,
        ROUND(MAX(CASE WHEN rn=1 THEN hold_percentage END), 2) AS top1_acc_hold,
        ROUND(SUM(CASE WHEN rn<=3 THEN hold_percentage ELSE 0 END), 2) AS top3_acc_hold,
        ROUND(SUM(CASE WHEN rn<=5 THEN hold_percentage ELSE 0 END), 2) AS top5_acc_hold
    FROM ranked_acc
    GROUP BY chain, token_address
)
SELECT s.*, c.top1_acc_hold, c.top3_acc_hold, c.top5_acc_hold
FROM latest_stats s
LEFT JOIN acc_concentration c
  ON s.chain = c.chain AND s.token_address = c.token_address;
```

> `latest_acc_hold` 这列就是主引擎 `v3.3` 的 d3 输入；若要与 `codex-ai-sum.py` 做完全可比的 AB，对应评分应使用 `min(100, latest_acc_hold * 2.5)`，不要再退回旧的 `* 1.5` 标尺。
> 若要跑 `--assoc-gt10-bonus` 实验，可在 `latest_rows` 中额外取 `inbound_addresses` / `outbound_addresses`，并对 `max(inbound_addresses, outbound_addresses) > 10` 的地址提高 `d1/d4` 计权质量。

---

## 5. 最新快照 DEX 证据观测

```sql
WITH latest_snapshot AS (
    SELECT chain, token_address, MAX(snapshot_time) AS latest_snapshot
    FROM bubblemap_holders
    GROUP BY chain, token_address
),
latest_rows AS (
    SELECT b.*
    FROM bubblemap_holders b
    JOIN latest_snapshot s
      ON b.chain = s.chain
     AND b.token_address = s.token_address
     AND b.snapshot_time = s.latest_snapshot
)
SELECT
    chain,
    token_address,
    SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS latest_acc_h,
    ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 AND dex_ratio >= 0.5 THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END), 0), 1) AS direct_dex_acc_pct,
    ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 AND dex_ratio_hop2 >= 0.5 THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END), 0), 1) AS hop2_dex_acc_pct,
    ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 AND gmgn_verified >= 1 THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END), 0), 1) AS gmgn_pass_acc_pct,
    ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 AND gmgn_verified = 2 THEN 1 ELSE 0 END) /
          NULLIF(SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END), 0), 1) AS gmgn_double_acc_pct
FROM latest_rows
GROUP BY chain, token_address
ORDER BY direct_dex_acc_pct DESC, gmgn_pass_acc_pct DESC;
```

> 说明:
> - `direct_dex_acc_pct` = 直接来自 DEX pool swap 的吸筹地址占比
> - `hop2_dex_acc_pct` = 通过来源地址二跳推断的 DEX 占比
> - `gmgn_pass_acc_pct` = 已经通过 GMGN 地址页 swap 级验证的占比

---

## 6. 高分地址查询

```sql
SELECT
    b.chain,
    b.wallet_address,
    COALESCE(t.name, '未知') AS token_name,
    b.acc_score,
    b.acc_signals,
    b.hold_percentage,
    b.buy_amt_usd,
    b.sell_amt_usd,
    b.net_inflow,
    b.is_cex, b.is_dex, b.is_contract, b.is_supernode
FROM bubblemap_holders b
LEFT JOIN token_names t
    ON b.chain = t.chain
   AND b.token_address = t.token_address
WHERE b.is_accumulating = 1
ORDER BY b.acc_score DESC
LIMIT 20;
```

---

## 7. 数据完整性检查

```sql
-- buy/sell=0 的地址身份分布
SELECT
    SUM(CASE WHEN is_supernode=1 THEN 1 ELSE 0 END)  AS supernode,
    SUM(CASE WHEN is_contract=1  THEN 1 ELSE 0 END)  AS contract,
    SUM(CASE WHEN is_cex=1       THEN 1 ELSE 0 END)  AS cex,
    SUM(CASE WHEN is_dex=1       THEN 1 ELSE 0 END)  AS dex,
    SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0
             AND is_supernode=0 THEN 1 ELSE 0 END)   AS normal,
    COUNT(*)
FROM bubblemap_holders
WHERE buy_amt_usd = 0 AND sell_amt_usd = 0;

-- 吸筹地址买入覆盖率验证
SELECT
    COUNT(*) AS total_acc,
    SUM(CASE WHEN buy_amt_usd > 0 THEN 1 ELSE 0 END)  AS has_buy,
    SUM(CASE WHEN buy_amt_usd = 0 THEN 1 ELSE 0 END)  AS no_buy
FROM bubblemap_holders
WHERE is_accumulating = 1;
```

---

## 8. 链分布统计

```sql
SELECT
    chain,
    COUNT(DISTINCT token_address) AS tokens,
    COUNT(*)                      AS total_holders,
    SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) AS acc_holders
FROM bubblemap_holders
GROUP BY chain;
```

---

## 9. 快照深度统计

```sql
SELECT
    snap_count,
    COUNT(*) AS token_count
FROM (
    SELECT chain, token_address, COUNT(DISTINCT snapshot_time) AS snap_count
    FROM bubblemap_holders
    GROUP BY chain, token_address
) t
GROUP BY snap_count
ORDER BY snap_count;
```

---

## ⚠️ SQL 编写规范

1. **JOIN 必须使用 `(chain, token_address)` 双键** — 避免跨链串数
2. **禁止 `LOWER()` join** — 绕开索引，查询从 0.19s 退化到 22.6s
3. **优先 CTE** — 先聚合再 join，不要在大表上直接 join
4. **单代币查询必须带 `chain` 条件** — 即使当前数据无同地址跨链情况
