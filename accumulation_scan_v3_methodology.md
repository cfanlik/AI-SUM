# Accumulation Analysis V3 — 全局吸筹扫描方法论

> 版本: v3.3 | 更新: 2026-03-16 | 主数据源: BubbleMap

> ⚠️ **强制规定**: 每次功能修改后，必须同步更新本文件及 AI-SUM/ 目录下所有关联 md 文件，并在 `changelog.md` 中记录修改。

---

## 1. 系统概述

### 1.1 目标

从 BubbleMap 采集的 TOP 300 持有者数据中，自动识别"正在被大户低位吸筹"的代币，并按 5 级(S/A/B/C/D) 输出排名。

### 1.2 数据源定位

| 数据源 | 持有者上限 | 买卖数据 | 地址分类 | 关系图谱 | 定位 |
|--------|-----------|----------|----------|----------|------|
| **BubbleMap** | **300** | IN/OUT 转账 | CEX/DEX/合约/超级节点 | 有 | **主数据源** |
| Binance | 100 | API 直给(USD) | 无 | 无 | 辅助验证 |
| GMGN | 100 | API 直给(USD) | 少量标签 | 无 | 辅助验证 |

### 1.3 数据库

- 路径: `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db` (SQLite)
  - ⚠️ 旧路径 `seletc-coin/seletc.db` 已失效
- 主表: `bubblemap_holders` — 每条记录 = 一个地址在某代币某快照时刻的持仓和评分
- 辅表: `token_names` — 代币名称/符号/市值缓存

### 1.4 当前数据画像 (2026-03-16)

| 指标 | 03-13 | **03-16** |
|------|-------|-----------|
| 数据库大小 | ~50MB | **368 MB** |
| 去重代币 | ~310 | **469** |
| 链分布 | bsc 为主 | eth=264, bsc=187, base=18 |
| 最小快照次数 | 1 (164个仅1次) | **6** (全部 ≥ 6 次) |
| 最大快照次数 | ~3 | **10** |

---

## 2. 核心数据模型

### 2.1 BubbleMap 记录字段

```
bubblemap_holders:
  chain              TEXT     -- 链名 (bsc/eth/sol/base)
  token_address      TEXT     -- 代币合约地址
  snapshot_time      TEXT     -- 快照时间
  rank               INTEGER  -- 持仓排名 (1-300)
  wallet_address     TEXT     -- 持有者地址
  hold_amount        REAL     -- 持仓数量
  hold_percentage    REAL     -- 持仓占比 (%)
  buy_amt_usd        REAL     -- 买入总额 (token 数量×价格，非精确 USD)
  sell_amt_usd       REAL     -- 卖出总额
  net_inflow         REAL     -- 净流入
  acc_score          REAL     -- 吸筹评分 (0-100)
  acc_signals        TEXT     -- 吸筹信号列表 (逗号分隔)
  is_accumulating    INTEGER  -- 是否判定为吸筹 (0/1)
  is_cex             INTEGER  -- CEX 地址标记
  is_dex             INTEGER  -- DEX 地址标记
  is_contract        INTEGER  -- 合约地址标记
  is_supernode       INTEGER  -- 超级节点标记 (做市商/高频)
```

### 2.2 数据完整性真相

**buy_amt_usd=0 且 sell_amt_usd=0 的记录不是数据缺失**:

| 地址类型 | 占比 | 说明 |
|----------|------|------|
| 超级节点 | 84.0% | 做市商/高频，无法追踪交易对 |
| 合约 | 36.0% | 智能合约，非自然人 |
| CEX | 14.8% | 交易所冷/热钱包 |
| DEX | 7.3% | 流动性池 |
| 普通地址 | **0.5%** | 极少数无交易记录 |

**关键结论**:
- 吸筹地址(is_accumulating=1) 买入数据覆盖率 = **100%**
- 真实用户地址买入数据覆盖率 = **99.8%**
- 真实用户定义: `is_cex=0 AND is_dex=0 AND is_contract=0 AND is_supernode=0`

---

## 3. 评分引擎架构

### 3.1 两层评分

