# Session Memory

更新日期: 2026-03-27

> ⚠️ **强制规定**: 每次功能修改后，必须同步更新本文件及 AI-SUM/ 目录下所有关联 md 文件，并在 `changelog.md` 中记录修改。

## 交流规则

- 非专业内容全部使用中文。
- 必要的专业术语、代码、命令、接口名保留英文。
- 优先直接执行、验证、再总结；能从本地代码和数据库确认的内容尽量不靠猜测。

## 环境结论

- 当前机器可调用本机 `Python 3.13.4`。
- 数据库路径 (已更正):
  `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db`
  - ⚠️ 旧路径 `seletc-coin/seletc.db` 已失效，已在脚本中修正为 `select-coin/select.db`
- 数据库关键表已确认存在:
  `bubblemap_holders`、`token_names`、`token_snapshots`、`top_holders` 等。
- 当前数据库画像 (2026-03-19):
  - 数据库大小: `541 MB`
  - 原始代币 `472` 个；其中入选评分 `469` 个，链分布: `eth=266`、`bsc=188`、`base=18`
  - 所有代币均有 `≥ 10` 次快照（最多 13 次），趋势维度确认度高
  - 上次画像 (03-18): `1,514,513` 行、`469` 代币、`≥ 10` 次快照

## AI-SUM 项目状态

- 路径: `E:\AI-SUM`
- 主脚本: `E:\AI-SUM\accumulation_scan_v3.py`
- 当前脚本已支持:
  - Windows `cmd` 中文输出兼容处理
  - `1-4` 模式选择
  - `--backfill-mcap` 显式触发 CMC 市值回填
  - 自动导出扫描结果到 `csv/json`
  - 秒级扫描版本: 精确 join + 批量趋势/信号聚合
  - 使用 `(chain, token_address)` 作为代币主键，避免跨链串数
  - 最新快照结构指标 + 集中度 + 人工复核标签系统
- 关联文件:
  - `accumulation_scan_v3_methodology.md` — 评分方法论
  - `sql_reference.md` — SQL 查询参考手册
  - `changelog.md` — 修改记录
  - `SESSION_MEMORY.md` — 本文件
  - `report/` — 分析报告目录 (文件名: `report_YYYYMMDD.md`)

## 最近功能补记 (2026-03-27 v4.2.1)

- `accumulation_scan_v3.py` 执行工作流已修正:
  - 默认跳过 CMC 市值回填
  - 仅在显式传入 `--backfill-mcap` 时调用远程 API
- 触发原因:
  - 当前 `select.db` 实测 `token_names.market_cap_usd` 缺失 `473/473`
  - 若每次运行都自动回填，会把本地扫描错误拖成 10+ 分钟的串行网络任务
- 本地验证:
  - 去掉 Step 0 后，主扫描聚合/评分主路径约 `0.94s`
  - 慢点不在 SQL 或评分逻辑，而在默认启用的远程 CMC 回填

## 最近功能补记 (2026-03-27 v4.2.2)

- `accumulation_scan_v3.py` 的 CMC 代理已固定优先走 `socks5://127.0.0.1:18000`
- 触发原因:
  - `select-coin` 当前 `get_proxy()` 返回 `socks5://127.0.0.1:42000`
  - 用户要求 AI-SUM 这边强制走 `18000` 再执行回填脚本
- 脚本会在 Step 0 开始时直接打印实际使用的 CMC 代理，便于核对运行口径

## 最近功能补记 (2026-03-27 v4.2)

- `accumulation_scan_v3.py` 新增上游 DEX 证据观测字段导出:
  - `direct_dex_acc_pct`
  - `hop2_dex_acc_pct`
  - `gmgn_pass_acc_pct`
  - `gmgn_double_acc_pct`
- 这些字段来源于 select-coin / BubbleMap v6 的 DEX 证据链:
  - DEX pool 直接命中 (`dex_ratio`)
  - 来源地址二跳推断 (`dex_ratio_hop2`)
  - GMGN 地址页 swap 级验证 (`gmgn_verified`)
- `token_scores` 持久化已同步新增上述观测字段。
- `codex-ai-sum.py` 已升级到主引擎 `v4.0` 口径:
  - 保留 `d2/d5/d6/d7/d8`
  - 仅重算 `d1/d3/d4`
  - 去重时优先使用 `entity_id`
  - 报告中同步输出 DEX 证据观测
