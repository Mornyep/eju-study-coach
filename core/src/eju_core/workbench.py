"""Durable task delivery and learner actions; every client shares Core transactions."""
from __future__ import annotations
import json
from .errors import CoreError


class WorkbenchMixin:
    def study_guidance(self, item):
        guidance = self.coaching.get(item['id'])
        return guidance if guidance and guidance.get('item_version') == item['version'] and guidance.get('reviewed') is True else None

    @staticmethod
    def reflection_options(item):
        subjects = item.get('subjects', [])
        common = [('question_direction', '题目问的方向'), ('not_sure', '还不确定'), ('independent_ok', '目前能自己说明依据')]
        if 'japanese_reading' in subjects:
            specific = [('reference', '指代对象'), ('relation', '句段关系'), ('scope', '选项范围或程度'), ('evidence', '原文依据'), ('vocabulary', '词语理解')]
        elif 'japanese_writing' in subjects:
            specific = [('task_requirements', '题目要求'), ('argument_support', '主张与理由'), ('structure', '文章结构'), ('expression', '表达')]
        elif 'integrated' in subjects:
            specific = [('knowledge', '知识点'), ('data_reading', '图表读取'), ('time_place', '时间或地点'), ('calculation', '计算'), ('elimination', '选项排除')]
        else:
            specific = [('evidence', '解题依据'), ('calculation', '计算')]
        return [dict(value=value, label=label) for value, label in common[:1] + specific + common[1:]]

    @staticmethod
    def latest_reflection(events, attempt_id):
        return next((e for e in reversed(events) if e['type'] == 'reflection' and e.get('attemptId') == attempt_id), None)

    @staticmethod
    def task_hint(item):
        return item.get('hint') or next((p['reason'] for p in item.get('prerequisites',[]) if p.get('reason')), '')

    @staticmethod
    def task_state(events):
        tasks = [e for e in events if e['type'] == 'task']
        if not tasks:
            return None
        task = dict(tasks[-1])
        task.update(answer='', reason='', guessed=False, status='active', exposure='none', attempt_id=None)
        for e in events:
            if e.get('task_id') != task['id']:
                continue
            if e['type'] == 'task_draft':
                task.update({k: e[k] for k in ('answer', 'reason', 'guessed')})
            if e['type'] == 'task_status':
                task.update(status=e['status'], attempt_id=e.get('attempt_id', task['attempt_id']))
            if e['type'] == 'exposure':
                levels = ['none', 'stem', 'hint', 'answer']
                if levels.index(e['level']) > levels.index(task['exposure']):
                    task['exposure'] = e['level']
        return task

    def task_summary(self, events):
        task = self.task_state(events)
        if task:
            task = {k: v[:500] if isinstance(v, str) else v for k,v in task.items()}
            task['full_draft_available'] = True
        return task

    def present_learning_task(self, session_id, expected_revision, request_id, item_id, instruction):
        from .store import short_text, stamp, today
        short_text(instruction, 'instruction', 2000)
        item = self.items.get(item_id)
        from .question_media import ready
        if not item or not ready(item):
            raise CoreError('item_unavailable', '任务需要已登记的完整题面和全部必要图片。')
        payload = {'item_id': item_id, 'instruction': instruction}
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, 'present_task', payload, expected_revision, session_id)
            if replay:
                return replay
            current = self.task_state(self.events(db))
            if current and current['status'] == 'active':
                raise CoreError('task_active', '已有未完任务；先保存暂停位置，再安排下一项。')
            e = dict(id=request_id, type='task', itemId=item_id, itemVersion=item['version'], instruction=instruction,
                     createdAt=stamp(), date=today(), sessionId=session_id)
            self.insert_event(db, e, 'native_codex')
            return self.finish_request(db, request_id, digest, task_id=request_id, learner_exposure_recorded=False)

    def read_workbench(self, session_id):
        with self.connection() as db:
            self.check_session(db, session_id)
            events = self.events(db)
            task = self.task_state(events)
            result = dict(ok=True, revision=self.meta(db, 'revision'), task=task)
            if task:
                from .evidence import item_status
                item = self.items[task['itemId']]
                status = item_status(events, item)
                from .question_media import assets
                images = assets(item)
                result['material'] = dict(id=item['id'], version=item['version'], stem=item.get('stem'),
                    title=item.get('displayTitle', '当前练习'), subjects=item.get('subjects', []), choiceCount=item.get('choiceCount'), grading_mode=(item.get('grading') or {}).get('mode'), status=status, hint_available=bool(self.task_hint(item)), images=images)
                if task['exposure'] in ('hint', 'answer'):
                    result['hint'] = self.task_hint(item)
                if task['exposure'] == 'answer':
                    result['solution'] = item.get('solution', '')
                    result['answer'] = item.get('answer')
                    result['coaching'] = self.study_guidance(item)
                    if task['status'] == 'completed':
                        result['reflection_options'] = self.reflection_options(item)
                        result['reflection'] = self.latest_reflection(events, task['attempt_id'])
                if task['attempt_id']:
                    result['attempt'] = next((e for e in events if e['id'] == task['attempt_id']), None)
            return result

    def workbench_action(self, session_id, expected_revision, request_id, task_id, action, payload=None):
        from .store import short_text, stamp, today
        payload = payload or {}
        if not isinstance(payload, dict):
            raise CoreError('invalid_field', '操作内容必须是对象。')
        allowed = {'show', 'draft', 'hint', 'answer', 'submit', 'pause', 'resume', 'reflect'}
        if action not in allowed:
            raise CoreError('invalid_action', '不支持此工作台操作。')
        content = dict(task_id=task_id, action=action, payload=payload)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, 'workbench', content, expected_revision, session_id)
            if replay:
                return replay
            events = self.events(db)
            task = self.task_state(events)
            if not task or task['id'] != task_id:
                raise CoreError('task_changed', '当前任务已变化，输入仍保留在页面；请读取新状态。')
            if task['status'] == 'completed' and action not in ('hint', 'answer', 'show', 'reflect'):
                raise CoreError('task_completed', '本次作答已经提交。')
            if task['status'] == 'paused' and action not in ('resume', 'draft'):
                raise CoreError('task_paused', '本次任务已暂停，先继续任务再作答。')
            item = self.items[task['itemId']]
            if action in ('show', 'hint', 'answer', 'submit'):
                from .question_media import assets
                assets(item)
            if action in ('hint', 'answer', 'submit') and not any(
                    e['type'] == 'exposure' and e.get('task_id') == task_id
                    and e.get('level') == 'stem' and e.get('source') == 'workbench'
                    and e.get('delivery_evidence') == 'client_report' for e in events):
                raise CoreError('question_not_shown', '当前题目尚未完整展示，不能提示、揭答或提交。')
            base = dict(createdAt=stamp(), date=today(), sessionId=session_id, task_id=task_id)
            extra = {}
            if action in ('show', 'hint', 'answer'):
                if action=='hint' and not self.task_hint(item):
                    raise CoreError('hint_unavailable','这题尚无核对过的提示，请回到 Codex 说明卡住的位置。')
                level = {'show':'stem', 'hint':'hint', 'answer':'answer'}[action]
                e = self.validate_native(db, dict(id=request_id, type='exposure', itemId=item['id'], level=level,
                                                 source='workbench', task_id=task_id), session_id)
                e['delivery_evidence'] = 'client_report' if action == 'show' else 'server_response_prepared'
                self.insert_event(db, e, 'native_workbench')
                extra['content'] = self.task_hint(item) if action == 'hint' else item.get('solution', '') if action == 'answer' else None
                extra['answer'] = item.get('answer') if action == 'answer' else None
            elif action == 'reflect':
                if task['status'] != 'completed' or task['exposure'] != 'answer' or not task['attempt_id']:
                    raise CoreError('reflection_not_ready', '提交并查看解析后再保存本人复盘。')
                if set(payload) - {'cause', 'evidence', 'next_action', 'base_reflection_id'}:
                    raise CoreError('invalid_field', '复盘包含不支持的字段。')
                cause = payload.get('cause')
                if cause not in [option['value'] for option in self.reflection_options(item)]:
                    raise CoreError('invalid_field', '请选择本次复盘方向；不确定时可选择还不确定。')
                evidence = short_text(payload.get('evidence'), 'evidence', 2000)
                next_action = short_text(payload.get('next_action', ''), 'next_action', 1200, empty=True)
                previous = self.latest_reflection(events, task['attempt_id'])
                if payload.get('base_reflection_id') != (previous['id'] if previous else None):
                    raise CoreError('reflection_conflict', '另一处已经更新这次复盘；请比较后明确选择保留内容。')
                e = dict(base, id=request_id, type='reflection', itemId=item['id'], itemVersion=item['version'],
                         attemptId=task['attempt_id'], cause=cause, evidence=evidence, next_action=next_action,
                         source='learner_self_report', previous_id=previous['id'] if previous else None)
                self.insert_event(db, e, 'native_workbench')
                extra['reflection_id'] = request_id
            elif action in ('draft', 'submit'):
                answer = short_text(payload.get('answer', ''), 'answer', 20000, empty=True)
                reason = short_text(payload.get('reason', ''), 'reason', 20000, empty=True)
                guessed = payload.get('guessed', False)
                if type(guessed) is not bool:
                    raise CoreError('invalid_field', 'guessed 必须是布尔值。')
                self.insert_event(db, dict(base, id=request_id+'.draft', type='task_draft', answer=answer,
                                           reason=reason, guessed=guessed), 'native_workbench')
                if action == 'submit':
                    if not answer.strip() and not reason.strip():
                        raise CoreError('answer_empty', '写下回答或卡住的位置后再提交。')
                    hints = [int(e.get('hint','H0')[1:]) for e in events if e['type']=='exposure'
                             and e.get('itemId')==item['id'] and e.get('task_id')==task_id]
                    seen = task.get('practice_mode') == 'review' or any(e['type']=='task_status' and e.get('task_id')==task_id and e['status']=='paused' for e in events) or any(e['type'] in ('attempt','exposure') and e.get('itemId')==item['id']
                               and (e.get('task_id')!=task_id or e.get('sessionId')!=session_id
                                    or e.get('level') in ('hint','answer')) for e in events)
                    grading = 'automatic' if (item.get('grading') or {}).get('mode') in ('number','choice') else 'external'
                    attempt = self.validate_native(db, dict(id=request_id+'.attempt', type='attempt', itemId=item['id'],
                        itemVersion=item['version'], answer=answer, reason=reason, guessed=guessed, seenBefore=seen,
                        hint='H'+str(max(hints or [0])), seconds=None, result='ungraded', grading=grading, task_id=task_id), session_id)
                    self.insert_event(db, attempt, 'native_workbench')
                    self.insert_event(db, dict(base, id=request_id+'.status', type='task_status', status='completed',
                        attempt_id=attempt['id']), 'native_workbench')
                    extra.update(attempt_id=attempt['id'], result=attempt['result'])
            else:
                self.insert_event(db, dict(base, id=request_id, type='task_status',
                    status='paused' if action=='pause' else 'active'), 'native_workbench')
            return self.finish_request(db, request_id, digest, task_id=task_id, **extra)

    def read_workbench_records(self,session_id,after=0,limit=10):
        result=self.read_learning_records(session_id,after,limit)
        for row in result['records']:
            event=row['event']
            # Coach-only card answers, source quotes and scheduling internals must never leak via learner history.
            if event.get('type','').startswith('recall_'):
                row['event']={k:event[k] for k in ('id','type','date','card_id','status') if k in event}
                row['event']['details']='回忆内容由 Codex 在相应学习轮次中展示。'
        return result