```
第 1 层(地址级): BubbleMap 评分引擎 → 每个地址的 acc_score + is_accumulating
                (已内置在数据采集阶段，7 维度评分，详见 bubblemap_analyzer.py)

第 2 层(代币级): 全局扫描引擎 → 聚合所有地址 → 代币综合评分 → S/A/B/C/D
                (本文档描述的方法论)
```

### 3.2 第 2 层 — 代币级 8 维综合评分 (v4.0)

```python
composite = (d1 * 0.20 +   # 吸筹占比 (基于真实用户，聚类感知)
             d2 * 0.15 +   # 吸筹均分
             d3 * 0.14 +   # 持仓控制度 (最新快照口径)
             d4 * 0.12 +   # 卖出抑制 (聚类感知)
             d5 * 0.06 +   # 趋势稳定性 (v4: 时间衰减+方向一致性)
             d6 * 0.13 +   # 信号质量 (加权)
             d7 * 0.10 +   # 集中度 (越分散越高)
             d8 * 0.10)    # 实时活跃度 (v4: Δhold+新地址涌入)
```

#### 维度 1: 吸筹占比 (20%)

```python
# 基数 = 真实用户数 (排除 CEX/DEX/合约/超级节点)
acc_pct_real = acc_holders / real_users * 100
d1 = min(100, acc_pct_real * 1.25)   # 80% 占比 = 满分

# v4: 当 cluster_ratio ≥ 10% 时，使用去重后的 eff_acc_h 替代 acc_holders
# (entity_id 优先 → 特征聚类回退，组内 sqrt(n) 加权)
```

**SQL**:
```sql
-- 真实用户数
SUM(CASE WHEN is_cex=0 AND is_dex=0 AND is_contract=0 AND is_supernode=0
    THEN 1 ELSE 0 END) as real_users

-- 吸筹占比 (基于真实用户)
ROUND(100.0 * SUM(CASE WHEN is_accumulating=1 THEN 1 ELSE 0 END) /
      NULLIF(real_users, 0), 1) as acc_pct_real
```

#### 维度 2: 吸筹均分 (15%)

```python
# 55 分是吸筹门槛，映射到 0-100
d2 = max(0, min(100, (avg_acc_score - 55) / 45 * 100))
```

#### 维度 3: 持仓控制度 (14%) — v3.3 改为最新快照口径

```python
# v3.3: 使用最新快照的 latest_acc_hold，而非历史累计
# 40% 供应被吸筹地址控制 = 满分
d3 = min(100, latest_acc_hold * 2.5)
```

> ℹ️ v3.2 及之前使用历史累计 `acc_hold`（跨快照累加），导致 63.3% 代币 d3 打满 100。
> v3.3 改为最新快照口径 + 满分线从 66.7% 下调到 40%，解决了饱和问题。

#### 维度 4: 卖出抑制 (15%) — v3 新增

```python
# 只买不卖 (sell=0 且 buy>0) 的吸筹地址占比
only_buy_pct = only_buy_count / acc_holders * 100
d4 = min(100, only_buy_pct * 1.5)  # 66.7% 只买不卖 = 满分
```

**SQL**:
```sql
SUM(CASE WHEN is_accumulating=1 AND sell_amt_usd=0 AND buy_amt_usd>0
    THEN 1 ELSE 0 END) as only_buy_cnt
```

#### 维度 5: 趋势稳定性 (12%)

```python
# 需要 >= 2 次快照
# 稳定性 = 1 - (标准差 / 均值)，即变异系数的补数
stability = max(0, 1 - std_dev / mean)

d5 = stability * 80                    # 基础分
if trend_direction > 0: d5 += 20       # 增持加分
elif trend_direction == 0: d5 += 10    # 不变加分
d5 *= (1 + (snap_count - 2) * 0.05)   # 快照次数加成
d5 = min(100, d5)

# 仅 1 次快照: d5 = 30 (中性分)
```

> ⚠️ **已知问题 (2026-03-16)**: 当前所有代币均 ≥ 6 次快照后，d5 几乎全部打满 100 分，已丧失区分能力。详见 §6.1。

