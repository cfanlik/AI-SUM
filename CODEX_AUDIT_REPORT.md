# AI-SUM 六引擎架构深度审计 — 提交 Codex 5.5 评估

> 审计时间: 2026-04-30 14:50 | 数据来源: VPS `/opt/AI-SUM/` 实机数据

---

## 一、定时任务运行状态

### Crontab 执行情况

| 引擎 | 时间点 | 日志行数 | 最新报告 | 状态 |
|------|--------|---------|---------|------|
| master-scan | 00:00/12:00 | 159 | radar_20260430_1409.md | ✅ 正常 |
| opus-scan | 00:10/12:10 | 104 | opus_20260430_1210.md | ✅ 正常 |
| unified-scan | 00:20/12:20 | 88 | unified_20260430_1220.md | ⚠ 未注入持久化 |
| whale-scan | 00:30/12:30 | 111 | whale_20260430_1230.md | ✅ 正常 |
| cost-basis-scan | 00:40/12:40 | 143 | cb_20260430_1240.md | ✅ 正常 |
| meta-verdict | 00:50/12:50 | 96 | meta_20260430_1250.md | ⚠ 见下文 |

### 已完成的定时轮次

```
04-29 00:xx  ✅ (首轮)
04-29 12:xx  ✅
04-30 00:xx  ✅
04-30 12:xx  ✅
共 4 轮自动执行，全部成功
```

---

## 二、select-sum.db 数据质量审计

### 各表数据量

| 表名 | 行数 | 时间点数 | 每轮记录数 | 问题 |
|------|------|---------|-----------|------|
| watchlist | 128 | — | — | ✅ DIAMOND:7 RED:19 YELLOW:102 |
| cost_basis_snapshots | 1,644 | 12 | ~149 | ✅ 时序最完整（从 04-28 开始） |
| opus_snapshots | 285 | 7 | ~40 | ✅ 仅保存非 NEUTRAL |
| whale_snapshots | 274 | 5 | ~55 | ✅ 仅保存非 CLEAN |
| unified_snapshots | **0** | 0 | 0 | ❌ **未注入持久化，meta-verdict 无法读取** |
| unified_results | 6,403 | — | — | ℹ unified-scan 自有表，但 meta 未对接 |
| meta_snapshots | 167 | 8 | ~20 | ⚠ 仅保存 ACC/DIST，不保存 NEUTRAL |
| token_lifecycle | 37 | — | — | ⚠ 31 ACCUMULATING + 6 CONTROLLED，无 DISTRIBUTING |

### 关键发现

1. **unified_snapshots = 0**：unified-scan 持久化注入未完成，导致 meta-verdict 积分明细中 `unified得分` 全部为 0
2. **cost_basis 覆盖不全**：DIAMOND 代币（CYS/GWEI/UAI/SQD 等）在 cost_basis 中显示价格 $0.0000，因为这些代币在 `bubblemap_holders` 中无 `gmgn_avg_price` 数据
3. **时序数据仅 2 天**：从 04-29 00:xx 到 04-30 12:xx，只有 4 个完整轮次。趋势分析需要至少 7 天数据

---

## 三、meta-verdict 报告空洞问题诊断

### 现状：报告只有静态表格

```
当前 meta-verdict 报告内容:
├── 积分权重说明（静态文本，每次重复）
├── 🎯 吸筹排行表格（25 行）
├── 📋 积分明细表格（20 行）
└── 结束
```

### 具体空洞表现

| 问题 | 表现 | 根因 |
|------|------|------|
| 无趋势分析 | TRADOOR 从 4.0→7.5 的积分跃升完全不可见 | meta_snapshots 有时序数据但报告不读取 |
| 无价格变化 | 大量代币显示 $0.0000 | 仅从 cost_basis 取价格，其他引擎无价格字段 |
| 无信号变化叙事 | "本轮新增 2 个 RED" 这类信息不存在 | 不做跨轮对比 |
| 无出货预警 | 💀出货永远 = 0 | DIST 信号只来自 cost-basis 的 DEATH_SPIRAL/LIQUIDITY_CRISIS，极少触发 |
| unified 贡献为零 | 所有代币 unified 得分 = 0 | unified_snapshots 表为空 |
| 阶段无变化 | 37 个代币全部停留在 ACCUMULATING/CONTROLLED | 无退出条件触发 DISTRIBUTING |

