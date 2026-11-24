"""Read-only validation of the v5 JSON backup contract (no migration or writes)."""
from __future__ import annotations

import calendar
import json
import math
import re
from datetime import datetime
from email.utils import parsedate_to_datetime

TYPES = frozenset({'attempt', 'exposure', 'issue', 'resolve', 'retract', 'worksheet', 'archive', 'reason_review'})
SUBJECTS = frozenset({'math1', 'math2', 'japanese_reading', 'japanese_writing', 'japanese_listening', 'japanese_listening_visual', 'physics', 'chemistry', 'biology', 'integrated'})


def _fail(message: str) -> None:
    raise ValueError(message)


def _length(value: str) -> int:
    return len(value.encode('utf-16-le', errors='surrogatepass')) // 2


def _text(value, maximum=20000) -> bool:
    return isinstance(value, str) and _length(value) <= maximum


def _number(value) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _choice(value, choices) -> bool:
    return isinstance(value, str) and value in choices


def _integer(value) -> bool:
    return _number(value) and value == int(value)


def date_ok(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return False
    year, month, day = map(int, value.split('-'))
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]


def _timestamp(value) -> bool:
    if not _text(value, 40) or not value.strip():
        return False
    # v5's emitted timestamp is ISO UTC. Also accept ISO date/naive/offset forms
    # and RFC timestamps; retain the original string, never normalize its value.
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True
    except (ValueError, OverflowError):
        try:
            return parsedate_to_datetime(value) is not None
        except (ValueError, TypeError, OverflowError):
            return False


def _json_check(value, *, banned=True) -> None:
    """Reject non-JSON objects/nonfinite numbers and prototype keys; don't filter."""
    try:
        def walk(v):
            if isinstance(v, dict):
                for key, child in v.items():
                    if not isinstance(key, str):
                        _fail('备份字段必须为字符串。')
                    if banned and key in {'__proto__', 'prototype', 'constructor'}:
                        _fail('备份包含不支持的字段，未导入。')
                    walk(child)
            elif isinstance(v, list):
                for child in v:
                    walk(child)
            elif v is None or isinstance(v, (str, bool)):
                pass
            elif not _number(v):
                _fail('备份包含非JSON数据或非有限数字。')
        walk(value)
    except RecursionError as exc:
        raise ValueError('备份嵌套过深或存在循环。') from exc


