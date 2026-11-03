# SI 设计约束与 JF 电容对比 Dify Chatflow

## 1. 独立工作流

本目录新增两个独立 Dify Advanced Chatflow：

- SI_DESIGN_CONSTRAINT_CHATFLOW
- JF_CAPACITOR_CHATFLOW

两者复用现有 PHONE_MODULE_JF_CHATFLOW 的 Registry 驱动查询骨架，但各自拥有独立的 Registry、Prompt、查询记忆和 DSL 分片。原 PHONE/JF 工作流不修改。

## 2. 环境变量

### SI 设计约束

- SI_DESIGN_CONSTRAINT_QUERY_REGISTRY_JSON
- SI_DESIGN_CONSTRAINT_ANALYSIS_SYSTEM_PROMPT
- SI_DESIGN_CONSTRAINT_ANALYSIS_USER_PROMPT

目标表：sn_si_design_constraints_wide

业务字段：manufacturer, platform, chip, module, level, insertion_loss, return_loss, sdc, crosstalk, isolation, parasitic_constraint, remark。

### JF 电容对比

- JF_CAPACITOR_QUERY_REGISTRY_JSON
- JF_CAPACITOR_ANALYSIS_SYSTEM_PROMPT
- JF_CAPACITOR_ANALYSIS_USER_PROMPT

目标表：sn_pisi_jf_capacitor_comparisons_wide

业务字段：manufacturer, platform, power_domain, pin_net_name, inductor, near_capacitor, far_capacitor, far_total_capacitance, near_cap_count, far_cap_count, far_0402_regular, far_0402_three_terminal, far_0201_regular, far_cap_area_mm2, total_cap_area_mm2, remark。

两个 DSL 继续共用以下基础设施环境变量，不要复制成业务专属变量：

- ENABLE_FULL_LLM
- SN_FULL_LLM_CHAT_URLS
- SN_MYSQL_QUERY_URLS
- LOCAL_CONTEXT_TOKENS
- LOCAL_MAX_PREDICT_TOKENS
- PROMPT_TOKEN_SAFETY_MARGIN

## 3. 查询语义和 Registry 边界

当前默认 Registry 已配置迁移结果中确认的 manufacturer/platform 标准值别名。SI 当前确认的身份值为 Huawei/Changsha；JF 电容对比的 manufacturer/platform 值来自本轮迁移的 12 个已审计平台。

chip, module, level, power_domain, pin_net_name, inductor 已作为查询上下文字段纳入 SELECT。由于当前资料没有提供这些维度的完整 DISTINCT 值清单，它们暂不伪造 value_aliases；LLM 必须在返回 rows 中按字段值执行细粒度筛选。需要把这些维度也下推 SQL WHERE 时，应先从目标 MySQL 表取得真实 DISTINCT 值，再扩充各自 Registry 的 value_aliases。不得凭测试样例猜测生产值。

Registry 字段清单根据现有 SI / JF 电容导入数据结构建立。导入 Dify 前仍应以目标 MySQL SHOW CREATE TABLE 或 SHOW COLUMNS 结果复核列名；技术列 id, record_key, insert_time, update_time 不作为用户业务查询字段。

## 4. 业务回答边界

### SI 设计约束

- 保留 chip → module → constraints 的层级语义和每条约束明细。
- 不合并不同 level，不推断单位、阈值或 PASS/FAIL。
- 源值字符串 NA 是业务原始值，不得擅自变成 NULL 或 0。
- 对比必须说明芯片、模块、级别是否一致。

### JF 电容对比

- 严格区分 power_domain、pin_net_name、near_capacitor、far_capacitor。
- 不混淆容值、数量和面积；面积字段单位为 mm²，其他单位以源数据为准。
- 不擅自判断容值越大、数量越多或面积越小就一定更好。
- 不对不同电源域或不同 Pin/Net 记录强行配对。

## 5. 本地分片合并与验证

在仓库根目录执行：

    python NL2SEARCH_CHATFLOW_DSL/merge_business_chatflow.py ALL --write
    python scripts/dify_dsl_schema_check.py NL2SEARCH_CHATFLOW_DSL/SI_DESIGN_CONSTRAINT_CHATFLOW.yml
    python scripts/dify_dsl_schema_check.py NL2SEARCH_CHATFLOW_DSL/JF_CAPACITOR_CHATFLOW.yml
    python scripts/business_query_chatflow_regression.py ALL
    ruby scripts/dsl_self_check.rb NL2SEARCH_CHATFLOW_DSL/SI_DESIGN_CONSTRAINT_CHATFLOW.yml
    ruby scripts/dsl_self_check.rb NL2SEARCH_CHATFLOW_DSL/JF_CAPACITOR_CHATFLOW.yml

00、01、02 分片是 Git 源文件；合并生成的 *_CHATFLOW.yml 是本地 Dify 导入产物，已加入 .gitignore，不提交。

## 6. 外部依赖

两个 DSL 均调用现有 SN_MYSQL_QUERY_URLS 参数化只读查询接口。该仓库不包含 Spring Boot 查询服务源码，因此本次只能验证 DSL 生成的 SQL、参数和 HTTP 契约；后端若存在表名/字段白名单，需在后端另行确认是否允许上述两张表。