#### 维度 6: 信号密度 (10%)

```python
# 吸筹地址平均触发的信号数量
avg_signals = total_signal_count / acc_holders
d6 = min(100, avg_signals / 6.0 * 100)  # 6 个信号 = 满分
```

### 3.3 五级评价

| 等级 | 综合分 | 含义 |
|------|--------|------|
| **S** | ≥ 75 | 极强吸筹 |
| **A** | ≥ 60 | 强吸筹 |
| **B** | ≥ 45 | 中等吸筹 |
| **C** | ≥ 30 | 弱吸筹 |
| **D** | < 30 | 微弱信号 |

前置过滤: `acc_holders >= 3` (至少 3 个吸筹地址，排除噪声)

> ⚠️ **已知问题 (2026-03-16)**: S 级从 16 → 142（暴增 7.9 倍），门槛 ≥ 75 区分度已不足。考虑上调至 80 或引入 S+/S 分级。

---

## 4. 执行流程

```
输入: bubblemap_holders 表全量数据
 │
 ├─ Step 0: 可选 CMC 市值回填 (`--backfill-mcap`)
 │   └─ 产出: token_names.market_cap_usd
 │
 ├─ Step 1: 代币级聚合 (CTE + GROUP BY chain, token_address)
 │   └─ 产出: real_users, acc_holders, acc_pct_real, avg_acc_score,
 │           acc_hold_pct, acc_buy/sell/net, only_buy_cnt, snap_count
 │
 ├─ Step 2: 趋势计算 (批量聚合，Python 后处理)
 │   └─ 产出: stability, trend_direction, snaps
 │
 ├─ Step 3: 信号密度统计 (批量聚合)
 │   └─ 产出: avg_signals_per_holder
 │
 ├─ Step 4: 最新快照结构 (CTE + 窗口函数)
 │   └─ 产出: latest_acc_h, latest_only_buy_pct, top1/3/5_acc_hold
 │
 ├─ Step 5: 6 维综合评分 + 复核标签
 │   └─ 产出: d1-d6, composite, level(S/A/B/C/D),
 │           review_priority, structure_tags
 │
 └─ Step 6: 排序输出 + 自动导出 (CSV/JSON)
     └─ 产出: 分级代币清单 + 各维度评分明细
```

### 4.1 已落地工程优化

- **SQL join 优化 (2026-03-13)**:
  - 原 `LEFT JOIN token_names t ON LOWER(b.token_address) = LOWER(t.token_address)` 绕开索引
  - 改为"先 CTE 聚合，再按 `chain + token_address` 精确 join"
  - 实测: 主查询 `22.6s -> 0.19s`，端到端 `24.2s -> 0.8-0.9s`
- **批量聚合 (2026-03-13)**:
  - 趋势/信号统计从逐代币 N+1 查询改为批量聚合 + Python 后处理
- **主键规范 (2026-03-13)**:
  - 代币级分析必须使用 `(chain, token_address)` 双主键，不能只用 `token_address`
- **DB 路径更正 (2026-03-16)**:
  - 从 `seletc-coin/seletc.db` 更正为 `select-coin/select.db`
- **工作流修正 (2026-03-27)**:
  - CMC 市值回填改为显式参数 `--backfill-mcap`
  - 默认本地扫描不再先执行远程串行回填
  - 本地评分/导出路径恢复到秒级完成
- **代理固定 (2026-03-27)**:
  - AI-SUM 侧 CMC 请求优先固定走 `socks5://127.0.0.1:18000`
  - 不再直接跟随 `select-coin` 当前代理返回值

---

## 5. 辅助指标 (非评分维度，但辅助决策)

| 指标 | 计算方式 | 用途 |
|------|----------|------|
| `sell_buy_ratio` | acc_sell / acc_buy × 100% | 吸筹地址的卖出/买入比 |
| `cex_contract_hold` | CEX+合约地址的持仓合计 | 判断流通盘大小 |
| `non_real_count` | CEX+DEX+合约+超级节点数 | 代币的机构参与度 |
| `market_cap_usd` | token_names 表 | 价格位置参考 |

### 5.1 最新快照结构指标（人工复核高优先）

