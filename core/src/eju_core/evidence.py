"""Derive v5 evidence without mutating events; native unrelated types are ignored."""
from __future__ import annotations
import calendar
from .legacy import date_ok
from .exposure import shares_exposure

_RELEVANT = {'attempt', 'exposure', 'issue', 'resolve', 'retract', 'reason_review'}


def _ordered(events):
    # Original engine uses lexical date/createdAt/id order, not UTC timestamp order.
    return sorted((e for e in events if e.get('type') in _RELEVANT), key=lambda e: (e.get('date', ''), e.get('createdAt', ''), e.get('id', '')))


def _add_days(value, days):
    if not date_ok(value):
        raise ValueError('日期无效，请使用真实的年月日。')
    y, m, d = map(int, value.split('-'))
    d += days
    while d > calendar.monthrange(y, m)[1]:
        d -= calendar.monthrange(y, m)[1]
        m += 1
        if m > 12:
            m = 1
            y += 1
    if y > 9999:  # JS ISO extended year, then its original slice(0,10).
        return f'+{y:06d}-{m:02d}-{d:02d}'[:10]
    return f'{y:04d}-{m:02d}-{d:02d}'


def item_status(events: list[dict], item: dict) -> str:
    all_events = _ordered(events)
    for issue in (e for e in all_events if e['type'] == 'issue' and e.get('itemId') == item['id']):
        if not any(r['type'] == 'resolve' and r.get('issueId') == issue['id'] for r in all_events):
            return 'disputed'
    if any(r['type'] == 'resolve' and r.get('itemId') == item['id'] and r.get('outcome') == 'corrected' for r in all_events):
        return 'corrected_local'
    return item.get('status') or 'indexed'


def invalid_reason(events: list[dict], event: dict, item: dict | None) -> str:
    if any(e.get('type') == 'retract' and e.get('targetId') == event['id'] for e in events):
        return '已撤销，保留原证据'
    if item is None:
        return '题目不在当前内容版本中，保留待核'
    if item_status(events, item) == 'disputed':
        return '题目争议未解决，暂不计入'
    if any(e.get('type') == 'resolve' and e.get('itemId') == event.get('itemId') and e.get('outcome') == 'corrected' for e in events):
        return '题目已有本地纠正，旧作答需在新核验题上重测'
    if event.get('itemVersion') != item.get('version'):
        return '题目版本已变化，待核对'
    if item.get('status') not in {'reviewed_original', 'reviewed_official'}:
        return '答案与解析尚未审完，不计能力证据'
    return ''


def _prior_exposure(events, event, include_current_hints=True, by_id=None):
    by_id = by_id or {}
    item = by_id.get(event.get("itemId"), {})
    for x in events:
        if x.get('id') == event.get('id') or (x.get('itemId') != event.get('itemId') and not shares_exposure(item, by_id.get(x.get('itemId'), {}))) or x.get('type') not in {'exposure', 'attempt'} or x.get('createdAt', '') > event.get('createdAt', ''):
            continue
        if x.get('itemId') != event.get('itemId'):
            return True
        if x['type'] == 'exposure' and (x.get('level') == 'stem' or (not include_current_hints and x.get('level') == 'hint')) and event.get('sessionId') and x.get('sessionId') == event['sessionId'] and x.get('date') == event.get('date'):
            paused_between = any(p.get('type') == 'plan' and p.get('status') == 'paused'
                                 and x.get('createdAt', '') <= p.get('createdAt', '') <= event.get('createdAt', '')
                                 for p in events)
            if not paused_between:
                continue
        return True
    return False


def _independent(e):
    return (e.get('type') == 'attempt' and e.get('result') == 'correct' and e.get('hint') == 'H0'
            and not e.get('seenBefore') and not e.get('guessed') and bool(e.get('reason', '').strip()))


def evidence_map(events: list[dict], items: list[dict], nodes: list[dict]) -> dict:
    by_id = {i['id']: i for i in items}
    out = {n['id']: dict(id=n['id'], label=n.get('label'), status='untested', stage=0, due=None, lastDate=None, evidence=[], invalid=[], independent=[], progress={}) for n in nodes}
    ordered = _ordered(events)
    # Index global correction and review facts once. The v5 rules are unchanged;
    # re-sorting all events for every attempt made long-running vaults quadratic.
    retracted = {e.get('targetId') for e in ordered if e['type'] == 'retract'}
    resolved = {e.get('issueId') for e in ordered if e['type'] == 'resolve'}
    disputed = {e.get('itemId') for e in ordered if e['type'] == 'issue' and e['id'] not in resolved}
    corrected = {e.get('itemId') for e in ordered if e['type'] == 'resolve' and e.get('outcome') == 'corrected'}
    latest_reviews = {e.get('targetId'): e for e in ordered if e['type'] == 'reason_review'}
    for e in (e for e in ordered if e['type'] == 'attempt'):
        item = by_id.get(e.get('itemId'))
        if e['id'] in retracted:
            invalid = '已撤销，保留原证据'
        elif item is None:
            invalid = '题目不在当前内容版本中，保留待核'
        elif item['id'] in disputed:
            invalid = '题目争议未解决，暂不计入'
        elif item['id'] in corrected:
            invalid = '题目已有本地纠正，旧作答需在新核验题上重测'
        elif e.get('itemVersion') != item.get('version'):
            invalid = '题目版本已变化，待核对'
        elif item.get('status') not in {'reviewed_original', 'reviewed_official'}:
            invalid = '答案与解析尚未审完，不计能力证据'
        else:
            invalid = ''
        # Never trust event.nodes to expand the current item's scope.
        for node_id in (item.get('nodes', []) if item else []):
            row = out.get(node_id)
            if row is None:
                continue
            if invalid:
                row['invalid'].append(dict(event=e['id'], reason=invalid))
                continue
            row['evidence'].append(e['id'])
            row['lastDate'] = e['date']
            last = latest_reviews.get(e['id'])
            checked = bool(last and last.get('agrees'))
            effective = dict(e)
            effective['result'] = ('incorrect' if last and not checked else (last.get('result', 'ungraded') if e.get('result') == 'ungraded' and last else e.get('result')))
            if _independent(effective) and checked and not _prior_exposure(events, e, by_id=by_id):
                group = item.get('groupId') or item['id']
                earlier = row['progress'].get(group)
                row['independent'].append(dict(itemId=e['itemId'], groupId=group, date=e['date'], event=e['id']))
                if earlier is None or (e['date'] >= earlier['due'] and e['date'] > earlier['date'] and e['itemId'] != earlier['itemId']):
                    stage = min(5, earlier['stage'] + 1) if earlier else 1
                    row['progress'][group] = dict(itemId=e['itemId'], date=e['date'], stage=stage, due=_add_days(e['date'], [0, 1, 3, 7, 14, 30][stage]))
                    row['stage'] = max(p['stage'] for p in row['progress'].values())
                    row['status'] = 'delayed' if row['stage'] >= 2 else 'immediate'
                    row['due'] = min(p['due'] for p in row['progress'].values())
            elif effective['result'] == 'incorrect' or (effective['result'] == 'correct' and (e.get('hint') != 'H0' or e.get('guessed')) and not e.get('seenBefore') and not _prior_exposure(events, e, False, by_id)):
                row.update(status='needs_repair', stage=0, due=_add_days(e['date'], 1), independent=[], progress={})
            elif effective['result'] == 'correct':
                if row['status'] in {'untested', 'supported'}:
                    row['status'] = 'supported'
                row['due'] = row['due'] or _add_days(e['date'], 1)
    return out
