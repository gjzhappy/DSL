# 完整 C Phase 15 Gold Cases

本文件定义发布前 gold cases。目标是覆盖 new query、refine full replan、use_patch、chart_only、validator non-valid、empty result 与 execution error，不新增能力，只验收完整 C 主链路 contract 与 gate。

## Case 1：普通查询 + 图表

**用户**：

```text
过去一个月所有订单, 按厂商进行分组统计,做一个柱状图进行对比
```

**期望路径与断言**：

```text
new_query
primary_collection=orders
group_fields=["brand"]
metric_alias=order_count
chart_type=bar
answer_type=query_with_chart
保存 compact context
```

## Case 2：结构性 refine 换 group

**用户**：

```text
不对，我想按订单状态统计过去两个月的订单数，不再按品牌统计, 生成新的柱状图
```

**期望路径与断言**：

```text
patch_route=full_replan
group_fields=["status"]
time_range.relative_days=60
不残留 brand
answer_type=query_with_chart
```

## Case 3：time-only patch

**用户**：

```text
改成过去两个月
```

**期望路径与断言**：

```text
patch_route=use_patch
group_fields 不变
time_range.relative_days=60
validator valid
compiler 执行
```

## Case 4：chart-only

**用户**：

```text
换成饼图
```

**期望路径与断言**：

```text
patch_route=chart_only
不进 schema retrieval
不进 planner
不进 compiler
ChartPayloadBuilder
answer_type=chart_only
不覆盖 last_plan_summary
```

## Case 5：unknown field clarification

**用户**：

```text
按客户星级统计订单数
```

**期望路径与断言**：

```text
validator_route=requires_clarification
不进 compiler
answer_type=clarification
```

## Case 6：PII blocked

**用户**：

```text
按用户手机号统计订单数
```

**期望路径与断言**：

```text
validator_route=blocked
不进 compiler
answer_type=validation_error
```

## Case 7：filter refine

**用户**：

```text
只看已支付订单
```

**期望路径与断言**：

```text
patch_route=full_replan
filters canonicalize value_alias
validator valid 或 requires_clarification
```

## Case 8：metric refine

**用户**：

```text
不看订单数了，改成销售额
```

**期望路径与断言**：

```text
patch_route=full_replan
metric_alias=gmv_sum
metric_function=sum
metric_field=amount
chart y_field=gmv_sum
```

## Case 9：empty result

**触发方式**：使用 mock / staging 中确定返回 0 行的过滤条件。

**期望路径与断言**：

```text
answer_type=empty_result
chart disabled
保存空 result profile
```

## Case 10：execution error

**触发方式**：使用 mock / staging 中可控的 Mongo executor 错误响应。

**期望路径与断言**：

```text
answer_type=execution_error
不暴露 request body / stack trace
不覆盖上一轮成功 context
```

## Gold Case 覆盖矩阵

| 能力 / 风险 | 覆盖 Case |
|---|---|
| new_query 主路径 | Case 1 |
| refine full_replan | Case 2、7、8 |
| use_patch | Case 3 |
| chart_only | Case 4 |
| validator clarification | Case 5、7 |
| validator blocked | Case 6 |
| empty result | Case 9 |
| execution error | Case 10 |
| compact context 保存 | Case 1、4、9、10 |
| compiler gate | Case 3、5、6 |
| chart gate | Case 1、4、9 |
