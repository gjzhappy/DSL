# 完整 C Phase 15 最终验收与发布报告

## DSL shard analysis

### part line counts（re-shard 前）

- `NL2MGQL_CHATFLOW_0.yml`: 900
- `NL2MGQL_CHATFLOW_1.yml`: 900
- `NL2MGQL_CHATFLOW_2.yml`: 900
- `NL2MGQL_CHATFLOW_3.yml`: 900
- `NL2MGQL_CHATFLOW_4.yml`: 900
- `NL2MGQL_CHATFLOW_5.yml`: 13333

### top large nodes（re-shard 前后节点内容未拆分）

1. `1775000000007` / `代码执行_遍历语义计划并统一编译执行`: 2595 lines
2. `1775000000022` / `代码执行_合并最终回答`: 1570 lines
3. `1776000000024` / `代码执行_统一准备Mongo执行输入`: 1177 lines
4. `1776000000026` / `代码执行_汇总Refine Schema上下文`: 1018 lines
5. `1778000000001` / `代码执行_semantic_plan_validator`: 767 lines
6. `1776000000027` / `代码执行_合并Refine Schema上下文并准备重规划提示词`: 701 lines
7. `1776000000019` / `代码执行_尝试确定性查询修正`: 669 lines
8. `1775000000013` / `代码执行_提取语义计划结果`: 516 lines
9. `1776000000001` / `代码执行_读取并规范化会话状态`: 491 lines
10. `1779000000001` / `代码执行_准备Validator运行时Schema上下文`: 385 lines

### duplicate node ids

- 未发现重复 node id。

### duplicate titles

存在语义上合理的重复 title：

- `代码执行_CollectionCatalog候选选择`: new_query 与 refine 各一处。
- 空 title: iteration start 节点。
- `代码执行_解析当前任务`: schema retrieval iteration 与 refine iteration 各一处。
- `知识检索_当前CollectionSchema`: schema retrieval iteration 与 refine iteration 各一处。
- `代码执行_打包Schema结果`: schema retrieval iteration 与 refine iteration 各一处。
- `代码执行_ChartPayloadBuilder`: normal query chart builder 与 chart-only chart builder 各一处。

### duplicate large code blocks

- 仅发现 1 组完全重复 code block：`1775000000004` 与 `1776000000035` 均为 `代码执行_打包Schema结果`。该重复属于 new_query/refine 两个 iteration 内部的相同打包逻辑；Phase 15 未删除，以避免改变 iteration 父子关系与主链路。

### legacy candidates

- 仍保留旧 conversation 字段读取与 compat / fallback 标记，用于迁移旧会话。
- legacy / fallback 不作为主路径；静态校验继续检查 compiler 输入必须来自 validator valid。
- 未删除 legacy 节点；删除风险高于收益，Phase 15 仅做发布前收口。

### re-shard recommendation / result

13k 行来源主要是正常 code node 膨胀与长期未 re-shard，而不是重复 node id 或可安全删除节点。Phase 15 已执行无语义 re-shard，`NL2MGQL_CHATFLOW_5.yml` 不再存在为 13k 行异常分片；manifest 改为 `NL2MGQL_CHATFLOW_00.yml` 到 `NL2MGQL_CHATFLOW_21.yml` 顺序拼接。

超过 1000 行的 part 均由单个大 code node 导致，未拆分单个 code node：

- `NL2MGQL_CHATFLOW_04.yml`: 2595 lines，单节点 `1775000000007`。
- `NL2MGQL_CHATFLOW_07.yml`: 1570 lines，单节点 `1775000000022`。
- `NL2MGQL_CHATFLOW_14.yml`: 1177 lines，单节点 `1776000000024`。
- `NL2MGQL_CHATFLOW_18.yml`: 1018 lines，单节点 `1776000000026`。

## re-shard 后语义等价校验

- node ids unchanged: true
- edge id/source/sourceHandle/target/targetHandle unchanged: true
- code node content checksums unchanged: true
- node count: 76
- edge count: 84

## re-shard 后 part line counts

