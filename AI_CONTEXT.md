# AI-SUM — AI 可读项目文档

> **全局吸筹扫描分析系统**：基于 BubbleMap 数据库（Top 300 持有者），对所有代币做多维评分与行为模式识别，输出钻石/红色/黄色三级雷达预警。

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.13 |
| 数据库 | SQLite（外部共享 `select-coin/data/select.db`，独立分析库 `select-sum.db`） |
| 输出 | 终端 + Markdown 雷达报 + CSV/JSON 导出 |
| 部署 | 本地 Windows + VPS Linux（`/opt/AI-SUM/`） |

## 目录结构

```
AI-SUM/
├── AI_CONTEXT.md                     # ★ 本文件：AI 项目索引
├── .env                              # 环境配置（SRC_DB_PATH/SUM_DB_PATH/阈值）
├── .gitignore
├── requirements.txt
│
├── accumulation_scan_v3.py           # V3 主脚本（截面评分 S/A/B/C/D）
├── AI-SUM-0408.py                    # V5 顶层入口（路由 python-ai/）
├── run_v5.py                         # V5 备用入口
├── vps_deploy_master.py              # VPS 一键部署+执行+下载报告
│
├── python-ai/                        # V5 原版模块（基线，不含 V8.2）
│   ├── config.py
│   ├── db_loader.py
│   ├── engine.py
│   ├── pattern_detector.py            # A/B/C 三模式
│   ├── report_generator.py
│   ├── time_series_aligner.py
│   └── watchlist_tracker.py
│
├── master-scan/                      # ★ V8.2 生产版（含钻石绞杀区）
│   ├── run.py                         # 统一入口
│   ├── config.py                      # 全局配置（.env 读取）
│   ├── db_loader.py                   # SQL 封装 + ATTACH 只读
│   ├── time_series_aligner.py         # SnapshotDiff + V8.2 计算
│   ├── pattern_detector.py            # A/B/C/E 四模式识别
│   ├── report_generator.py            # 雷达报生成（钻石+红色）
│   ├── engine.py                      # 主调度器
│   └── watchlist_tracker.py           # 追踪生命周期
│
├── data/                             # 独立分析库（.gitignore）
│   └── select-sum.db
├── report/v5/                        # 雷达报输出目录
└── exports/                          # CSV/JSON 导出
```

## 数据源

| 项 | 说明 |
|---|---|
| 本地源库 | `C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db` |
| VPS 源库 | `/opt/select-coin/data/select.db` |
| 主表 | `bubblemap_holders` — 每行 = 地址在某代币某快照的持仓 + 评分 |
| 辅表 | `token_names` — 代币名称/符号/市值 |
| 评分表 | `token_scores` — 历次扫描的 8 维评分 |

### bubblemap_holders 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `chain` | TEXT | 链名 (bsc/eth/base) |
| `token_address` | TEXT | 代币合约地址 |
| `snapshot_time` | TEXT | 快照时间 |
| `rank` | INTEGER | 持仓排名 (1-300) |
| `hold_percentage` | REAL | 持仓占比 (%, 100进制: 2.5=2.5%) |
| `acc_score` | REAL | 地址级吸筹评分 (0-100) |
| `is_accumulating` | INTEGER | 是否吸筹 (0/1) |
| `is_cex` / `is_dex` / `is_contract` / `is_supernode` | INTEGER | 地址分类 |
| `dex_ratio` | REAL | 直接 DEX 交互比例 (0-1) |
| `dex_ratio_hop2` | REAL | 二跳 DEX 来源比例 (0-1) |
| `gmgn_verified` | INTEGER | GMGN swap 级验证 (0/1/2) |
| `entity_id` | TEXT | 聚类实体 ID |

## V8.2 Master-Scan 架构

### 四大行为模式

| 模式 | 触发逻辑 | 意义 |
|------|----------|------|
| **A 地址聚合** | 12h 换手率 >25%(黄)/40%(红) + 新吸筹涌入 | 底层筹码重组 |
| **B 新鲸下场** | 新进地址均分≥72 + 纯买入≥80% + 持仓>0.5% | 高质量鲸鱼入场 |
| **C 爆发前静默** | 持仓历史高位 + 换手<5% + 只买不卖≥70% | 吸筹完毕静待拉升 |
| **E 钻石绞杀** | 机构控盘率≥阈值 + DEX真金率≥阈值（默认双85%） | 极低流通+真金吸筹 |

