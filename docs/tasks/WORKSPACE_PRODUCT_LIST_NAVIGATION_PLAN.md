# 统筹 Agent 商品清单与详情导航实施计划

> **执行方式：** 本会话内按 TDD 顺序执行。`.project-to-act` 保持只读，不使用子代理，
> 每个任务先得到失败测试，再写最小实现并复验。

**目标：** 商品目录回答稳定展示最多 10 个商品名称与 SKU，并提供安全跳转到高级管理
商品页的“查看详细商品”按钮。

**架构：** 商品事实完整性由 Python 展示层和回答守卫负责；页面导航复用后端现有
`advanced_view`，浏览器只根据固定白名单生成站内链接。高级管理页验证 `view` 查询
参数后选择已有视图，不新增业务 API 或数据库迁移。

**技术栈：** Python 3.11、Pydantic v2、FastAPI SSE、原生 HTML/CSS/JavaScript、pytest。

## 全局约束

- 商品回答最多展示 10 条，展示项必须保留名称与 SKU。
- 只允许站内只读导航；不得执行商品同步、编辑、上下架或其他写操作。
- 不新增 Schema 版本、第三方依赖或关键词到工具的确定性路由。
- 不修改 `.project-to-act` 台账。
- 正式 PR 依赖 PR #11 和 PR #12，前置 PR 合并后再整理评审差异。

---

## 任务 1：商品清单上限与身份完整性守卫

**文件：**

- 修改：`src/ecommerce_agent/workspace_presenter.py`
- 修改：`src/ecommerce_agent/workspace_agent.py`
- 测试：`tests/test_workspace_presenter.py`
- 测试：`tests/test_workspace_agent.py`

**接口：**

```python
MAX_PRODUCT_FACTS = 10

def critical_observation_values(
    tool_name: str,
    observation: dict[str, Any],
    product_view: dict[str, Any],
) -> list[str]:
    """Return values that a customer-facing answer must preserve verbatim."""
```

- [ ] 增加失败测试：12 个商品只生成前 10 条商品事实，第 11、12 个不出现。
- [ ] 增加失败测试：前 10 个商品的名称和 SKU 全部进入必须保留值。
- [ ] 运行：

  ```powershell
  python -m pytest -q tests/test_workspace_presenter.py -k "product and limit"
  ```

  预期：旧实现因只展示 8 条且不保护中文名称而失败。

- [ ] 将 `_product_facts()` 和 `_product_search_facts()` 的展示上限统一为 10。
- [ ] 实现 `critical_observation_values()`：商品目录与商品搜索从结构化 `items` 读取前
  10 个 `title`、`sku_id`；其他工具继续使用现有数字和标识符提取。
- [ ] 将 `_run_read_task()` 改为使用新函数，不解析已经格式化的中文句子来猜商品名。
- [ ] 增加 Agent 失败测试：回答模型遗漏一个展示商品名称时，触发
  `critical_value_mismatch` 并返回确定性清单。
- [ ] 运行：

  ```powershell
  python -m pytest -q tests/test_workspace_presenter.py tests/test_workspace_agent.py -k "product or catalog"
  ```

- [ ] 提交：`fix: preserve workspace product list identities`

## 任务 2：受控商品详情导航

**文件：**

- 修改：`docs/agent-workspace.html`
- 修改：`docs/admin-console.html`
- 测试：`tests/test_workspace_agent.py`
- 测试：`tests/test_admin_console.py`

**浏览器契约：**

```javascript
const advancedDestinations = {
  commerce: {label: '查看详细商品', href: '/admin/advanced?view=commerce'}
};

function detailActionHtml(advancedView) {
  const destination = advancedDestinations[advancedView];
  return destination
    ? `<a class="button small" href="${destination.href}">${destination.label} ↗</a>`
    : '';
}
```

- [ ] 增加失败测试：工作台包含受控 `commerce` 映射、中文按钮和目标地址。
- [ ] 增加失败测试：SSE `done.response.advanced_view="commerce"` 被传给消息渲染详情。
- [ ] 实现固定白名单 `advancedDestinations`；不得将模型或响应中的任意 URL 写入 DOM。
- [ ] 让实时消息完成后追加详情导航；写确认卡片继续使用原有独立逻辑。
- [ ] 增加失败测试：高级管理页面读取 `view`，只接受现有 `.nav [data-view]` 声明的值。
- [ ] 修改 `bootstrapConsole()`：鉴权成功后调用 `switchView(initialView)`；无效参数回退
  `overview`。现有侧边栏点击行为不变。
- [ ] 运行：

  ```powershell
  python -m pytest -q tests/test_workspace_agent.py tests/test_admin_console.py -k "advanced or product or catalog"
  ```

- [ ] 提交：`feat: link workspace product summaries to catalog`

## 任务 3：回归与交付证据

**文件：**

- 修改：`docs/tasks/WORKSPACE_PRODUCT_LIST_NAVIGATION_PLAN.md`（只更新勾选状态和实际证据）

- [ ] 运行完整工作区定向测试：

  ```powershell
  python -m pytest -q tests/test_workspace_read_plan.py tests/test_workspace_presenter.py tests/test_workspace_agent.py tests/test_admin_console.py tests/test_api.py
  ```

- [ ] 运行静态检查：

  ```powershell
  python -m compileall -q src
  git diff --check
  ```

- [ ] 确认没有修改数据库版本、`.project-to-act` 或业务写接口。
- [ ] 记录实际测试数量；未运行全量测试时必须明确写“未运行全量”，不得沿用旧数字。
- [ ] 提交：`docs: record product list navigation verification`
- [ ] 前置 PR 合并后同步最新 `main`，再普通推送并创建 Draft PR；不自行合并。
