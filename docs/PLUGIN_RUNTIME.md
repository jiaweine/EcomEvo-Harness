# Plugin Runtime

EcomEvo 的插件单元不是展示标签，而是 Runtime 执行图中的真实实例。内置组件、启动注入与外部包使用同一组固定插件槽位和结构化能力契约。

## Runtime guarantees

| Guarantee | Behavior |
|---|---|
| Structural contract | 替换前检查插件槽位要求的方法与属性 |
| Atomic rebind | 替换失败时恢复实例、版本、来源、代次与依赖图 |
| Lifecycle | 可选同步 `plugin_start(context)` 与 `plugin_stop(context)` |
| Run isolation | 任一任务执行期间拒绝插件变更 |
| Explicit loading | 发现 entry point 不导入模块；只有 `load_plugin` 执行第三方代码 |
| Fixed topology | 外部插件只能占用已注册槽位，不能静默创建新的执行路径 |

## Trust boundary

外部插件与应用本身拥有相同的进程权限，必须视为受信任的部署代码。结构化契约只能验证接口形状，不能证明自定义 Sandbox 或 Verifier 的业务语义正确。

替换 Sandbox、Verifier 或 Event Store 属于部署管理员操作，不属于 Harness Evolver 的可学习坐标。生产合并前仍需运行安全回归、replay gate 与业务策略测试。

## Replace a component between tasks

```python
from ecomevo.runtime import EcomEvoEngine
from my_runtime import CompanyPlanner

engine = EcomEvoEngine("outputs/runtime.db")
engine.replace_plugin("planner.adaptive", CompanyPlanner(), version="2.0.0")
```

替换 Planner 后，Runtime 会同步更新自主控制器与决策策略中的 Planner 引用。Tool Registry、PTC、Skill Library、Recursive Agent、Sandbox 与 Verifier 也按各自依赖关系重绑定。

必需插件不能禁用。`model.gateway` 与 `mcp.remote` 是可选槽位，可以在任务之间启用或禁用。

## External package contract

外部包在 `pyproject.toml` 中声明：

```toml
[project.entry-points."ecomevo.plugins"]
company_planner = "company_ecomevo.plugin:CompanyPlannerBundle"
```

入口对象提供 manifest 与 create：

```python
class CompanyPlannerBundle:
    manifest = {
        "key": "planner.adaptive",
        "api_version": "1",
        "version": "2.0.0",
    }

    def create(self):
        return CompanyPlanner()
```

发现阶段不会导入第三方模块：

```python
candidates = engine.discover_plugins()
```

加载必须显式触发，并会依次执行 API 版本校验、能力契约校验、生命周期启动和依赖重绑定：

```python
descriptor = engine.load_plugin("company_planner")
```

## Plugin slots

| Key | Required capability | Runtime dependents |
|---|---|---|
| `planner.adaptive` | goal parsing、belief initialization、planning、patch apply | autonomy、decision policy |
| `tool.registry` | tool catalog、planned calls | PTC、autonomy、decision policy |
| `tool.ptc` | bounded execution | autonomy |
| `memory.skills` | skill retrieval、policy state | failure evolver、autonomy、decision policy |
| `agent.recursive` | bounded review | autonomy、delegator |
| `sandbox.action` | tool validation | PTC、autonomy、policy、replay gate |
| `verifier.decision` | deterministic verification | autonomy、final verification |
| `model.gateway` | current provider | optional reasoning provider |
| `mcp.remote` | read specs and tool calls | optional enterprise connector |

Event Store、Runtime Memory、Harness Evolver 与 Failure Evolver 也有独立插件槽位。完整运行时目录可通过 `engine.plugins.describe()` 获取，其中包含 source、generation、contract_valid 与 contract_missing。