> 历史聚合评分用于排序，最新快照结构用于判断"当前吸筹是否健康"，两者不要混用。

| 指标 | 计算口径 | 判读作用 |
|------|----------|----------|
| `latest_only_buy_pct` | 最新快照中 `sell=0 且 buy>0` 的吸筹地址占比 | 判断当前卖压是否低 |
| `latest_acc_holders` | 最新快照吸筹地址数 | 判断当前参与广度 |
| `top1_acc_hold` | 最新快照吸筹地址中第一大地址持仓占比 | 判断是否由单一大户主导 |
| `top3_acc_hold` | 最新快照吸筹地址中前 3 大地址持仓占比 | 判断头部集中度 |
| `top5_acc_hold` | 最新快照吸筹地址中前 5 大地址持仓占比 | 判断结构是否健康分散 |
| `acc_top100_pct` | 最新快照中吸筹地址属于 Top100 持有者的占比 | 判断吸筹是否来自头部持仓者 |
| `acc_rest_pct` | 最新快照中吸筹地址属于 101-300 持有者的占比 | 判断吸筹是否来自中尾部持仓者 |
| `direct_dex_acc_pct` | 最新快照中 `dex_ratio>=0.5` 的吸筹地址占比 | 判断吸筹是否直接来自 DEX pool swap |
| `hop2_dex_acc_pct` | 最新快照中 `dex_ratio_hop2>=0.5` 的吸筹地址占比 | 判断吸筹是否经中转后二跳进入 |
| `gmgn_pass_acc_pct` | 最新快照中 `gmgn_verified>=1` 的吸筹地址占比 | 判断二跳来源是否已被 GMGN swap 级验证 |
| `gmgn_double_acc_pct` | 最新快照中 `gmgn_verified=2` 的吸筹地址占比 | 判断二跳来源是否达到 GMGN 双确认 |

### 5.2 人工复核判读框架

- 高综合分 + 低卖压 + 低 `top5_acc_hold` + 多次快照 = 高质量分散吸筹。
- 高综合分 + 高集中度 = 可能是大户控盘或结构脆弱，不应仅因总分高就高看。
- 单次快照高分 = 候选，不宜过度解读，应等待第二次及以上快照确认。
- 历史聚合 `acc_hold` 很高，但最新快照 `latest_acc_hold` 并不高时，通常说明历史累计抬高了指标，应优先看最新结构而不是累计值。

### 5.3 复核优先级自动分类

脚本内置 `classify_review()` 函数，根据结构指标自动产出三类标签：

| 优先级 | 触发条件 | 含义 |
|--------|----------|------|
| **优先复核** | S/A级 + 分散/良好结构 + 卖/买≤25% + 快照≥3 | 最高质量候选 |
| **谨慎复核** | 含"单一大户主导"/"高集中度"/"卖压偏高"标签 | 需人工确认风险 |
| **正常复核** | 其余情况 | 常规关注 |
| **待确认** | 仅 1 次快照 | 等待更多数据 |

---

## 6. 已知局限与优化方向

| # | 局限 | 影响 | 优化方向 | 状态 |
|---|------|------|----------|------|
| 1 | 无价格历史 | 无法判断"低位" | 接入 CMC/CoinGecko 价格 API | 待定 |
| 2 | ~~大量代币仅 1 次快照~~ | ~~趋势维度退化为中性分~~ | ~~提高快照频次~~ | ✅ 已解决 |
| 3 | buy/sell 是 token 级非 USD 级 | 跨代币金额不可比 | 需 token_price 归一化 | 待定 |
| 4 | 集中度分析仍未进入正式评分 | 高分代币可能只是少数大户主导 | 将 `top1/top5`、Gini、HHI 纳入评分 | **高优** |
| 5 | 无跨数据源交叉验证 | 单一来源可能有偏 | 补充 Binance/GMGN 数据 | 待定 |
| 6 | 无时间衰减 | 旧快照与新快照等权 | 近期快照权重更高 | 待定 |
| 7 | 元数据缺口明显 (`market_cap_usd` 当前库仍大面积为空) | 无法直接判断市值位置 | 按需运行 `--backfill-mcap` 或单独做离线回填 | **高优** |
| 8 | ~~历史聚合指标与最新快照结构容易被混用~~ | ~~容易误判~~ | ~~报告层默认同时展示两类指标~~ | ✅ 已解决 |
| 9 | **趋势维度(d5)膨胀** | d5 几乎全部 100 分，丧失区分能力 | 提高满分门槛或引入时间衰减 | **新发现·高优** |
| 10 | **S 级数量过多 (142/469)** | 30% 代币为 S 级，区分度不足 | 上调 S 级门槛至 80+ 或引入 S+ 分级 | **新发现·高优** |