- `AI_CONTEXT-codex.md` 已重写为当前口径，不再停留在旧的 `v3.3` / 7维审计语境。

## 性能结论

- 原版主瓶颈不是数据量本身，而是 SQL join 写法。
- 关键慢点:
  `LEFT JOIN token_names t ON LOWER(b.token_address) = LOWER(t.token_address)`
  会绕开 `token_names(chain, token_address)` 主键索引。
- 已验证优化:
  - 改为"先 CTE 聚合，再按 `chain + token_address` 精确 join"
  - 趋势/信号由逐代币 N+1 查询改为批量聚合
- 实测收益:
  - 主查询约 `22.6s -> 0.19s`
  - 端到端脚本约 `24.2s -> 0.82-0.94s`

## accumulation_scan_v3.py 当前模式

- `1`: 全部等级
- `2`: 仅看 `S/A` 级
- `3`: 仅看 `S` 级
- `4`: 仅看汇总统计

## 最近一次扫描摘要 (2026-03-19 v4.0)

- 生成时间: `2026-03-19 20:09`
- 引擎版本: `V4.0 八维评分`
- 代币总数: `469`
- 等级分布:
  - `S=2`
  - `A=139`
  - `B=286`
  - `C=40`
  - `D=2`
- 复核分布:
  - 优先复核: `39`
  - 正常复核: `234`
  - 谨慎复核: `196`
- 快照深度: 全部 ≥ 10 次（10次=236、11次=109、12次=101、13次=26）
- d5 趋势分: 平均 67.9，不再打满
- d8 活跃度: 平均 42.6，覆盖 0-100 区间
- cluster_ratio ≥ 10% 的代币: 92 个

### 与上次 (v3.3) 对比

| 指标 | v3.3 | v4.0 | 变化 |
|------|-------|-------|------|
| 代币总数 | 469 | 469 | +0 |
| S 级 | 20 | 2 | -18 |
| A 级 | 230 | 139 | -91 |
| B 级 | 192 | 286 | +94 |
| C/D 级 | 27 | 42 | +15 |

变化原因: d5 时间衰减重写(平均降 30分) + d5降权(10%→6%) + d8新维度(10%) + cluster_ratio去重影响 92 个代币的 d1/d4。

## 最近一次 Codex 审计摘要 (2026-03-18 18:37)

- 基线文件: `E:\AI-SUM\exports\accumulation_scan_v3_20260318_181047.json`
- 评审脚本: `E:\AI-SUM\codex-ai-sum.py`
- 本轮口径:
  - `d1/d4` 使用聚类加权去重
  - `d3` 与主引擎 v3.3 完全对齐 (`latest_acc_hold * 2.5`)
- 分布变化:
  - 基线: `S=19 / A=238 / B=185 / C=27 / D=0`
  - 修正: `S=12 / A=242 / B=189 / C=26 / D=0`
- 影响统计:
  - 降级: `15`
  - 升级: `3`
  - 持平: `454`
  - 平均分变化: `-0.26`
  - 中位分变化: `-0.02`
- 样本 `CLO`:
  - `S 80.30 -> A 73.59`
  - `cluster_ratio=52.94%`
  - `acc_pct: 96.96% -> 55.59%`

## 最近一次 >10 关联数加权实验 (2026-03-18 20:30)

- 命令: `python -X utf8 codex-ai-sum.py --assoc-gt10-bonus`
- 规则: `max(inbound_addresses, outbound_addresses) > 10` 的吸筹地址按 `2.0` 单位质量计入 `d1/d4` 权重
- 影响覆盖:
  - 受影响钱包: `74`
  - 受影响代币: `58`
- 分布结果:
  - 基线: `S=20 / A=230 / B=192 / C=27 / D=0`
  - 实验: `S=11 / A=241 / B=187 / C=27 / D=3`
  - 与未加权的 Codex 审计相比: **等级分布不变**
- 最大增分:
  - `LISTA +0.22`
  - `FXS +0.13`
  - `ERA +0.13`
- 结论: 当前库里 `>10` 关联数信号覆盖很低，更像微调因子而不是主导因子。

## 最近一次导出结果

