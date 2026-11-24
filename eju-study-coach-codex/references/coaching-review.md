# 持久讲解与旧题复习

`read_coaching_context(session_id, attempt_id)` 返回 context.attempt、coaching_note、previous_attempt_id、previous_coaching_note、base_note_id、basis_reflection_id、basis_reason_review_id、base_task_id 与 review_available。列表和 context 都是只读。完整原始长内容仍按 read_record_detail 分页。

`save_coaching_note(session_id, expected_revision, request_id, attempt_id, summary, next_step, open_question="", hypothesis="", base_note_id=null, basis_reflection_id=null, basis_reason_review_id=null, reasoning_assessment=null, practice_focus_id=null, practice_focus_version=null)` 仅通过 MCP 保存。summary 必填最多2000字符，next_step 必填最多1200；open_question 最多1200、hypothesis 最多1000。服务写日期、来源作答/题目/版本与上一说明 ID；每次新增不可变记录。不要把未核实推断写成事实。三个新增参数都可省略，旧的自由文字讲解继续有效；不要为补齐新结构而改写旧记录。

`reasoning_assessment` 只含 `status`、`reason_quote`、`error_stage` 三个字段；`open_question` 仍是外层参数。引文最多500字符，必须逐字取自完整原始 `attempt.reason`，不能从本人复盘、题目、工具截短摘要或教师改写中拼接。需要时用已有详情分页读取完整记录，不能把省略号当原文。结构化说明和实际 `reason_review` 是两次独立操作，保存讲解不会生成理由审核。

| status | 当前审核前提 | error_stage | 教学含义 |
| --- | --- | --- | --- |
| `supported` | 当前有效的 tutor 审核且 `agrees=true` | `none` | 这段理由已核对；不等于选项必定正确或已掌握 |
| `needs_revision` | 当前有效的 tutor 审核且 `agrees=false` | `question_reading`、`knowledge`、`method`、`calculation`、`graph`、`unknown` 中一个 | 有具体可指认的问题；阶段是教师对本次理由的判断 |
| `needs_clarification` | 当前没有 tutor 理由审核 | `unknown` | 证据不足；外层 `open_question` 必须有一个聚焦问题，不得附练习建议 |

前两种状态必须有非空原理由引文。待澄清时，原理由为空或仅空白，`reason_quote` 必须为空；原理由非空，则仍取非空逐字短引文。已有 tutor 审核而新出现的信息不足时，先核对当前事实和审核依据，不能为了通过校验伪造撤回、不认可审核或澄清状态。self 审核不满足 tutor 前提。

`practice_focus_id` 与 `practice_focus_version` 必须同时提供或同时省略。只有 `supported` / `needs_revision`、当前最新有效作答、完整可用材料及当前 tutor 审核同时成立时才考虑建议；ID与版本来自重新读取的 `context.practice_focus_options` / `practice_focus_version`，且来源题确实在当前已核对的对应分类中。选项列表表示可用分类，不是自动生成的个人弱项。缺分类、失效、跨科或版本变化均不猜补。保存后返回的 `practice_recommendation` 只含公开说明及可用性，不泄露分类审查理由或内部 coach。

教学措辞和两科实例见[理由反馈](reasoning-feedback.md)。只有实际已经向本人讲过、且依据仍相符的内容才能保存；模板、预期结果和合成案例均不能作为真实学习记录写入。

先读取实际回答、复盘、材料和现存讲解，完成实际教学沟通；如果另记 reason_review，随后重新读 context，再保存讲解。revision_conflict/basis_changed/coaching_conflict 必须重新读并比较。超时保留同 request_id/同 payload，恢复后重试；先回放原回执，不因后来依据变化重复写入。

`read_review_queue(session_id, subject=null, before=0, limit=10, include_checked=false)` 按每题最新作答倒序，返回 entries、next_before、has_more、pending_count、checked_count、unavailable_count、subjects、base_task_id。limit 1–20。默认待核对与不可重做记录；include_checked 同时显示本次理由已核对。列表不含旧答案，详情显式读 context。新提交后只出现该题最新作答，旧讲解仍可从历史记录读取。

`resume_learning.review_queue` 最多3项，讲解字段截短，只用于选择下一步读取；旧题不按 FSRS 排期。本人自述不等于原因已证实；理由已核对不等于学习目标已掌握；有先前曝光/提示的作答不变成未见独立证据。

恢复时完整 context 中的结构化理由和练习建议也要检查 stale / available；旧讲解的冻结标签只是历史内容。作答被更新、材料不可用、分类被改动或讲解依据改变，旧建议不能继续采用。本人点击采用前再次读取并核对同一讲解和依据；采用只查询未见原题，不记录曝光、不开始任务、不改长期档案。有未完任务或未保存草稿/复盘时先处理现有内容。上一轮 `previous_coaching_note` 不能作为本轮理由审核。

`start_review_practice` 为认证浏览器内部接口，MCP不提供。本人明确操作才创建已见旧题任务；先校验预览修订号、当前任务、同题最新作答、题目版本与全部依据 ID。开始不记曝光，完整题面成功显示后另记 show。题面缺失、争议、旧版本或当前 active/paused 均拒绝，不切换到其他题。重做后 answer/reason 为空；上次说明仅作为独立历史内容查看。

恢复包整体控制在20KB以内（UTF-8 JSON）。resume_truncated=true 或 recent_reviews_truncated、review_queue.entries_truncated 等标记说明返回的摘要有所缩短；它们不改变已存记录或完整队列计数。按 read_coaching_context、read_study_review 或已有分页详情工具读取当前需要的原记录，不把缩短或未返回字段解释成没有历史。