### 6.1 当前优化优先级（基于 2026-03-16 实测）

1. **修复 d5 趋势膨胀** — 所有代币 ≥ 6 快照后 d5≈100，需：
   - 方案 A: 提高满分门槛（如需 ≥ 10 快照 + 明确增持方向）
   - 方案 B: 引入时间衰减，近 3 次快照权重更高
   - 方案 C: 降低 d5 权重（12% → 6%），将差额给集中度
2. **集中度纳入正式评分** — 作为第 7 维度，赋权 8-12%
3. **调整 S 级门槛** — 从 75 上调至 80，或引入 S+（≥82）
4. 补齐 `token_names.market_cap_usd` 等元数据
5. 做 `buy/sell` 的价格归一化与时间衰减
6. 最后再考虑跨数据源交叉验证

---

## 7. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v4.2.2 | 2026-03-27 | CMC 回填代理优先固定为 `socks5://127.0.0.1:18000`，并在回填开始时输出实际代理 |
| v4.2.1 | 2026-03-27 | 修正执行工作流：CMC 市值回填改为显式参数 `--backfill-mcap`，默认本地扫描不再被远程回填阻塞 |
| v4.2 | 2026-03-27 | 暴露 BubbleMap v6 DEX 证据观测字段（direct/hop2/GMGN），并同步到导出、报告、Codex 审计脚本 |
| v4.1 | 2026-03-19 | CMC市值回填、token_scores 评分持久化、BubbleMap 时间维度入库 |
| v4.0 | 2026-03-19 | 8维评分(+d8实时活跃度)、d5时间衰减重写、cluster_ratio 合并+entity_id 去重 |
| v1 | 2026-03-11 | 初版：2 维排序(占比+均分)、3 级星级评价 |
| v2 | 2026-03-13 | 5 维评分(+持仓控制+趋势+信号密度)、5 级(S/A/B/C/D) |
| v3 | 2026-03-13 | 修正 buy/sell=0 认知、占比基于真实用户、新增卖出抑制维度(6维) |
| v3.1 | 2026-03-16 | 更新数据画像(469代币/368MB)、发现d5膨胀和S级泛滥问题、DB路径更正 |
| v3.2 | 2026-03-16 | 7维评分(+d7集中度)、d5平方压缩、d6信号加权、权重重分配 |
| v3.3 | 2026-03-16 | d3改为最新快照口径(满分线40%)、报告改为综合分TOP20、S=19/A=238 |

> 工程补记: 在不改变评分逻辑的前提下，已完成精确 join + 批量聚合优化；本地扫描路径端到端耗时从约 `24s` 降到 `<1s`。如显式开启 `--backfill-mcap`，则额外耗时主要由远程 API 串行回填决定。

---

## 8. 文档同步规则

> ⚠️ **强制规定**: 每次功能修改后，必须同步更新以下文件，并在 `changelog.md` 中记录修改:
>
> | 文件 | 更新内容 |
> |------|----------|
> | `SESSION_MEMORY.md` | 环境信息、扫描结果、复核名单 |
> | `accumulation_scan_v3_methodology.md` | 评分逻辑、数据画像、局限与优化方向 |
> | `sql_reference.md` | SQL 查询模板 |
> | `changelog.md` | 修改记录 |
> | `accumulation_scan_v3.py` | 代码配置 (DB_PATH 等) |
> | `report/report_YYYYMMDD.md` | 每次扫描的分析报告 |
