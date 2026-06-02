# ⚙️ select-coin-AI-SUM 项目空间顶级自洽全局索引文件

> 本文件维护在 Google Drive 网页端 select-coin-AI-SUM 专属云盘空间根目录中。
> 用于追踪和归档每一次 AI-SUM 与 select-coin 迭代产生的核心代码，并精确映射到 VPS 远程部署物理路径。

---

## 📂 历史版本迭代归档

### 📅 2026-06-02 多源嵌套拓扑纠错着色升级

本轮迭代升级了 BubbleMap 多色拓扑嵌套着色算法，引入子 boss 优先过滤，消除 BASED 等代币的多源打款错配漏洞；并在 GitHub 层面对 17 个历史裸 PR 进行了 Issues 并发补齐与结项归档。

- **本期迭代备份目录**：[20260602/](file:///E:/Antigravity/scratch/select-coin-AI-SUM/20260602/)

| 专属空间临时文件 | 变更说明 | VPS 部署目标路径 | 部署同步状态 |
| :--- | :--- | :--- | :--- |
| 📄 [bubblemap_analyzer.py](file:///opt/select-coin/onchain/bubblemap_analyzer.py) | 注入子 boss 优先嵌套过滤算法，实现分发 X 完美对齐成员 X | `/opt/select-coin/onchain/bubblemap_analyzer.py` | ✅ 已同步并运行通过 |

### 📅 2026-05-19 真实浓度与大盘双轨制升级迭代

本轮重构通过 BubbleMap 排除交易所及合约等噪点地址，构建了“真实用户吸筹浓度” (D3 评分) 与“精英主力+大盘百分位门控”的双轨制打分机制 (D6 评分)。升级了长期综合画像落库 DDL，打通多维度指标展现。

- **本期迭代备份目录**：[20260519/](file:///E:/Antigravity/scratch/select-coin-AI-SUM/20260519/)

| 专属空间临时文件 | 变更说明 | VPS 部署目标路径 | 部署同步状态 |
| :--- | :--- | :--- | :--- |
| 📄 [pump_detector.py](file:///E:/Antigravity/scratch/select-coin-AI-SUM/20260519/pump_detector.py) | D3 升级真实用户吸筹浓度；D6 引入大盘百分位门控修正，彻底规避信噪压制。 | `/opt/AI-SUM/meta-verdict/pump_detector.py` | ✅ 已同步并运行通过 |
| 📄 [history_report.py](file:///E:/Antigravity/scratch/select-coin-AI-SUM/20260519/history_report.py) | 跨库批量获取浓度和大盘分；长期看板改版渲染双轨分；`token_history` 兼容扩展 DDL 落库。 | `/opt/AI-SUM/meta-verdict/history_report.py` | ✅ 已同步并落库成功 |
| 📄 [AI_CONTEXT.md](file:///E:/Antigravity/scratch/select-coin-AI-SUM/20260519/AI_CONTEXT.md) | 同步更新 V6 双轨制版本迭代日志，校准数据库表结构说明。 | `/opt/AI-SUM/AI_CONTEXT.md` | ✅ 已同步并完美更新 |
