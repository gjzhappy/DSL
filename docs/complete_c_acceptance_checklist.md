# 完整 C Phase 15 发布前 Acceptance Checklist

本 checklist 用于 Phase 15 最终验收：不新增架构、不新增主流程能力，只确认完整 C 主链路、contract、compact context 边界、compiler / chart / answer gate、DSL shard 与 Dify 画布布局已经达到发布前要求。

## 1. 主链路验收

| 路径 | 入口节点 | 关键 contract | 关键 route | 进入 compiler | 保存 compact context | Answer 输出字段 |
|---|---|---|---|---|---|---|
| `new_query` | `用户输入` → `代码执行_读取并规范化会话状态` → `条件分支_query类型判断(false)` | `collection_selection_contract`、`semantic_plan_contract`、`schema_runtime_context_contract`、`validator_result_contract`、`compiler_execution_contract`、`chart_payload_contract`、`answer_payload_contract`、`context_update_contract` | collection selector → schema retrieval iteration → planner → normalizer → runtime schema context / hydration → validator `valid` | 是，仅 validator `valid` 分支 | 是，`变量赋值_保存多轮上下文` 写 `last_context_json` / `last_answer_payload_json` / `last_context_update_json` | `answer_payload_markdown` |
| `refine full_replan` | `代码执行_尝试确定性查询修正` → `条件分支_patch结果判断(false)` | `patch_result_contract`、`collection_selection_contract`、`semantic_plan_contract`、`schema_runtime_context_contract`、`validator_result_contract` | deterministic patch → full replan → refine collection selector / schema retrieval / planner → validator `valid` | 是，仅 validator `valid` 分支 | 是 | `answer_payload_markdown` |
| `use_patch` | `代码执行_尝试确定性查询修正` → `条件分支_patch结果判断(true)` | `patch_result_contract`、`semantic_plan_contract`、`schema_runtime_context_contract`、`validator_result_contract` | deterministic patch → unified Mongo input preparation → runtime schema context / hydration → validator `valid` | 是，仅 validator `valid` 分支 | 是 | `answer_payload_markdown` |
| `chart_only` | `条件分支_多轮路由(chart)` | `patch_result_contract`、`chart_request_contract`、`chart_payload_contract`、`answer_payload_contract`、`context_update_contract` | chart planning → `ChartPayloadBuilder` → chart answer adapter | 否 | 是，adapter 输出 compact update，不覆盖上一轮 plan | `answer_payload_markdown` |
| `validator non-valid` | `条件分支_validator结果判断` 的 `requires_clarification` / `blocked` / `needs_replan` / `false` | `validator_result_contract`、`answer_payload_contract`、`context_update_contract`、`compact_context_contract` | validator non-valid route → non-valid context saver → non-valid Answer | 否 | 是，保存 validator 产生的最小 context update；不覆盖上一轮成功 plan | `answer_payload_markdown` |
| `execution_error` | compiler 执行失败后进入最终回答合并 | `compiler_execution_contract`、`query_result_contract`、`answer_payload_contract`、`context_update_contract` | validator `valid` → compiler returns error profile → final answer merge | 已进入 compiler，但错误不泄露 request body / stack trace | 是，保存错误摘要，不污染上一轮成功 context | `answer_payload_markdown` |
| `empty_result` | compiler 返回空结果后进入 chart / answer | `query_result_contract`、`chart_payload_contract`、`answer_payload_contract`、`context_update_contract` | validator `valid` → compiler empty result → chart disabled / answer merge | 是 | 是，保存空结果 profile 摘要 | `answer_payload_markdown` |

## 2. Contract 验收

确认以下 contract 已存在且稳定：

- `compact_context_contract`
- `schema_metadata_contract`
- `schema_alias_index_contract`
- `collection_catalog_contract`
- `collection_selection_contract`
- `patch_result_contract`
- `semantic_plan_contract`
- `schema_runtime_context_contract`
- `validator_result_contract`
- `compiler_execution_contract`
- `query_result_contract`
- `chart_request_contract`
- `chart_payload_contract`
- `answer_payload_contract`
- `context_update_contract`

## 3. Compact Context 边界验收

