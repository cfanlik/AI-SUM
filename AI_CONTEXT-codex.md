# AI_CONTEXT Codex Detailed Review Pack

> 生成时间: 2026-03-27  
> 目的: 作为“给其他 AI 的接力评审包”，快速理解当前 `AI-SUM` 主引擎、Codex 审计脚本与上游 BubbleMap v6 DEX 证据链。  
> 范围: `AI_CONTEXT.md` + `accumulation_scan_v3.py` + `codex-ai-sum.py` + `select-coin/AI_CONTEXT.md`

---

## 1. Executive Summary

当前 `AI-SUM` 主引擎已经是 **v4.0 八维评分**：

- `d1` 吸筹占比
- `d2` 吸筹均分
- `d3` 最新快照持仓控制度
- `d4` 卖出抑制
- `d5` 趋势稳定性
- `d6` 信号质量
- `d7` 集中度
- `d8` 实时活跃度

当前 Codex 审计脚本的职责不是替代主引擎，而是做两类旁路校验：

1. 重新计算 `d1/d3/d4`，验证聚类去重是否影响主评分。  
2. 暴露上游 BubbleMap v6 的 DEX 证据链，帮助解释“吸筹来自哪里”。  

目前主引擎已经正式合并：

- `cluster_ratio`
- `entity_id` 优先去重
- `eff_acc_h / eff_only_buy`
- `d8` 实时活跃度

本轮新增的重点不是再改主分，而是让以下上游证据在 AI-SUM 里可见：

- `direct_dex_acc_pct`
- `hop2_dex_acc_pct`
- `gmgn_pass_acc_pct`
- `gmgn_double_acc_pct`

---

## 2. Source of Truth

### 2.1 核心文档/脚本

- `AI_CONTEXT.md`：项目总索引，当前应视为主口径
- `accumulation_scan_v3.py`：主评分脚本
- `codex-ai-sum.py`：Codex 并行审计脚本
- `accumulation_scan_v3_methodology.md`：方法论文档
- `changelog.md`：变更日志

### 2.2 上游数据源口径

- `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\AI_CONTEXT.md`
- `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\AI_CONTEXT\scoring_engines.md`
- `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\bubblemap-list\scraper.py`
- `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\onchain\bubblemap_analyzer.py`

### 2.3 数据库

- SQLite: `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db`

---

## 3. Current Architecture

### 3.1 上游: select-coin / BubbleMap v6

select-coin 负责地址级吸筹识别，BubbleMap v6 的关键新增是 **DEX 置信度链路**：

1. **直接 DEX 证据**  
   通过 DexScreener pool 地址集合判断 `from_address in pool_set`，得到 `swap_in_value` 与 `dex_ratio`。

2. **二跳 DEX 证据**  
   对 `dex_ratio` 很低但收到目标代币的地址，沿 `_inbound_sources` 追溯来源地址，计算 `dex_ratio_hop2`。

3. **GMGN swap 级验证**  
   对前 2 个非 Pool 来源地址进入 GMGN 地址页，精抓最近 50 条 swap，写回 `gmgn_verified = 0/1/2`。

4. **BubbleMap DEX 置信度打分**  
   在上游地址级评分中，`dex_ratio` / `dex_ratio_hop2` / `gmgn_verified` 共同决定 `dex_confidence`。

### 3.2 下游: AI-SUM / 代币级聚合

AI-SUM 不直接重算地址级 `acc_score`，而是：

- 复用 `bubblemap_holders` 中的地址级吸筹结果
- 在代币级聚合 `d1-d8`
- 输出 CSV / JSON / Markdown
- 将结果持久化进 `token_scores`

### 3.3 主引擎当前显式暴露的上游 DEX 观测

`accumulation_scan_v3.py` 当前会在最新快照层面聚合：

- `direct_dex_acc_pct`
- `hop2_dex_acc_pct`
- `gmgn_pass_acc_pct`
- `gmgn_double_acc_pct`

这些字段目前定位为 **解释性观测字段**，不直接参与主综合分，避免与上游 `acc_score` 重复计权。

---

## 4. What Main Script Does Now

`accumulation_scan_v3.py` 当前做的关键事情：

1. 当 `cluster_ratio >= 10%` 时，对 `d1/d4` 使用去重后的 `eff_acc_h / eff_only_buy`。  
2. `d3` 固定使用最新快照口径：`latest_acc_hold * 2.5`。  
3. `d8` 使用跨快照的 `Δhold + new_acc_cnt`。  
4. 把 `cluster_ratio + DEX证据字段` 一起写入导出结果。  
5. 把关键评分和结构信息写入 `token_scores`。  
6. 默认跳过 CMC 市值回填，只有显式传 `--backfill-mcap` 才执行远程补数。  
7. 如执行 CMC 回填，AI-SUM 侧优先固定走 `socks5://127.0.0.1:18000`。  

当前主引擎对 DEX 证据的态度：

- **可见**
- **可导出**
- **可打标签**
- **暂不直接改主分**

---

## 5. What Codex Script Does Now

`codex-ai-sum.py` 当前口径已升级到与主引擎 `v4.0` 对齐：

