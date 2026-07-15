# B&R Code Search 迭代路线

目标是让 AI 能从经过筛选的 B&R Automation Studio 工程中读取实现风格和工程上下文，并为后续代码修改提供可追溯参考。每个版本先保留稳定的只读 MCP 契约，再逐步提高解析和检索质量。

## v0.1 — 已完成

- 独立 stdio MCP Server；
- ST/FUN/VAR/TYP/C/H/APJ/PKG 基础解析；
- SQLite/FTS5 索引；
- 项目、文件、来源、符号和行号元数据；
- 程序上下文和项目概览；
- 真实三工程样例库索引与 MCP 调用验收。

## v0.2 — 已完成

- 增量同步：只更新新增、修改和删除的文件；
- 记录源文件 hash、大小、修改时间和编码；
- 增加基于标识符和控制结构的轻量相似代码检索；
- 保留“这不是向量语义搜索”的明确结果标注；
- CLI 和 MCP 都支持同步/相似检索。

## v0.3 — 已完成：检索质量

- 建立真实查询评测集，记录 Top-K 命中情况；
- 支持工具侧项目标记 `gold / normal / deprecated / do_not_copy`；
- 支持 `verified_only`、质量和废弃项目过滤；
- 将质量与验证状态返回到每个检索结果和项目概览；
- 以独立的 `var/project_metadata.json` 保存标记，不修改参考工程；
- 完成 SQLite 旧索引的自动列迁移。

## v0.4 — 已完成：B&R 结构理解

- 解析 `.sw` 中的 TaskClass/Task 与源程序归属，并保留显式周期属性；
- 用 Task 关联补充程序上下文，同时保留目录邻居以保证向后兼容；
- 增加 `find_references`、`get_type_definition`、`get_task_configuration` MCP 工具；
- 提供 CLI 的 `tasks`、`type`、`references` 命令；
- 从 `.var` 和 ST VAR 区块提取变量声明、类型表达式和实际行号；
- 在程序上下文中解析到同工程或库中的 `TYPE`/`FUNCTION_BLOCK` 定义，并标注引用/声明。

## v0.4.1 — 已完成：变量类型上下文

- `br_get_program_context` 返回去重后的变量声明与类型引用；
- 类型引用优先匹配当前工程，再补充库和其他参考工程定义；
- `br_find_references` 返回 `declaration/use`、声明类型和精确行号；
- MCP stdio 在 Windows 默认代码页下强制使用 UTF-8，避免中文 B&R 源码导致协议中断。

## v0.4.2 — 已完成：变量访问方向

- `br_find_references` 区分 `read`、`write`、`call`、`member` 和 `comment`；
- 保留 `declaration/use` 关系、声明类型和精确行号，并去除同文件重复行。

## v0.4.3 — 当前版本：工程环境过滤

- 从 `.apj` 和 `.pkg` 提取 AS、Automation Runtime、CPU ModuleId 和技术包版本；
- `search`、`similar`、`find_symbol` 支持 AS/AR/CPU/库版本过滤；
- 默认检索排序优先 `gold`、已验证项目和用户工程代码。

后续 v0.4.x 小版本继续完善：

- 结果去重、同文件聚合和质量标签排序；
- AS、AR、CPU 和库版本过滤。

## v0.5 — 语义检索

- 以可插拔后端接入本地 embedding；
- 结合精确、结构和语义结果进行混合排序；
- 支持按项目、机型、AS/AR、语言和可信等级过滤；
- 在不改变 MCP 接口的前提下，可选接入 Qdrant。

## v0.6+ — 验证闭环

- 接收外部构建结果和人工验证结果；
- 以成功构建、现场验证和版本兼容性参与排序；
- 与 B&R Automation Studio 工具链建立只读诊断反馈接口；
- 保持参考库只读，代码修改和 PLC 下载继续由其他受控工具负责。

## 迭代原则

1. 每次迭代必须有真实样例库或固定夹具测试。
2. MCP 工具参数和返回结构尽量向后兼容。
3. 解析失败要显式报告，不能静默丢弃源代码。
4. 不因增加语义能力而牺牲精确符号检索。
5. 参考代码索引和 PLC 控制权限保持隔离。