`last_context_json` 只能保存摘要和可恢复状态引用，不保存大对象、敏感调试信息或完整 schema / rows。禁止写入：

- `schema_metadata_json`
- `schema_alias_index_json`
- `schema_runtime_context_json`
- `hydrated_schema_metadata_json`
- `hydrated_schema_alias_index_json`
- `raw_schema_docs`
- `raw_llm_response`
- `full_prompt`
- `pipeline_json`
- `request_body_json`
- `query_response_body`
- `query_response_headers`
- `compiler_debug_json`
- `validator_debug`
- `chart_debug`
- `stage_execution_trace`
- `rows_json` 全量
- `merged_rows_json` 全量
- full rows
- echarts option
- echarts markdown
- `chart_payload.option`
- `chart_payload.echarts_markdown`

## 4. Compiler Gate 验收

- 只有 `validator_route == valid` 才能进入 compiler。
- non-valid validator route 不进入 compiler。
- chart-only 不进入 compiler。
- compiler 主输入来自 `semantic_plan_validator.normalized_plan_json`。
- compiler 不消费未校验 planner 原始 plan。

## 5. Chart Gate 验收

- `ChartPayloadBuilder` 只消费 `chart_request` + `query_result`。
- `ChartPayloadBuilder` 不解析 alias。
- `ChartPayloadBuilder` 不猜业务字段。
- `ChartPayloadBuilder` 不查 Mongo。
- chart-only 不走 schema / planner / compiler。

## 6. Answer / Context 验收

- 所有分支输出 `answer_payload_json`。
- 所有分支输出 `context_update_json`。
- Answer 主路径读取 `answer_payload_markdown`。
- context saver 主路径写 `last_context_json` / `last_answer_payload_json` / `last_context_update_json`。
- non-valid 不覆盖上一轮成功 plan。
- chart-only 不覆盖上一轮 plan。
- execution_error 不污染上一轮成功 context。

## 7. DSL Shard 分片验收

- manifest 中所有 part 存在。
- 所有 part 按 manifest 顺序拼接后 YAML 可解析。
- node id 无重复。
- edge source / target 均存在。
- 单个 part 行数合理；超过 1000 行的 part 必须能追溯到单个大 code node。
- `NL2MGQL_CHATFLOW_5.yml` 不再异常超过 13k 行；Phase 15 后采用 `NL2MGQL_CHATFLOW_00.yml` ... `NL2MGQL_CHATFLOW_21.yml` 的 manifest 顺序分片。
- re-shard 不改变节点 id、节点 title、变量 selector、edge 语义或 code node 逻辑。

## 8. Dify 画布布局验收

- 所有节点有 `position` / `positionAbsolute`。
- 没有完全相同坐标的节点。
- 静态 bbox 检测无普通节点重叠；iteration 父容器包含子节点不计为普通重叠。
- 主路径从左到右。
- 分支按泳道分布。
- validator valid / non-valid / chart-only / hydration 分支清晰。
- iteration 内部节点不重叠。
- legacy / fallback 节点不遮挡主路径。
- Answer 节点位于对应分支末端。

## 9. 旧 Conversation 迁移验收

reader 仍可读取旧字段作为迁移输入：

- `last_normalized_semantic_plan_json`
- `last_chart_request_json`
- `last_result_rows_json`
- `last_result_fields_json`
- `last_final_answer_summary`
- `last_schema_metadata_json`
- `last_schema_alias_index_json`

迁移要求：旧字段仅作为 compat / fallback 输入，不继续写 full schema / full rows；迁移后进入 `compact_context_contract`；legacy fallback 使用时应带 warning / compat 标记，且不破坏新 `last_context_json` 主路径。

## 10. 发布判定

- 阻断项：静态校验失败、mock E2E 失败、compiler 可绕过 validator、chart-only 可绕回 compiler、compact context 写入大对象、edge 悬空、Answer 变量无效、iteration 内 edge 识别字段丢失。
- 非阻断项：缺少真实 Dify / Mongo 环境导致只能静态 smoke；单个 code node 自身体积超过 1000 行；少量 legacy compat 字段仍作为迁移输入。
- 建议：若全部脚本通过且无阻断项，可进入有条件发布；发布前仍建议人工导入 Dify 做一次画布肉眼确认。
