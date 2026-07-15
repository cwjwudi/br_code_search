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

## v0.4.3 — 已完成：工程环境过滤

- 从 `.apj` 和 `.pkg` 提取 AS、Automation Runtime、CPU ModuleId 和技术包版本；
- `search`、`similar`、`find_symbol` 支持 AS/AR/CPU/库版本过滤；
- 默认检索排序优先 `gold`、已验证项目和用户工程代码。

## v0.4.4 — 已完成：同文件聚合

- `br_search_code`/CLI 支持可选的同文件聚合；
- 聚合结果保留文件主单元、全部符号摘要、文档 ID 和受限源码单元；
- 默认仍返回符号级结果，保证已有 MCP 客户端兼容。

## v0.4.5 — 当前版本：Task 目标关联

- 每条 `.sw` Task 关联最近的 `Cpu.pkg`；
- 返回精确的 CPU ModuleId、Automation Runtime 版本和配置包路径；
- `get_task_configuration`/CLI `tasks` 支持 CPU 与 AR 过滤；
- 旧索引自动回填 Task 目标元数据，后续同步不重复扫描未变化的 `.sw`。

后续 v0.4.x 小版本继续完善：

- 多文件结果的去重、同文件聚合默认策略和质量标签评测；
- 多目标工程的 CPU 配置选择和质量评测集。

## v0.4.6 — 当前版本：多目标感知检索

- 文档单元保存目标 CPU ModuleId、Automation Runtime 版本和配置包路径。
- 通过 `.sw` Task 将逻辑程序关联到具体物理目标；共享逻辑单元保留合理回退。
- `search`、`similar`、`find_symbol` 的 CPU/AR 过滤改为目标感知，并在结果中返回目标元数据。
- 新旧索引自动迁移并回填文档目标元数据。

## v0.4.7 — 当前版本：检索质量评测

- 提供版本化 JSON 评测集，支持 `search`、`similar` 和 `find_symbol`。
- 统一输出 Hit@1/3/5/10、首个相关结果排名和 MRR。
- CLI 与 MCP 共用同一评测实现，避免“测试路径”和实际 AI 调用路径分叉。
- 保存不含源代码的路径/符号标签，作为后续语义检索混合排序的基线。

## v0.5.0 — 已完成：可插拔混合检索

- 以可插拔后端接入本地 embedding；默认标准库 hashing 后端离线可运行，`sentence_transformers` 后端支持显式指定本地模型。
- 将语义、精确词法和 B&R 结构信号统一为可解释的混合排序，并返回各分项分数。
- 向量按文档内容 hash 和后端 key 缓存到 SQLite，增量同步后只重新编码变化文档。
- CLI/MCP 增加 `hybrid`/`br_search_hybrid`，保留原有精确和 lexical_structural 工具兼容性。
- CLI/MCP 提供 embedding runtime 健康检查，不会因为检查可用性而下载或加载模型。
- 评测集可直接运行 hybrid case，为后续 Qdrant/更大模型后端保留扩展点。

## v0.5.1 — 当前版本：Embedding 运行时诊断

- CLI/MCP 可检查 hashing、SentenceTransformers 和自动选择后端的可用性，不会因为健康检查下载或加载模型。
- 混合检索默认覆盖完整索引（最多 50,000 个文档），并明确返回离线 fallback、训练模型或自定义后端类型。
- 允许注册自定义 embedding 工厂，为后续本地模型和 Qdrant 适配保留稳定扩展点。

## v0.6.0 — 当前版本：外部验证反馈闭环

- 新增 `record-validation`/`br_record_project_validation`，在工具自己的元数据目录接收构建、现场和版本兼容性结果。
- 检索结果和项目概览返回最近验证记录；混合/相似检索对通过或失败的验证施加小幅可解释排序修正。
- 验证记录不写入参考工程，增量同步会保留并重新关联这些记录。

## v0.7.0 — 当前版本：统一工程分析工具集

- 补齐库使用、工程架构、FUNCTION_BLOCK 相似实现和实现对比工具。
- 编译验证记录支持错误/警告明细，并提供项目级历史与跨项目错误检索。
- 索引扩展到 C++、Python、JSON/YAML/XML；未知方言仍保留文件级可检索回退。
- MCP/CLI 共享同一实现，保持 AI 查询路径和本地诊断路径一致。

## v0.8.0 — 当前版本：Qdrant 外部向量索引适配