### TRADOOR 积分时序（实际数据）

```
04-29 10:35  score=4.0  [仅 master+CB]
04-29 10:53  score=7.5  [+opus+whale 注入后首次]
04-29 12:50  score=7.5  [稳定]
04-30 00:50  score=4.9  [CB 空白，whale 计入但 opus 变化]  ← 数据波动
04-30 12:50  score=7.5  [恢复]
```

> 问题：00:50 轮次中 cb="" 导致得分下降 1.6 分（CB 贡献丢失），这种数据不一致性在报告中完全不可见。

---

## 四、与前五个引擎的协同性分析

### 当前协同架构

```
master-scan ──→ watchlist ──────────────────────────────→ meta-verdict (读取)
opus-scan ────→ opus_snapshots ─────────────────────────→ meta-verdict (读取)
whale-scan ──→ whale_snapshots ────────────────────────→ meta-verdict (读取)
cost-basis ──→ cost_basis_snapshots ───────────────────→ meta-verdict (读取)
unified-scan ──→ unified_results (自有表) ──→ ❌ 断裂 ──→ meta-verdict (读不到)
```

### 协同性评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 数据链路 | 4/5 | unified 断裂，其余 4 引擎已通 |
| 时序利用 | 1/5 | 只读最新一条，不做跨轮趋势 |
| 价格补全 | 1/5 | 仅依赖 cost-basis 提供价格，大量代币 $0 |
| 信号去重 | 2/5 | master DIAMOND + unified DIAMOND 会双倍计分（+8），但实际是同一数据源 |
| 叙事输出 | 0/5 | 纯表格，无 delta/变化/预警叙事 |
| 预测价值 | 1/5 | 无回测、无 PnL 追踪、无时间窗口收益率 |

---

## 五、趋势分析框架：当前缺失 vs 目标状态

### 目标：从"快照汇总"升级为"趋势引擎"

| 能力 | 当前 | 目标 |
|------|------|------|
| 积分趋势 | ❌ 仅最新值 | ✅ 每代币 N 轮积分曲线，标注关键拐点 |
| 信号增减 | ❌ 无 | ✅ "本轮新增: TRIA (RED)，移除: H (降级)" |
| 价格联动 | ❌ $0 大量存在 | ✅ 从 gecko_market_data 或 CoinGecko API 补全价格 |
| 跨引擎一致性校验 | ❌ 无 | ✅ "master=DIAMOND 但 whale=CLEAN" → 标注矛盾 |
| PnL 回测 | ❌ 无 | ✅ 被标记时价格 vs 7d/14d/30d 后价格 → 准确率 |
| 跃迁告警 | ⚠ 仅 lifecycle 表记录 | ✅ 报告顶部醒目展示 "⚡ ACC→DIST 跃迁: X 个" |
| 引擎健康监控 | ❌ 无 | ✅ "unified 本轮贡献 0（断链）" 自动告警 |

---

## 六、12 项工程化改进方案

### P0：数据链路修复（1 天）

| # | 改进项 | 工作量 | 详情 |
|---|--------|--------|------|
| 1 | unified-scan 持久化注入 | 30 min | 修复 unified_snapshots=0 的断链问题。从 unified_results 表读取或注入 persist_helper.save_unified |
| 2 | 价格补全 | 1h | meta-verdict collector.py 增加从 `gecko_market_data` 或 `bubblemap_holders` 读取最新价格，填充 $0 代币 |
| 3 | master/unified 信号去重 | 30 min | 当 master 和 unified 输出相同 DIAMOND 信号时，只计一次（去重因子 0.5） |

### P1：趋势分析引擎（2 天）

| # | 改进项 | 工作量 | 详情 |
|---|--------|--------|------|
| 4 | meta-verdict 跨轮 diff | 3h | 读取上一轮 meta_snapshots，计算每代币 Δscore / 新增引擎 / 移除引擎 |
| 5 | 积分趋势摘要 | 2h | 报告顶部：「↑升 X 个 / ↓降 X 个 / 新进 X 个 / 退出 X 个」 |
| 6 | 信号变更叙事 | 2h | 「TRIA: 本轮新增 RED(A模式)，积分 +3.0 → 5.0，连续 2 轮上升」 |
| 7 | 引擎健康自检 | 1h | 检测每个引擎最新 scan_time，若 >2h 前 → 报告标红 |

### P2：预测与回测（3 天）