def _size(value) -> int:
    # Compact sorted JSON, matching JS canonical field order and UTF-16 units.
    # Integral Python floats encode like their JS Number representation.
    def normalize(v):
        if isinstance(v, dict):
            return {k: normalize(x) for k, x in v.items()}
        if isinstance(v, list):
            return [normalize(x) for x in v]
        if isinstance(v, float) and v.is_integer():
            return int(v)
        return v
    try:
        return _length(json.dumps(normalize(value), ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError('备份不能表示为有限JSON。') from exc


def _worksheet(w, *, item=False) -> bool:
    return (isinstance(w, dict) and (not item or _text(w.get('itemId'), 100))
            and _text(w.get('transcription'), 30000) and _text(w.get('feedback'), 30000)
            and isinstance(w.get('confirmed'), bool) and _integer(w.get('firstError')) and w['firstError'] >= -1)


def validate_event(event: dict) -> dict:
    e = event
    if not isinstance(e, dict):
        _fail('记录事件格式无效。')
    _json_check(e)
    if (not _text(e.get('id'), 150) or not e['id'] or not _choice(e.get('type'), TYPES) or not _timestamp(e.get('createdAt'))):
        _fail('记录事件的标识、类型或时间无效。')
    if not date_ok(e.get('date')):
        _fail('记录事件的学习日期无效。')
    if _size(e) > 100000:
        _fail('单条记录过大，未导入。')
    kind = e['type']
    if kind in {'attempt', 'exposure', 'issue', 'resolve'} and (not _text(e.get('itemId'), 100) or not e['itemId']):
        _fail('记录缺少题目ID。')
    if kind == 'attempt':
        if not isinstance(e.get('nodes'), list) or len(e['nodes']) > 30 or any(not _text(n, 100) for n in e['nodes']):
            _fail('作答知识点无效。')
        if not _choice(e.get('result'), {'correct', 'incorrect', 'ungraded'}) or not _choice(e.get('hint'), {'H0', 'H1', 'H2'}):
            _fail('作答结果或提示级别无效。')
        if not all(isinstance(e.get(k), bool) for k in ['seenBefore', 'guessed']):
            _fail('作答缺少独立性条件。')
        if not _text(e.get('answer')) or not _text(e.get('reason')) or not _text(e.get('itemVersion'), 100) or not _choice(e.get('grading'), {'automatic', 'self_report', 'external'}):
            _fail('作答证据不完整。')
        if 'seconds' not in e or (e['seconds'] is not None and (not _number(e['seconds']) or not 0 <= e['seconds'] <= 86400)):
            _fail('作答用时无效。')
    if 'sessionId' in e and (not _text(e['sessionId'], 150) or not e['sessionId']):
        _fail('练习会话标识无效。')
    if kind == 'exposure' and 'level' in e and not _choice(e['level'], {'stem', 'hint', 'answer'}):
        _fail('暴露类型无效。')
    if kind == 'issue' and (not _text(e.get('reason'), 5000) or not e['reason'].strip()):
        _fail('请说明题目哪里有问题。')
    if kind == 'resolve' and (not _text(e.get('issueId'), 150) or not e['issueId'] or not _text(e.get('reference'), 5000) or not e['reference'].strip() or not _text(e.get('explanation'), 10000) or not e['explanation'].strip() or not _choice(e.get('outcome'), {'confirmed_valid', 'corrected'})):
        _fail('纠正需要关联问题、核验来源和说明。')
    if kind == 'retract' and (not _text(e.get('targetId'), 150) or not _text(e.get('reason'), 5000) or not e['reason'].strip()):
        _fail('撤销证据需要目标和原因。')
    if kind == 'worksheet' and not _worksheet(e):
        _fail('步骤记录格式无效。')
    if kind == 'reason_review':
        if not _text(e.get('targetId'), 150) or not isinstance(e.get('agrees'), bool) or not _text(e.get('note'), 5000) or not e['note'].strip() or not _choice(e.get('reviewer'), {'self', 'tutor'}):
            _fail('理由核对需要关联作答、结论和来源说明。')
        if 'result' in e and not _choice(e['result'], {'correct', 'incorrect', 'ungraded'}):
            _fail('人工核对结果无效。')
    if kind == 'archive' and (not _text(e.get('content'), 50000) or not e['content'].strip()):
        _fail('聊天原文不能为空或超过50000字。')
    return e


def validate_state(state: dict) -> dict:
    s = state
    _json_check(s)
    if not isinstance(s, dict) or s.get('schema') != 5 or isinstance(s.get('schema'), bool) or not _text(s.get('id'), 150) or not s['id'] or not _integer(s.get('revision')) or not 0 <= s['revision'] <= 9007199254740991:
        _fail('这不是工作台v5备份。旧版聊天文字请用“保留聊天原文”。')
    p = s.get('profile')
    if not isinstance(p, dict) or not isinstance(p.get('subjects'), list) or not p['subjects'] or any(not isinstance(x, str) or x not in SUBJECTS for x in p['subjects']) or not _number(p.get('minutes')) or not 3 <= p['minutes'] <= 180:
        _fail('备份中的科目或学习时长无效。')
    if not isinstance(s.get('events'), list) or len(s['events']) > 25000:
        _fail('备份事件列表无效或过大。')
    draft = s.get('draft')
    if not isinstance(draft, dict) or _size(draft) > 200000:
        _fail('备份草稿无效或过大。')
    if 'currentItem' in draft and not _text(draft['currentItem'], 100):
        _fail('当前题目草稿无效。')
    if 'worksheet' in draft and not _worksheet(draft['worksheet'], item=True):
        _fail('手写工作区草稿无效。')
    # Validate events before draft references, while retaining all original data.
    ids = {}
    for e in s['events']:
        validate_event(e)
        if e['id'] in ids:
            _fail('备份内部有重复事件ID，未导入。')
        ids[e['id']] = e
    if 'practices' in draft:
        if not isinstance(draft['practices'], dict):
            _fail('练习草稿无效。')
        for id, practice in draft['practices'].items():
            p = practice
            if not _text(id, 100) or not isinstance(p, dict) or not _text(p.get('answer')) or not _text(p.get('reason')) or not _choice(p.get('hint'), {'H0', 'H1', 'H2'}) or not isinstance(p.get('seen'), bool) or not isinstance(p.get('guessed'), bool) or not date_ok(p.get('date')):
                _fail('练习草稿字段无效。')
            t = p.get('timer')
            if not isinstance(t, dict) or not isinstance(t.get('running'), bool) or not _number(t.get('elapsed')) or not 0 <= t['elapsed'] <= 86400 or (t['running'] and (not _number(t.get('startedAt')) or t['startedAt'] < 0)):
                _fail('练习计时草稿无效。')
            if 'submittedId' in p:
                e = ids.get(p['submittedId']) if isinstance(p['submittedId'], str) else None
                if not _text(p['submittedId'], 150) or not e or e['type'] != 'attempt' or e['itemId'] != id:
                    _fail('练习草稿的已提交作答不存在。')
    if not date_ok(s.get('createdDate')):
        _fail('备份创建日期无效。')
    for e in s['events']:
        if e['type'] == 'resolve':
            issue = ids.get(e['issueId'])
            if not issue or issue['type'] != 'issue' or issue['itemId'] != e['itemId']:
                _fail('纠正记录找不到对应问题，未导入。')
        if e['type'] in {'retract', 'reason_review'}:
            target = ids.get(e['targetId'])
            if not target or target['type'] != 'attempt':
                _fail('撤销记录找不到对应作答，未导入。')
    return s


def parse_backup(raw: str) -> dict:
    if not _text(raw, 12000000):
        _fail('备份超过12MB，未导入。')
    try:
        def invalid_constant(value):
            raise ValueError(value)
        doc = json.loads(raw, parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise ValueError('文件不是有效JSON，原记录未改变。') from exc
    if not isinstance(doc, dict) or doc.get('format') != 'eju-workbench-backup' or not doc.get('state'):
        _fail('不是EJU工作台备份；请不要导入试卷或普通JSON。')
    return validate_state(doc['state'])