- 导出目录: `E:\AI-SUM\exports`
- 最新文件:
  - `E:\AI-SUM\exports\accumulation_scan_v3_20260318_181047.csv`
  - `E:\AI-SUM\exports\accumulation_scan_v3_20260318_181047.json`
  - `E:\AI-SUM\exports\codex_ai_sum_20260318_183703.json`
  - `E:\AI-SUM\exports\codex_ai_sum_assoc_gt10_bonus_20260318_203016.json`

## 人工复核优先名单 (2026-03-16 更新)

### 优先复核 S 级 TOP 10（最高质量标的）

| 代币 | 链 | 综合分 | 卖/买比 | 快照 | Top5% | 标签 |
|------|-----|--------|---------|------|-------|------|
| Anoma (XAN) | eth | 82.7 | 2.9% | 6 | 13.5% | 买盘强·分散 |
| Zircuit (ZRC) | eth | 80.1 | 3.4% | 6 | 4.8% | 分散 |
| Space and Time (SXT) | eth | 79.7 | 0.7% | 6 | 12.63% | 分散 |
| Yei Finance (CLO) | bsc | 80.3 | 22.8% | 8 | 26.01% | 买盘强·良好 |
| SKYAI (SKYAI) | bsc | 77.1 | 15.0% | 6 | 3.04% | 买盘强·分散 |
| Alchemy (ACH) | eth | 75.4 | 12.3% | 6 | 21.65% | 结构良好 |

### 谨慎复核 S 级（高分但有风险）

- `ETHGas (GWEI)` — 综合 83.7，**Top5=63.61%** 集中度极高 + 卖压 59.4%
- `Humanity (H)` — 综合 82.1，**Top5=81.5%** 高集中度
- `Unibase (UB)` — 综合 85.0，卖压 51.1%
- `Elsa (ELSA)` — 综合 81.5，**卖压 90.1%** 极高
- `Stafi (FIS)` — 综合 80.0，**Top5=58.58%** 单一大户主导
- `Perpetual (PERP)` — 综合 80.1，卖压 66.6% + 集中度偏高
- `Bluzelle (BLZ)` — 综合 78.4，Top5=53.52% + 卖压 50.2%

### 持续表现优秀（两次均为高优先复核）

- `Beat Token (Beat)` — 10 次快照，结构始终分散
- `Quack AI Token (Q)` — 9 次快照，买盘强劲
- `Yei Finance (CLO)` — 8 次快照，结构良好
- `Obol Network (OBOL)` — 7 次快照，持续稳定

## 本次会话沉淀的分析框架

- 高综合分不等于结构健康，必须叠加最新快照结构指标来判断。
- 最新快照优先关注:
  - `latest_only_buy_pct`
  - `top1_acc_hold`
  - `top3_acc_hold`
  - `top5_acc_hold`
  - `latest_acc_holders`
- 推荐判读法:
  - 高分 + 低卖压 + 低 `top5_acc_hold` + 多次快照 = 高质量分散吸筹
  - 高分 + 高集中度 = 大户控盘/结构脆弱风险
  - 单快照高分 = 候选，不宜过度解读 (当前已不存在此情况)

## 已知分析注意点

- 当前 `market_cap_usd` 基本全部缺失或为 `0`，不能直接用于判断市值位置。
- 导出里的 `acc_hold`、`cex_hold` 是跨快照聚合后的相对强弱指标，不适合直接当作真实供应占比。
- 最新快照结构分析与历史聚合评分应分开看，避免把"历史累计强"误判为"当前结构健康"。
- **趋势维度(d5)膨胀问题**: 已在 v3.2 通过平方压缩解决。
- **S 级数量过多(142)**: 已在 v3.3 通过 d3 口径修正解决（S=19）。
- **集中度未纳入评分**: 已在 v3.2 作为 d7 维度纳入。

## 文档同步规则

> ⚠️ **强制规定**: 每次功能修改后，必须同步更新以下文件，并在 `changelog.md` 中记录修改:
>
> | 文件 | 更新内容 |
> |------|----------|
> | `SESSION_MEMORY.md` | 环境信息、扫描结果、复核名单 |
> | `accumulation_scan_v3_methodology.md` | 评分逻辑、数据画像、局限与优化 |
> | `sql_reference.md` | SQL 查询模板 |
> | `changelog.md` | 修改记录 |
> | `accumulation_scan_v3.py` | 代码配置 (DB_PATH 等) |
> | `report/report_YYYYMMDD.md` | 每次扫描的分析报告 |
