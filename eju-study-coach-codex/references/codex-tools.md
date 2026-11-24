# EJU Core 基础10工具协议

B版保留以下接口，并增加[12个资料、工作台及回忆工具](codex-b-tools.md)。

配置名默认 `eju_core`，实现内服务器名为eju-learning-core；工具前缀由宿主确定，按实际发现调用。服务为STDIO，不是HTTP。下表名称来自当前server.py，不增加拟定但未实现的工具。

|工具|实际参数|用途/关键返回|
|---|---|---|
|resume_learning|无|ok、vault_id、session_id、revision、local_date、profile、last_plan、recent_events和截断标志|
|set_profile|session_id, expected_revision, request_id, profile|保存本人确认档案；profile支持subjects、minutes、goal、exam、language|
|get_task_candidates|session_id, minutes=null, subject=null, purpose="study"|最多3个未见候选。study仅材料完整且已核对的EJU原题；supplement辅助原创；prerequisite基础补练。无原题候选不静默回退|
|read_question_images|session_id, item_id|取得所有完整原页/原创配图，返回真实图片、绝对路径与hash；内部读取不记曝光，须在学习者消息实际嵌入图片|
|read_material|session_id, resource_id, section="stem", offset=0, limit=6000|stem或coach；文本分页，注册二进制返回受控路径，不自动识别|
|read_learning_records|session_id, after=0, limit=10, item_id=null, event_id=null, include_legacy_draft=false|原事件、origin、next_cursor、has_more；需旧未完题时显式读draft|
|read_record_detail|session_id, record_type, event_id=null, offset=0, limit=6000, source_sha256=null|event/profile/legacy_draft/legacy_sources/legacy_source原始JSON片段；json_fragment、next_offset、has_more、total_characters、sha256|
|record_learning_event|session_id, expected_revision, event|事实保存；event.id作为幂等请求标识|
|save_learning_plan|session_id, expected_revision, request_id, plan|保存管理决定，attempts_added=0|
|import_material|session_id, expected_revision, request_id, inbox_name|仅vault/inbox单文件名；回执resource_id/sha256，extracted=false|
|prepare_feedback|session_id, item_id, category, description|question/explanation/software/learning草稿，draft_only=true、uploaded=false，不发送|

## 会话、修订与回执

每个新聊天首先resume，使用返回session_id与revision。恢复视图有界：has_more_history/evidence_truncated/issues_truncated为真时，按当前需要分页read_learning_records；after用next_cursor，limit最大50。摘要未知不要洗成完整已读。profile的details_truncated/full_profile_available、last_plan的details_truncated提示还有完整字段；需要档案保留的长/未知字段时用read_record_detail(record_type="profile")，需要完整计划时先定位其事件再用event详情。evidence每行只给最近5个引用，另有evidence_total/invalid_total/independent_total与details_truncated；总量不等于返回全部事件，必要时分页历史恢复具体依据。

写入带最新expected_revision和稳定ID，回执须ok=true且saved=true。成功后使用新revision；replayed=true时若有current_revision，以它为当前值，旧receipt.revision不能覆盖更新。超时/不明结果保留同ID同内容并恢复/查原事件，不生成另一个成功；同ID不同内容会冲突，不偷换ID覆盖。revision_conflict先resume再重读相关事实/计划，重新判断是否仍需原动作。session失效同样恢复，不要求用户复述全库。

read_learning_records的单事件过长时返回摘要和record_truncated=true；很大的旧草稿返回null与legacy_draft_truncated=true。此时用read_record_detail读取event（须event_id）或legacy_draft，不把null解释成没有草稿。按next_offset续读直到has_more=false，limit最大12000字符；json_fragment可能不是单独可解析JSON，需按offset拼接，sha256用于确认同一份原记录。只读当前需要的详情，不因工具存在就全量灌入上下文。

后续v5导入不自动覆盖当前草稿；resume的legacy_source_count提示来源数量，read_learning_records(include_legacy_draft=true)返回最近10份legacy_sources的source_sha256与导入修订。需要更早来源时用read_record_detail(record_type="legacy_sources")分页读索引；要具体原备份用record_type="legacy_source", source_sha256=返回的hash。由原备份的state.draft读取后续草稿，不能把首次active草稿当成最新导入快照。

set_profile合并保存本人确认的字段，保留未传入值和旧版未知字段；subjects必传，可为空，minutes可不传或为null。仅支持subjects、minutes、goal、exam、language的新值，不把summary中的details_truncated/full_profile_available当档案字段发送。需要长/未知字段的完整上下文时按需read_record_detail，不为更新一个设置而重复上传整个档案。服务初始化不猜数学1或时长；只问当前安排需要的信息，不让学习者挑模式或维护数据库。

## 事件协议

事件必须含id和type。createdAt/date/sessionId/origin/reportedHint由服务写入，**原生事件不传这些字段**；历史日期走CLI迁移，不能把旧作答今天重记为新成功。

- attempt：itemId/itemVersion/answer/reason/result(correct|incorrect|ungraded)/hint(H0–H4)/seenBefore/guessed/seconds(null或测得)/grading(automatic|self_report|external)。nodes由服务取实际题目，不能扩范围；非null seconds须timingSource=learner_report或workbench。本次提示最高级由服务核曝光底线，回执applied_hint为实际等级。
- exposure：itemId、level(stem|hint|answer)、source(codex_report|learner_report|workbench)，hint可选。stem默认为H0、hint至少H1、answer至少H4。只保存真正已给学习者的内容；若本轮消息尚未发出而没有显示回执，不能把计划当实际展示，可在收到作答时补记确认已显示的同session题面。coach读答案不自动曝光；不要把模型自报当独立UI观测。
- reason_review：targetId、agrees、note、reviewer(self|tutor)，可选result。本人对照为self，Codex实际审理由为tutor；不能没读就同意。未评分开放题要明确人工结果，自动数值正确仍需理由审核才能独立推进。
- issue：itemId、reason；resolve：issueId、itemId、reference、explanation、outcome(confirmed_valid|corrected)。关联必须真实存在，纠正保留旧事件，不能编新版验证。
- retract：targetId、reason；withdraw不删除源事件。
- worksheet：transcription、feedback、confirmed、firstError（−1未知，否则从0计行）。照片实际读取/转录未确认不得写confirmed=true。
- archive：content，保存可获得的原文，非独立作答证据。用户自带未登记题可先据完整材料教学，并用archive/worksheet/计划留事实；导入文件不会自动成为可评分item，不编itemId/version。

数值自动核对只针对number/choice，服务会另存reportedResult并计算实际result；不能把数值匹配当推导正确或单位无误。Hint H3/H4仍有提示，不因为兼容v5形状校验而写成H2或H0。完整解析后改对不独立。

save_learning_plan需objective/next_action/reason/status(active|paused|completed)，可含basis_event_ids、item_ids、hypotheses列表。引用真实已存事件/已登记题，不猜阶段/到期写入；completed只表示管理动作完成。paused切断此前题面在同会话中的首次曝光例外，再答旧题也不作未见独立证据。停止保存paused和接续点，不加题或新复测占位；若仅要求记曝光，就只记该事实。

## 不可用或冲突

ok=false/error.code表示失败，不说保存成功。read_material available=false或缺资源说明具体缺口，继续可独立处理的部分；不要从摘要拼真题。prepare_feedback仅草稿，不能说已投稿。import-v5、backup、restore、export是CLI维护入口，不编同名MCP调用。

read_material.status是当前争议/纠正状态，catalog_status是内容初始审核状态；usable_for_assessment=false时可讨论题目疑义，但不当正常测评答案使用。