| # | 改进项 | 工作量 | 详情 |
|---|--------|--------|------|
| 8 | PnL 追踪表 | 3h | `token_pnl` 表：记录首次进入 ACC 时的价格，每轮更新当前价格，计算浮盈率 |
| 9 | 回测验证脚本 | 4h | 对已退出 watchlist 的代币，统计被标记后 7d/14d 价格变化，计算胜率 |
| 10 | 多引擎共识强度 | 2h | `engine_hits ≥ 3` 且 `score ≥ 5` → "强共识"标签，跟踪其历史准确率 |

### P3：报告质量升级（1 天）

| # | 改进项 | 工作量 | 详情 |
|---|--------|--------|------|
| 11 | 报告结构重构 | 3h | 头部：概览摘要+跃迁告警+健康自检 → 中部：排行+趋势 → 尾部：积分明细+引擎矛盾 |
| 12 | 生命周期跃迁可视化 | 2h | `ACCUMULATING(31) → CONTROLLED(6) → DISTRIBUTING(0)` 状态分布+跃迁历史 |

---

## 七、select-sum.db 趋势分析能力评估

### 结论：**有原始数据，无分析框架**

| 维度 | 数据 | 分析框架 | 缺口 |
|------|------|---------|------|
| 积分时序 | meta_snapshots 有 8 轮数据 | ❌ 不读取历史 | meta-verdict 只读最新一条 |
| 成本漂移 | cost_basis_snapshots 有 12 轮 ×149 代币 | ⚠ gravity_tracker 仅对比相邻 2 轮 | 无长期趋势线 |
| 吸筹加速/减速 | opus_snapshots 有 7 轮 ×40 代币 | ❌ 无 | 需计算 acc_confidence 斜率 |
| 集中度趋势 | whale_snapshots 有 5 轮 ×55 代币 | ❌ 无 | 需计算 top2_hold 变化率 |
| 持仓变化 | bubblemap_holders 4.4M 行 14K+ 快照 | ⚠ 各引擎独立计算 | 无统一时序视图 |
| 价格走势 | gecko_market_data 有历史 | ❌ meta 不读取 | 需接入 gecko 表做价格对比 |

### 量化评估

```
数据完整度:  ████████░░ 80%   (unified 断链 -10%, 价格空白 -10%)
分析深度:    ██░░░░░░░░ 20%   (仅静态快照汇总)
趋势能力:    █░░░░░░░░░ 10%   (有数据但不利用)
预测价值:    ░░░░░░░░░░  0%   (无回测/PnL)
报告质量:    ██░░░░░░░░ 20%   (纯表格，无叙事)
```

---

## 八、推荐实施路径

```
Week 1 Day 1-2:  P0 数据链路修复（unified注入 + 价格补全 + 去重）
Week 1 Day 3-4:  P1 趋势分析引擎（跨轮diff + 摘要 + 叙事 + 健康自检）
Week 1 Day 5:    P3 报告质量升级（结构重构 + 跃迁可视化）
Week 2:          P2 预测与回测（PnL追踪 + 回测脚本 + 共识强度）
```

### 预期效果

| 指标 | 当前 | P0+P1 后 | P0+P1+P2+P3 后 |
|------|------|---------|---------------|
| 引擎数据覆盖 | 4/5 (80%) | 5/5 (100%) | 5/5 (100%) |
| 价格覆盖率 | ~50% | ~95% | ~95% |
| 报告信息密度 | 2 个表格 | 5 个区块+趋势 | 7 个区块+PnL |
| 趋势分析能力 | 无 | 跨轮 diff | diff + 回测胜率 |
| 可决策性 | 低（需手动交叉阅读 5 份报告） | 中（单份报告含趋势） | 高（含 PnL 验证） |

---

## 附录：Codex 5.5 评估要点

1. **数据链路完整性**：unified_snapshots 断链需优先修复
2. **趋势分析框架**：meta_snapshots 已有 8 轮时序数据，但 `meta-verdict/run.py` 完全不读取历史，仅做当前快照汇总
3. **报告空洞根因**：无 diff、无叙事、无价格、无趋势 → 纯表格复读
4. **信号去重**：master DIAMOND + unified DIAMOND 双倍计分（实测 unified 当前为 0，但修复后会出现）
5. **回测空白**：系统运行 28 天（04-02~04-30），197 代币，14K+ 快照，但从未做过信号→价格的因果验证
