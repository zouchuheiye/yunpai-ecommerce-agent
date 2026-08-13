# 统筹 Agent 否定式只读请求修复实施计划

**目标：** 修复否定式安全说明误触发写操作确认，同时保持真实和混合写请求继续受门禁保护。

**架构：** 在统筹入口对写动作检测输入执行窄范围的否定作用域清洗；不改变全局策略模块。测试通过 HTTP/SSE 的用户可见响应验证门禁行为，并用纯函数边界用例覆盖混合表达。

**技术栈：** Python 3.11+、FastAPI、Pydantic、pytest。

## 全局约束

- 基线固定为 PR #9 提交 `adee44e31968804382ea222ace1ca2eb338a1b5f`。
- 不修改全局 `policy.py` 的高风险动作语义。
- 不新增依赖、Schema、HTTP 路由或响应字段。
- 不混入 PR #11 会话持久化或 PR #12 复合查询代码。
- 所有真实写请求仍必须返回 `requires_confirmation=true`，且不得执行写工具。

## Task 1：锁定用户可见回归

**文件：**

- 修改：`tests/test_workspace_agent.py`

**接口：**

- 消费：`POST /v1/admin/workspace/chat/stream`
- 产出：STD-02、STD-04 的否定式只读请求不会返回 `propose_action`

- [ ] 添加商品只读查询测试，模型返回只读计划时断言 `requires_confirmation=false` 且没有确认卡。
- [ ] 添加知识库/SOP 只读查询测试，覆盖同一句中的 `不修改或审批`。
- [ ] 运行两个测试并确认旧实现因返回 `propose_action` 而失败。

## Task 2：实现否定作用域清洗并保护真实写请求

**文件：**

- 修改：`src/ecommerce_agent/workspace_agent.py`
- 修改：`tests/test_workspace_agent.py`

**接口：**

- 产出：`_strip_negated_write_actions(message: str) -> str`
- 产出：`_requires_confirmation_request(message: str) -> bool` 仅对清洗后仍存在的写意图返回真

- [ ] 增加“不创建或修改实验”的只读边界测试。
- [ ] 增加“不要只查询，直接修改价格”的真实写请求测试。
- [ ] 增加“不修改商品，但创建采购单”的混合请求测试。
- [ ] 实现最小清洗逻辑：仅移除明确否定词直接控制的写动作，不删除同句其他动作。
- [ ] 运行新增测试并确认全部通过。
- [ ] 运行现有写操作门禁测试，确认退款、采购、改价等仍需确认。

## Task 3：回归、反证与交付

**文件：**

- 修改：`docs/tasks/WORKSPACE_NEGATED_ACTION_GUARD_PLAN.md`，记录实际测试证据

- [ ] 运行 `tests/test_workspace_agent.py` 和 `tests/test_policy.py`。
- [ ] 运行 Workspace 邻接 API 测试和 `python -m compileall -q src`。
- [ ] 运行 `git diff --check`。
- [ ] 临时禁用否定式清洗，确认 STD-02/STD-04 测试失败；立即恢复并复验。
- [ ] 检查 diff 只包含本修复文档、统筹门禁与对应测试。
- [ ] 提交独立小提交；不自行合并。

## 完成标准

- STD-02、STD-04 原始否定式输入不再触发确认卡。
- 三类真实或混合写请求仍触发确认，且零业务写入。
- 定向与邻接测试有新鲜通过证据。
- 谢良璇的真实模型复测仍是最终人工验收门禁；本地自动化通过不能替代该结论。
