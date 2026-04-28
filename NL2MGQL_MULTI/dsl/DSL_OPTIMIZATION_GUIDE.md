# NL2MGQL_MULTI / NL2MGQL_MULTI2 优化建议（按最终链路）

> 目标链路：
>
> LLM 原始 DSL → 规范化执行计划 → stage → validated_ast → 编译前 sanitize → pipeline generator → Mongo query

## 1) 规范化层（Normalization）

在 `代码执行_语义计划到Stage执行` 节点（`NL2MGQL_MULTI.yml` 内当前大段 Python）增加“**可追踪、可解释**”的规范化输出：

- 对 `time_range` 做最小可信清洗：
  - `mode=relative_days` 时忽略 `start/end`。
  - `mode=absolute` 时仅保留可解析时间，非法值置空。
  - `mode` 非法一律回退 `none`。
- `filters` 去重：
  - 按 `(field, operator, value)` 去重。
  - 当 `time_range.mode != none` 时，移除 filters 中对同一时间字段的重复时间约束。
- 输出 `normalization_notes`：
  - 每个 stage 记录清洗了哪些字段、去掉了哪些重复过滤。

推荐新增结构：

```json
{
  "stage_name": "stage1",
  "time_range_sanitized": true,
  "dropped_filter_count": 2,
  "dropped_filter_indexes": [1, 4],
  "notes": ["remove duplicated time filter on created_at"]
}
```

## 2) 编译前强制 Sanitize（不要信任 LLM start/end）

在 `_compile_stage(...)` 入口统一做 `sanitize_stage_for_compile(stage)`：

- 再次规范化 `time_range`（不要直接复用 LLM 原文）。
- 运行时重新计算 `relative_days` 起点：
  - 仅使用 `now - timedelta(days=N)`。
  - 不使用 LLM 给的 `start/end` 作为相对时间依据。
- 再次过滤掉与 `time_range` 重复的 filter（双保险）。

这样即使 LLM 在 normalization 之后又“污染”字段，也不会进入 pipeline。

## 3) validated_ast 与 pipeline 的一致性

确保 `validated_ast.time_range` 与实际 `$match` 逻辑一致：

- `validated_ast.time_range` 应该保存 **sanitize 后** 的结果。
- `$match` 仅由 sanitize 后的 `time_range + filters` 生成。
- 不再把原始 semantic_plan 的时间字段透传到 `validated_ast`。

## 4) Trace 可读性增强（建议）

在 `stage_execution_trace` 增加以下字段：

- `normalized_stage`
- `sanitize_report`
- `effective_match_expr`
- `dropped_filters`

示例：

```json
{
  "stage_name": "stage1",
  "normalized_stage": {...},
  "sanitize_report": {
    "dropped_filters": [{"field":"created_at","operator":"gte"}],
    "time_range_recomputed": true
  },
  "effective_match_expr": {...}
}
```

## 5) 与你当前两文件的落点

- `NL2MGQL_MULTI.yml`
  - 已有 `_normalize_time_range/_apply_time_range/_normalize_plan/_build_stage_match/_compile_stage`，建议把 sanitize 能力放在这些函数周围，优先在 `_compile_stage` 入口做总闸。
- `NL2MGQL_MULTI2.yml`
  - 已有 semantic_plan schema 清洗与解析逻辑，可补充对 `time_range` 与 `filters` 的提示约束，减少脏 DSL 进入下一层。

## 6) 最小改动策略（先稳后快）

建议分两步上线：

1. **第一步（低风险）**：只加 sanitize + trace，不改业务意图判定。
2. **第二步（提质）**：再优化 prompt/schema，减少重复过滤与无效 absolute 时间。

---

如果你愿意，我下一步可以直接给你一版“可粘贴到 `代码执行_语义计划到Stage执行` 节点”的 Python 函数补丁（`_sanitize_time_range/_dedup_filters/_sanitize_stage_for_compile` 三件套），用于一次性落地。