- 提供可选 `qdrant-client` 依赖、运行时健康检查和 CLI/MCP 导出接口。
- 将 SQLite 缓存的向量及项目、目标 CPU、AR、语言、符号等元数据批量写入本地或远程 Qdrant。
- 保留 SQLite 作为权威源文本和离线 fallback；Qdrant 不会修改参考工程，也不强制成为运行依赖。
- 已在真实 31,742 文档库完成本地 collection 导出与点数核对；大规模部署建议使用 Qdrant 服务而非本地模式。

## v0.9.0 — 当前版本：工具链报告适配器

- 新增 `br_get_toolchain_status`/CLI `toolchain-status`，只读检查
  `br_device_autodev` 的上下文文档、目标配置和报告目录；
- 新增 `br_import_toolchain_report`/CLI `import-toolchain-report`，解析注册的
  `br-plc-toolchain` MCP JSON/JSON-RPC 报告，并自动写入项目编译历史、版本、
  CPU、RUC 包路径、错误和警告；
- 明确执行边界：代码检索工具不启动 Automation Studio、PVITransfer，不下载、
  不写 PVI/OPC UA；实际 PLC 操作继续由独立受控工具链负责；
- 导入结果继续参与项目质量/验证排序，且保留原始报告路径以便审计。

## v0.10.0 — 当前版本：统一构建诊断摘要

- 导入记录保留报告 schema、事件/操作 ID、目标/config、日志路径和后续动作；
- `br_get_compile_history` 支持按目标和工具过滤；
- 新增 `br_get_build_diagnostic_summary`/CLI `build-diagnostic-summary`，跨项目聚合重复错误、警告和状态；
- 诊断接口只返回摘要，不把完整 PLC 日志复制进 MCP 响应。

## v0.11.0 — 当前版本：Qdrant 语义查询闭环

- 新增 `br_search_qdrant`/CLI `qdrant-search`，查询本地或远程 Qdrant 集合；
- Qdrant 只返回向量和元数据，源代码、验证记录和质量字段从 SQLite 权威索引回填；
- 支持项目、来源、语言、质量、已验证和废弃过滤，并返回可解释的 Qdrant 分数；
- 保持 Qdrant 可选依赖和 SQLite 离线 fallback，未改变 PLC 工具链权限。

## v0.12.0 — 当前版本：Git provenance 与符号影响摘要

- 新增 `br_get_source_provenance`/CLI `source-provenance`，明确报告源代码是否处于 Git 工作树、revision、分支和 dirty 状态；
- 新增 `br_get_symbol_impact`/CLI `impact`，聚合 B&R 符号的跨文件/项目引用、读写/调用方向、调用者和 CPU/AR 目标覆盖；
- 对没有 Git 元数据的当前 `code_base` 显式标记为 path/time provenance，不伪造 revision；
- 影响分析保持索引级只读摘要，不冒充编译器数据流或 Safety 分析。

## v0.13.0 — 当前版本：质量与 Task 感知的符号影响摘要

- `br_get_symbol_impact`/CLI `impact` 现在返回项目质量/验证标注、受影响 Task、质量计数和被标记为废弃/禁止复制的引用数量；
- 风险分级会把写入、调用、跨项目传播、Task 绑定和受阻质量标注纳入摘要；
- Task 关联复用已索引的 `.sw` 元数据，不扫描或修改参考工程，也不冒充编译器数据流分析。

## v0.14.0 — 当前版本：known_issue 防复用标注

- `annotate-project`/`br_annotate_project` 新增 `known_issue` 标注；
- `known_issue=true` 持久化在工具侧元数据，并自动强制 `do_not_copy=true`，默认检索不会把已知问题代码当作可复制参考；
- 项目概览和符号影响摘要显式返回 `known_issue`，保留与现有质量、验证和废弃标注的兼容性。

## v0.15+ — 外部索引与工具链闭环

- 接收外部构建结果和人工验证结果；
- 以成功构建、现场验证和版本兼容性参与排序；
- 在不扩大代码检索权限的前提下，对接更多只读 B&R 诊断能力。

## 迭代原则

1. 每次迭代必须有真实样例库或固定夹具测试。
2. MCP 工具参数和返回结构尽量向后兼容。
3. 解析失败要显式报告，不能静默丢弃源代码。
4. 不因增加语义能力而牺牲精确符号检索。
5. 参考代码索引和 PLC 控制权限保持隔离。
