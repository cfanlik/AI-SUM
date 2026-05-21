# Token Accumulation Suite 研发迭代管理标准操作规范 (SOP)

本手册为 `select-coin` 与 `AI-SUM` 联合项目的日常开发、需求迭代及缺陷修复的标准协同指南。所有开发工作已全面向 **GitHub Issues + GitHub Projects (V2 看板)** 看齐，废除繁琐的手动 Markdown 记录。

---

## 1. 研发协同架构图

```
                        【 全局 GitHub 看板: Token-Accumulation-Suite 】
                                               │
                       ┌───────────────────────┴───────────────────────┐
                       ▼                                               ▼
         【 select-coin.git 】                                  【 AI-SUM.git 】
  (Web Frontend/Backend, Scraper)                        (5大分析引擎 + meta-verdict)
    ├── Issues (去中心化需求/Bug)                           ├── Issues (去中心化算法需求/Bug)
    └── Pull Requests (代码审查/合并)                       └── Pull Requests (算法模型合并)
```

---

## 2. 研发标准流程 (SOP)

为保证代码的稳定度与看板状态的自动流转，所有研发迭代必须遵循以下 5 大步骤：

### 步骤一：创建 Issue 进行需求/缺陷定义
任何开发任务（无论是新增功能还是 Bug 修复），必须在对应的仓库创建 Issue。
1. 点击 GitHub 仓库顶部的 `Issues` -> `New issue`。
2. 选择对应的模板：
   - **🚀 功能需求 (Feature Request)**：新增功能点、接口、或前端看板改造。
   - **🐛 缺陷修复 (Bug Report)**：系统崩溃、接口报错、或大盘/精英评分倒挂等 Bug 修复。
3. 标题必须符合 Angular/Conventional Commit 规范：
   - 格式：`feat: 简短描述` 或 `fix: 简短描述`
   - 示例：`feat: 前端新增 D10 Pool 健康度指标展示与共振徽章`
4. 为 Issue 打上标签（如 `iteration` 代表本次迭代）。

### 步骤二：看板排期与认领
1. 打开全局 **Token-Accumulation-Suite** 看板项目。
2. 刚才新建 of Issue 会自动同步进入 `Todo` 泳道。
3. 手动将卡片指派给自己 (`Assignees`)，并设定本期迭代。
4. 开始开发时，将卡片拖入 `In Progress` 泳道。

### 步骤三：本地拉取规范分支
本地开发禁止直接在 `master` 分支提交，必须拉取功能分支：
```powershell
# 分支命名规范: <类型>/<Issue编号>-<简短英文描述>
# 示例:
git checkout -b feat/42-pool-health-d10
```

### 步骤四：规范 Git 提交信息 (Conventional Commit)
在本地编辑完代码后，进行批量暂存与提交：
```powershell
# 提交信息规范: <类型>(<作用域>): <中文简短描述>
# 示例：
git add -A
git commit -m "feat(web): 升级 D10 Pool 稳定性为 100 分制并加入 S11 信号"
```

### 步骤五：发起 Pull Request (PR) 并触发看板自动 Done
开发完毕后将分支推送至 GitHub 远端，并向 `master` 发起 PR：
1. 在 PR 描述中使用 GitHub 自动关联关键字绑定 Issue。
   - 格式：`Closes #<Issue编号>`
   - 示例：`Closes #42`
2. **跨仓库依赖声明**：如果此 PR 依赖于另一个仓库的改动，请在描述中显式追加：
   - 示例：`Depends on cfanlik/AI-SUM#18`
3. 审核通过合并 PR 后，关联的 Issue 将被 **GitHub 自动关闭**，且看板卡片会 **自动转为 Done**。

---

## 3. 常见协作规范与 FAQ

### Q1: 一个功能涉及两个仓库，Issue 怎么提？
- **A**：分别在两个仓库提 Issue。
  - 在 `AI-SUM` 中提：`feat: 升级 D10 满分 100 权重体系，引入 Pool 稳定性维度` (如 #18)
  - 在 `select-coin` 中提：`feat: DEX Pool 维度整合与前端可视化改造` (如 #42)
  - 并在 `select-coin` 的 PR 中声明依赖：`Depends on cfanlik/AI-SUM#18`。

### Q2: 以前写在 AI_CONTEXT.md 里的最近变更怎么处理？
- **A**：已由初始化脚本通过 GitHub API 批量灌入两仓库的历史 Issues 中，并置于 `closed` 状态归档。此后不需要在 MD 中手动写变更日志，直接通过 GitHub Releases 或 Issues 看板追溯。
