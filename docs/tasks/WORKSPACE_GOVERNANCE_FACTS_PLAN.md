# 统筹 Agent 治理事实呈现修复实施计划

**目标：** 补齐治理只读 observation 中质检与客户 Agent 评测事实的中文呈现，修复 STD-13/14，同时保持租户、安全和零写入边界。

**架构：** 沿用既有 `get_governance_status` 数据源，只在 `workspace_presenter.py` 将 `quality` 和 `evaluations` 的白名单字段翻译为完整中文事实。回答模型继续只接收 presenter 产物，不接收内部 JSON。

**技术栈：** Python 3.11+、pytest、现有 Workspace presenter。

## 全局约束

- 基线固定为 PR #9 提交 `adee44e31968804382ea222ace1ca2eb338a1b5f`。
- 不新增工具、HTTP 路由、Schema 或第三方依赖。
- 不修改全局 `policy.py`、质检规则或评测算法。
- 不传递内部字段、运行 ID、套件 ID、原始用例、原始回答或顾客数据。
- 不混入 PR #11、PR #12 或否定式门禁修复。
- 自动化通过不能替代谢良璇真实模型环境的 STD-13/14 人工复测。

## Task 1：锁定 STD-13 质检事实

**文件：**

- 修改：`tests/test_workspace_presenter.py`
- 修改：`src/ecommerce_agent/workspace_presenter.py`

**接口：**

- 消费：`observation["quality"] = {"total_runs": int, "pending_reviews": int, "average_score": number}`
- 产出：一条不含内部字段的中文质检事实

- [x] 增加失败测试：6 条质检、2 条待复核、平均分 93.5 必须完整呈现。
- [x] 运行该测试并确认旧实现因缺少质检事实而失败。
- [x] 增加 `_quality_facts(summary: dict[str, Any]) -> list[str]`，只读取三个白名单字段。
- [x] 无记录时输出“目前没有质检记录，待人工复核 0 条”。
- [x] 运行定向测试并确认通过。

## Task 2：锁定 STD-14 客户 Agent 评测事实

**文件：**

- 修改：`tests/test_workspace_presenter.py`
- 修改：`src/ecommerce_agent/workspace_presenter.py`

**接口：**

- 消费：`observation["evaluations"] = {"suites": dict[str, int], "runs": dict[str, int], "latest_run": dict | None}`
- 产出：套件计数、运行计数及最近完成状态的中文事实

- [x] 增加失败测试：1 个冻结套件、1 次通过运行必须呈现套件数量、运行数量和通过状态。
- [x] 运行该测试并确认旧实现因缺少评测事实而失败。
- [x] 增加固定状态中文映射；未知状态只输出“状态待核实”。
- [x] 增加 `_evaluation_facts(overview: dict[str, Any]) -> list[str]`。
- [x] 无运行时输出“目前还没有评测运行记录”，不能输出模块不可用。
- [x] 运行定向测试并确认通过。

## Task 3：安全、回归与交付

**文件：**

- 修改：`tests/test_workspace_presenter.py`
- 修改：`docs/tasks/WORKSPACE_GOVERNANCE_FACTS_PLAN.md`

- [x] 增加内部字段不泄露测试，覆盖 `runner_version`、运行/套件 ID、问题明细和原始文本。
- [x] 运行 `tests/test_workspace_presenter.py`。
- [x] 运行 `tests/test_workspace_agent.py`、`tests/test_governance.py`、`tests/test_customer_evaluations.py` 和邻接 API 测试。
- [x] 运行 `python -m compileall -q src` 与 `git diff --check`。
- [x] 临时移除质检/评测 presenter 接线，确认 STD-13/14 用例失败；立即恢复并复验。
- [x] 将 RED、GREEN、反证和回归数量写入本计划。
- [x] 精确暂存设计、计划、presenter 与测试，创建独立小提交；不自行合并。

## 实施证据（2026-08-13）

- RED：质检、评测、空值/未知状态三条新增 presenter 测试在旧实现上均失败，失败点为治理呈现只包含知识与 SOP。
- GREEN：新增三条 presenter 测试 `3 passed`。
- Presenter 全套与真实 Agent 接线：最初 `8 passed`；补充“仅有运行中任务、尚无已完成运行”的保守边界后最终 `9 passed`。
- Workspace 专项：`23 passed`。
- 治理与客户评测服务：`17 passed`。
- 管理页与邻接 API：`10 passed`。
- 反证：临时移除 `_governance_facts()` 对质检和评测的接线后，STD-13/14 两条用例均按预期失败；恢复后 `8 passed`。
- 静态检查：`python -m compileall -q src` 与 `git diff --check` 通过。
- 全量测试：运行 20 分钟后在约 34% 被命令超时终止，期间未显示失败；该结果不计为通过。
- 待执行：推送和堆叠 Draft PR。
- 待人工验收：谢良璇真实模型环境复跑 STD-13/14。

## 完成标准

- STD-13/14 对应 observation 能生成完整中文事实。
- 空数据、未知状态和内部字段边界均有自动化覆盖。
- 工作树差异不包含其他 PR 或运行数据。
- 真实模型人工复测仍保持待验收，未伪报通过。