### V8.2 核心指标计算

#### 机构控盘率 (institutional_hold_v8)
```
基础设施地址(is_cex/is_contract/is_supernode) 持仓
+ 持仓≥2% 的大户持仓
= 机构控盘率 (100进制)
```

#### 隐庄 (hidden_whale_count)
```
非基础设施 + 持仓≥2% 的地址数量
```

#### DEX真金率 (dex_verified_pct) — V8.3
```
吸筹地址中满足以下任一条件:
  1. dex_ratio ≥ 0.5
  2. dex_ratio_hop2 ≥ 0.5（二跳穿透）
  3. gmgn_verified ≥ 1
  4. 同 entity_id 组内有地址满足上述条件（聚簇穿透）
/ 吸筹地址总数 × 100 (100进制)
```

### 已知限制

| 问题 | 状态 | 优化方向 |
|------|------|----------|
| A→B→C 多跳转账导致 C 的 dex_ratio=0 | ✅ V8.3 已修复 | `dex_ratio_hop2` 二跳穿透 |
| 同主体多钱包未穿透 | ✅ V8.3 已修复 | `entity_id` 聚簇共享 |
| 小地址噪声拉低 DEX率 | 待定 | 持仓加权替代数量占比 |
| 历史中位数被早期低值拉低 | ✅ V8.3 已修复 | 近20快照滑动窗口 |
| 无价格维度 | 待定 | 引入价格-持仓背离检测 |

### 报告输出格式

仅展示两个核心区块：
1. **💎 钻石绞杀区** — 双阈值严格通过（默认双85%，可通过 .env 调整）
2. **🔴 红色预警** — A/B/C 模式触发

统一8列：`代币 | 链 | 信号级别 | 触发模式 | 机构控盘率 | 隐庄 | DEX真金率 | 吸筹数`

不展示：🟡 黄色关注、📋 Watchlist（避免报告冗长）

### 运行方式

```bash
# 本地执行
python master-scan/run.py

# VPS 一键部署+执行+下载报告
python vps_deploy_master.py
```

### 环境配置 (.env)

```env
SRC_DB_PATH=C:\Users\Administrator\.gemini\antigravity\playground\select-coin\data\select.db
SUM_DB_PATH=E:\AI-SUM\data\select-sum.db
DIAMOND_INST_THRESHOLD=85.0
DIAMOND_DEX_THRESHOLD=85.0
HIDDEN_WHALE_HOLD_THRESHOLD=2.0
```

## V3 截面评分（保留）

### 八维综合评分

```python
composite = (d1 * 0.20 +   # 吸筹占比       → 80% = 满分
             d2 * 0.15 +   # 吸筹均分       → 55-100 映射
             d3 * 0.14 +   # 持仓控制度     → 40% = 满分
             d4 * 0.12 +   # 卖出抑制       → 66.7% = 满分
             d5 * 0.06 +   # 趋势稳定性
             d6 * 0.13 +   # 信号质量
             d7 * 0.10 +   # 集中度
             d8 * 0.10)    # 实时活跃度
```

### 五级评价

| 等级 | 阈值 | 含义 |
|------|------|------|
| **S** | ≥ 75 | 极强吸筹 |
| **A** | ≥ 60 | 强吸筹 |
| **B** | ≥ 45 | 中等吸筹 |
| **C** | ≥ 30 | 弱吸筹 |
| **D** | < 30 | 微弱信号 |

## 版本历史

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| **V8.3** | **2026-04-09** | hop2二跳+entity聚簇穿透DEX率、近20滑动窗口中位数、阈值/隐庄配置化(.env)、双85%默认阈值 |
| V8.2 | 2026-04-08 | master-scan 生产版：钻石绞杀区(模式E)、V8.2 三指标(机构控盘/隐庄/DEX真金率)、统一8列报告格式 |
| **V5.1.2** | 2026-04-07 | `accumulation_scan_v3.py` 适配 `.env`，修复 Linux 兼容 |
| **V5.0** | 2026-04-03 | 全新多快照时序分析：SnapshotDiff、A/B/C 三模式、Watchlist 追踪 |
| v4.2.2 | 2026-03-27 | CMC 代理固定 `socks5://127.0.0.1:18000` |
| v4.0 | 2026-03-19 | 8维评分(+d8)、d5时间衰减、cluster_ratio |