1. 使用主引擎同口径权重：`0.20 / 0.15 / 0.14 / 0.12 / 0.06 / 0.13 / 0.10 / 0.10`。  
2. 保持 `d2/d5/d6/d7/d8` 不变，仅重算 `d1/d3/d4`。  
3. 聚类去重时优先使用 `entity_id`，无 `entity_id` 时回退到特征聚类。  
4. 附带输出 `direct_dex_acc_pct / hop2_dex_acc_pct / gmgn_pass_acc_pct`，用于解释样本差异。  
5. 支持实验模式 `--assoc-gt10-bonus`。  

因此 Codex 脚本现在回答的是：

- 去重后这个币还高分吗？
- 它的吸筹更像直接 DEX、二跳中转，还是已经被 GMGN 验证？

---

## 6. Critical Review Focus

当前最值得评审的点：

### P0 - 聚类放大仍然是主风险

- 风险位置: `d1/d4`
- 关键字段: `cluster_ratio`, `eff_acc_h`, `eff_only_buy`, `entity_id`
- 判断目标: 主榜是否仍被少数同构簇放大

### P1 - DEX 证据是否只停留在说明层

- 当前状态: `direct_dex_acc_pct / hop2_dex_acc_pct / gmgn_pass_acc_pct` 已进入导出和审计
- 仍需验证: 这些字段是否足够稳定，值得未来进入复核优先级或轻度加权

### P1 - 快照深度不足时的解释风险

- 当前 `d5/d8` 依赖跨快照
- 若数据库只保留很浅的快照深度，则 DEX 证据和趋势证据都容易被过度解读

### P1 - 文档一致性风险

- `AI_CONTEXT.md`
- `AI_CONTEXT-codex.md`
- `accumulation_scan_v3_methodology.md`
- `changelog.md`

上述文件必须同步，否则其他 AI 很容易按旧口径工作。

---

## 7. Reproducibility

```powershell
# 1) 主引擎
python -X utf8 accumulation_scan_v3.py 4

# 1b) 如需补 market cap，再显式开启远程回填
python -X utf8 accumulation_scan_v3.py 4 --backfill-mcap

# 2) Codex 审计
python -X utf8 codex-ai-sum.py

# 3) Codex 实验模式
python -X utf8 codex-ai-sum.py --assoc-gt10-bonus
```

期望产物：

- `exports/accumulation_scan_v3_*.json`
- `exports/codex_ai_sum_*.json`
- `report/YYYYMMDD_HHMM/report.md`
- `report/YYYYMMDD_HHMM/codex-ai-sum.md`

---

## 8. Review Checklist For Other AI

请按以下顺序评审：

1. **口径一致性**：主脚本 / 审计脚本 / 文档 / 报告是否一致。  
2. **去重有效性**：`cluster_ratio >= 10%` 的币种是否被合理修正。  
3. **DEX证据解释性**：高分币的 `direct_dex_acc_pct / hop2_dex_acc_pct / gmgn_pass_acc_pct` 是否符合叙事。  
4. **鲁棒性**：修改 `d1/d3/d4` 后，Top20 是否仍稳定。  
5. **误杀风险**：`entity_id` 去重是否误伤真实机构分仓。  
6. **可落地性**：计算复杂度、SQL 成本、导出兼容性、旧表迁移风险。  

---

## 9. Decision Options

### Option A: 继续观测层

- 主分不变
- 仅暴露 `cluster_ratio + DEX证据`
- 优点: 风险最低
- 缺点: 解释增强但决策不变

### Option B: 复核优先级增强

- 主分仍不变
- 在 `review_priority / structure_tags` 中更显式利用 DEX 证据
- 优点: 低风险、提升人工复核效率
- 缺点: 仍不改变最终排序

### Option C: 轻度规则化

- 保持上游地址级 `dex_confidence`
- 在代币级仅对高 `gmgn_pass_acc_pct` 或高 `direct_dex_acc_pct` 样本增加解释性加分或标签
- 优点: 更贴近实际资金来源
- 缺点: 有重复计权风险，必须先做多轮回测

---

## 10. Suggested Acceptance Criteria

1. `codex-ai-sum.py` 的 `d3` 必须与主引擎保持严格同口径。  
2. `codex-ai-sum.py` 必须保留主引擎 `d8`，不能回退到旧 7 维权重。  
3. `entity_id` 优先去重必须在主引擎与审计脚本中一致。  
4. 报告中必须同时展示聚类指标和 DEX 证据指标。  
5. 文档、脚本、changelog 三方版本号和特性描述一致。  

---

## 11. Handoff Prompt Template

```text
你现在接手 AI-SUM 评分审计。请先阅读:
1) AI_CONTEXT-codex.md
2) accumulation_scan_v3.py
3) codex-ai-sum.py

目标:
- 验证 d1/d3/d4 去重是否应进一步收紧
- 判断 DEX证据字段是否只用于解释，还是可进入复核优先级
- 输出可执行变更清单（含回归测试方案）

约束:
- 先做旁路验证，再讨论是否修改主评分
- 所有结论必须可复现（附命令/路径）
```

---

## 12. Current Recommendation

当前建议是 **Option B**：

- 主评分继续保持现状
- 持续运行 `codex-ai-sum.py`
- 先把 `cluster_ratio + direct_dex_acc_pct + hop2_dex_acc_pct + gmgn_pass_acc_pct` 暴露到报告/复核层

如果连续 2-3 次运行里，这些 DEX 证据字段对高质量样本有稳定区分力，再讨论是否进入更正式的规则层。
