# EJU Learning Core 1.0.0 Beta 9

SQLite 事件与恢复、28个 STDIO MCP 工具、本机工作台、图片完整性与 FSRS 回忆。开始使用见 [包说明](../README.md)，安全边界见 [SECURITY](../SECURITY.md)。Core 不包含大模型，Codex 的处理边界单独适用。

开发验证：Python 3.12–3.14，`uv sync --project core --locked` 后运行 `core/.venv/bin/python -m unittest discover -s core/tests`（Windows 使用 `.venv/Scripts/python.exe`）。原题媒体集成测试要求先按 README 本地导入五份指定官方 PDF；公开包不携带这些图。部分旧证据对照测试还需要 Node.js。

Schema 2；恢复只写新目录，保留原库。每库25000事件，单次JSON12MB，原件100MB，登记附件10000份。达到限制明确报错，没有自动清理旧进度。可选 Docling 不随公开包提供；未配置时原件保留并报告不可用。

## Beta4 连续练习接口

`read_study_review` 使用 attempt 的数据库序号倒序分页，`before=0` 表示最新；返回 `next_before` 与 `has_more`，并分开给出本人复盘、理由核对和失效原因。`resume_learning.recent_reviews` 复用同一有界视图。

`get_next_practice(session_id, subject, basis_attempt_id=None)` 只读列出完整未见官方候选，`shared_nodes` 表示源题知识点相关，`same_subject` 仅同科。只有认证 HTTP 的 `start_next_practice` 能在本人明确点击后创建新任务；它校验修订号、当前任务、来源复盘和候选版本，保护 active/paused，重试沿同一 request_id 回放。新建任务不记曝光；原图实际显示后另记 show。MCP 暴露两只读接口，不暴露浏览器 start 或 reflect。

## Beta5 持久讲解与复习

MCP新增只读 read_review_queue/read_coaching_context 与写入 save_coaching_note。认证 HTTP 提供两只读及 start_review_practice；浏览器不能写辅导摘要。Coaching note 为追加事件，不新增数据库 schema，不参与旧证据或 FSRS 推导。参数和语义见[讲解协议](../eju-study-coach-codex/references/coaching-review.md)。

## Beta6 官方题扩充与共用页证据

新增13道完整原题，总计45道；35份日语／综合答后指引。`exposure.py`按官方来源、页码和图片对照共用页，候选、显式下一题与证据派生共用该判定，不改写旧事件。完整导入来源与具体范围见上级README。

## Beta 8 内容扩充

完整官方题增至60道，日语24、综合26、物理10；50份日语／综合答后指引与重点分类。旧100条目录、35份指引及35项分类逐值保留。官方导入新增2010综合来源；2010日语同一来源新增完整文章。重点元数据版本随新增内容变化，已提交请求重放及旧暂停任务保留原语义。

## Beta 9 理由反馈

`save_coaching_note` 可选保存 `reasoning_assessment` 以及经审核的来源题训练重点建议。引用逐字匹配原作答，判断对应当前 tutor 理由核对；澄清状态不自动写入分歧核对或推荐。旧请求摘要保持兼容。工作台采用建议前重新读取依据，采用只查询候选；开始原题仍单独确认。完整字段和教学顺序见随包 Skill 的 reasoning-feedback.md。