- `NL2MGQL_CHATFLOW_00.yml`: 950
- `NL2MGQL_CHATFLOW_01.yml`: 322
- `NL2MGQL_CHATFLOW_02.yml`: 897
- `NL2MGQL_CHATFLOW_03.yml`: 297
- `NL2MGQL_CHATFLOW_04.yml`: 2595
- `NL2MGQL_CHATFLOW_05.yml`: 526
- `NL2MGQL_CHATFLOW_06.yml`: 902
- `NL2MGQL_CHATFLOW_07.yml`: 1570
- `NL2MGQL_CHATFLOW_08.yml`: 464
- `NL2MGQL_CHATFLOW_09.yml`: 924
- `NL2MGQL_CHATFLOW_10.yml`: 839
- `NL2MGQL_CHATFLOW_11.yml`: 403
- `NL2MGQL_CHATFLOW_12.yml`: 947
- `NL2MGQL_CHATFLOW_13.yml`: 43
- `NL2MGQL_CHATFLOW_14.yml`: 1177
- `NL2MGQL_CHATFLOW_15.yml`: 385
- `NL2MGQL_CHATFLOW_16.yml`: 830
- `NL2MGQL_CHATFLOW_17.yml`: 244
- `NL2MGQL_CHATFLOW_18.yml`: 1018
- `NL2MGQL_CHATFLOW_19.yml`: 701
- `NL2MGQL_CHATFLOW_20.yml`: 938
- `NL2MGQL_CHATFLOW_21.yml`: 861

## DSL layout analysis

### layout 前

- total nodes: 76
- nodes missing position: 0
- duplicate coordinates: 2
- approximate overlap count: 29
- iteration internal overlap count: 6

### layout 后

- total nodes: 76
- nodes missing position: 0
- duplicate coordinates: 0
- approximate overlap count: 0（iteration 父容器包含子节点不计为普通重叠）
- iteration internal overlap count: 0

### main lane summary

- Lane 0: Start / compact context reader / turn intent 自左向右排列。
- Lane 2: new_query 主路径与 schema retrieval iteration 放在上方泳道。
- Lane 3: refine full_replan 路径放在中部泳道。
- Lane 4: use_patch 汇入 runtime schema / validator 前置节点。
- Lane 5: hydration 分支下沉，避免遮挡 validator 主链路。
- Lane 6: validator valid → compiler → chart → answer 主成功路径自左向右。
- Lane 7: chart_only 独立泳道，不回到 schema / planner / compiler。
- Lane 8: non-valid / clarification / analysis 分支下沉或右侧收口。

### remaining layout warnings

- 静态检测无 duplicate coordinate、无普通节点 overlap、无 iteration internal overlap。
- 真实 Dify 画布仍建议人工导入后确认一次文字宽度、连线绕行和缩放视口显示。

## Dify / Mongo smoke

- 未执行真实 Dify 导入/预览，原因：当前仓库环境未提供 Dify 服务地址、访问 token 或导入 CLI。
- 已通过 manifest 拼接、YAML 解析、变量 selector / Answer 引用静态校验、layout 静态检测和 mock E2E 替代。
- 未执行真实 Mongo / staging smoke，原因：当前仓库环境未提供 staging Mongo executor endpoint、凭证或测试数据集。
- 发布前建议在目标 Dify 环境人工导入一次，并用 gold cases 做真实 smoke。

## 主链路最终确认

- `new_query`: compact context → collection selector → schema retrieval → planner → normalizer → runtime schema / hydration → validator → compiler → chart → answer/context → Answer。
- `refine full_replan`: compact context → deterministic patch → full_replan → refine schema / planner → validator → compiler → chart → answer/context → Answer。
- `use_patch`: compact context → deterministic patch → runtime schema / hydration → validator → compiler → chart → answer/context → Answer。
- `chart_only`: compact context → deterministic patch / chart plan → ChartPayloadBuilder → answer/context → Answer，不进入 schema / planner / compiler。
- `non-valid`: validator route 非 `valid` → non-valid answer/context → Answer，不进入 compiler，不覆盖上一轮成功 plan。

## 最终风险清单

### 阻断发布

- 当前静态校验与 mock E2E 未发现阻断发布问题。

### 非阻断风险

- 仍有 4 个 shard 超过 1000 行，均因单个 code node 自身体积超过 1000 行；Phase 15 未拆单个 code node 以避免语义风险。
- 未执行真实 Dify 导入/画布预览；需要发布前人工导入目标 Dify 环境确认。
- 未执行真实 Mongo / staging smoke；需要在具备凭证与测试数据后补做。
- schema hydration retrieval 依赖知识库 schema 文档质量。
- validator 规则仍需随 schema contract 演进持续增强。
- chart builder 支持的 chart type 有边界，复杂可视化仍需后续扩展。

### 后续优化

- 可在后续维护中把 DSL 内嵌 Python 抽取到共享脚本生成源，减少单节点行数。
- 可逐步降低旧变量名带来的阅读误导，但不应破坏 legacy conversation 兼容。
- 可在真实 Dify 中按业务使用习惯微调连线绕行与 viewport。

## 发布建议

有条件发布：当前静态校验、layout 检测与 mock E2E 通过；发布条件是目标环境完成一次真实 Dify 导入/预览 smoke，并在有 staging 凭证时补做 Mongo smoke。
