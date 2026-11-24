"""Legacy preservation and real Node-v5 comparison, without external dependencies."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'core' / 'src'))
from eju_core.legacy import parse_backup, validate_state, validate_event
from eju_core.evidence import evidence_map, item_status, invalid_reason

NODE = os.environ.get('EJU_NODE') or shutil.which('node')
ITEMS = [dict(id=x, nodes=['M.test'], status='reviewed_original', version='1', groupId='G') for x in 'abcdefg']
NODES = [dict(id='M.test', label='测试目标'), dict(id='M.other', label='未测')]


def state(events=None):
    return dict(schema=5, id='backup-one', revision=0, profile=dict(subjects=['math1'], minutes=15), events=events or [], draft={}, createdDate='2026-10-14')


def event(id='e1', item='a', date='2026-10-14', **extra):
    e = dict(id=id, type='attempt', itemId=item, nodes=['M.test'], itemVersion='1', date=date, createdAt=date+'T10:00:00Z', answer='2/3', reason='限制后的3种结果中2种成功', result='correct', hint='H0', seenBefore=False, guessed=False, grading='automatic', seconds=None)
    e.update(extra)
    return e


def auxiliary(kind, id, date='2026-10-14', **extra):
    return dict(type=kind, id=id, date=date, createdAt=date+'T10:00:01Z', **extra)


def review(target='e1', id='r1', date='2026-10-14', **extra):
    return auxiliary('reason_review', id, date, targetId=target, agrees=True, note='对照解析检查理由', reviewer='self', **extra)


def js(payload):
    code = """const fs=require('node:fs'),E=require(process.argv[1]);
const p=JSON.parse(fs.readFileSync(0,'utf8'));
if(p.op==='validate'){try{E.validateState(p.state);process.stdout.write(JSON.stringify({valid:true}));}catch(e){process.stdout.write(JSON.stringify({valid:false}));}}
else{const s={events:p.events};process.stdout.write(JSON.stringify({map:E.evidenceMap(s,p.items,p.nodes),statuses:p.items.map(i=>E.itemStatus(s,i)),invalid:p.events.filter(e=>e.type==='attempt').map(e=>E.invalidReason(s,e,p.items.find(i=>i.id===e.itemId)))}));}"""
    result = subprocess.run([NODE, '-e', code, str(Path(__file__).resolve().parent / 'fixtures' / 'v5-engine.js')], input=json.dumps(payload, ensure_ascii=False), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


class ValidationTests(unittest.TestCase):
    def test_full_backup_preserves_unknowns_and_identity(self):
        e = event(extension={'日本語': ['𝄞', {'future': 9}], 'flag': True})
        s = state([e, review()])
        s['profile']['extra'] = {'preference': '短く'}
        s['draft'] = dict(currentItem='a', worksheet=dict(itemId='a', transcription='α²−β', feedback='', confirmed=False, firstError=-1, unknown='keep'), practices={'a': dict(answer='2/3', reason='条件', hint='H1', seen=False, guessed=False, date='2026-10-14', timer=dict(running=False, elapsed=0, startedAt=None, marker='keep'), submittedId='e1')}, extra={'keep': [1, False, None]})
        s['extension'] = ['unchanged', {'opaque': 1}]
        before = copy.deepcopy(s)
        self.assertIs(validate_state(s), s)
        self.assertIs(validate_event(e), e)
        raw = json.dumps(dict(format='eju-workbench-backup', version='5.0', state=s, wrapper_extension='not a state field'), ensure_ascii=False)
        self.assertEqual(parse_backup(raw), before)
        self.assertEqual(s, before)

    def test_event_variants_and_references(self):
        events = [event(), review(), auxiliary('exposure', 'x', itemId='a', sessionId='S', level='stem'), auxiliary('issue', 'i', itemId='a', reason='疑问'), auxiliary('resolve', 'c', itemId='a', issueId='i', outcome='confirmed_valid', reference='题与答案', explanation='复核一致'), auxiliary('retract', 't', targetId='e1', reason='见过'), auxiliary('worksheet', 'w', transcription='1+2', feedback='', confirmed=True, firstError=0), auxiliary('archive', 'z', content='原记录')]
        validate_state(state(events))
        for e in events:
            validate_event(e)
        for field in ['issueId', 'targetId']:
            for original in [e for e in events if field in e]:
                bad = copy.deepcopy(events)
                next(e for e in bad if e['id']==original['id'])[field] = 'missing'
                with self.assertRaises(ValueError): validate_state(state(bad))
        bad = copy.deepcopy(events); bad[4]['itemId'] = 'b'
        with self.assertRaises(ValueError): validate_state(state(bad))

    def test_event_invalid_values_raise_value_error(self):
        changes = [{'type':'native'}, {'type':[]}, {'hint':'H3'}, {'hint':{}}, {'result':[]}, {'grading':[]}, {'seenBefore':0}, {'guessed':None}, {'seconds':True}, {'seconds':-1}, {'seconds':'0'}, {'seconds':86401}, {'createdAt':'not-date'}, {'date':'2026-02-30'}, {'nodes':['x']*31}, {'nodes':[7]}, {'itemId':''}, {'itemVersion':{}}, {'answer':'a'*20001}, {'sessionId':''}, {'revision_extension':float('nan')}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError): validate_event(event(**change))
        missing = event(); del missing['seconds']
        with self.assertRaises(ValueError): validate_event(missing)
        for seconds in [None, 0, 30, 86400]: self.assertEqual(validate_event(event(seconds=seconds))['seconds'], seconds)
        for timestamp in ['2026-10-14T10:00:00Z', '2026-10-14T19:00:00+09:00', '2026-10-14', 'Wed, 14 Oct 2026 10:00:00 GMT']:
            validate_event(event(createdAt=timestamp))

    def test_state_invalid_profiles_dates_drafts_duplicates(self):
        mutations = [lambda s:s.update(schema=6), lambda s:s.update(revision=True), lambda s:s.update(revision=9007199254740992), lambda s:s['profile'].update(minutes=True), lambda s:s['profile'].update(minutes=2), lambda s:s['profile'].update(subjects=[]), lambda s:s['profile'].update(subjects=['unknown']), lambda s:s.update(createdDate='2027-02-29'), lambda s:s.update(draft=[]), lambda s:s['events'].append(copy.deepcopy(s['events'][0])), lambda s:s['draft'].update(worksheet={'itemId':'a'}), lambda s:s['draft'].update(currentItem=123)]
        for mutation in mutations:
            s=state([event()]);mutation(s)
            with self.subTest(state=s), self.assertRaises(ValueError):validate_state(s)
        validate_state(state() | {'createdDate':'2028-02-29'})
        # Gregorian year zero accepted by JS Date's strict date-only validation.
        validate_state(state() | {'createdDate':'0000-02-29'})
        p=dict(answer='',reason='',hint='H0',seen=False,guessed=False,date='2026-10-14',timer=dict(running=True,elapsed=0,startedAt=10))
        for change in [{'elapsed':-1},{'elapsed':True},{'running':1},{'startedAt':None}]:
            bad=copy.deepcopy(p);bad['timer'].update(change)
            with self.assertRaises(ValueError):validate_state(state() | {'draft':{'practices':{'a':bad}}})
        p['submittedId']='missing'
        with self.assertRaises(ValueError):validate_state(state() | {'draft':{'practices':{'a':p}}})

    def test_json_and_size_boundaries(self):
        for raw in ['{broken','null','[]','{"format":"x"}', '{"format":"eju-workbench-backup","state":{"value":NaN}}', 'x'*12000001]:
            with self.assertRaises(ValueError):parse_backup(raw)
        for key in ['__proto__','prototype','constructor']:
            s=state();s['extra']={'nested':[{key:1}]}
            with self.assertRaises(ValueError):validate_state(s)
        for value in [float('inf'), set(), object()]:
            s=state();s['extra']=value
            with self.assertRaises(ValueError):validate_state(s)
        e=event(extra='𝄞'*50001)
        with self.assertRaises(ValueError):validate_event(e)
        s=state();s['draft']['extra']='x'*200001
        with self.assertRaises(ValueError):validate_state(s)
        s=state();s['events']=[event(str(i)) for i in range(25001)]
        with self.assertRaises(ValueError):validate_state(s)
        s=state();s['extra']=s
        with self.assertRaises(ValueError):validate_state(s)


@unittest.skipUnless(NODE, 'Node unavailable; actual JS comparison not run')
class EngineParityTests(unittest.TestCase):
    def compare(self, events, items=None):
        items=ITEMS if items is None else items
        before=copy.deepcopy((events,items,NODES))
        expected=js(dict(op='evidence',events=events,items=items,nodes=NODES))
        actual=dict(map=evidence_map(events,items,NODES),statuses=[item_status(events,i) for i in items],invalid=[invalid_reason(events,e,next((i for i in items if i['id']==e['itemId']),None)) for e in events if e.get('type')=='attempt'])
        self.assertEqual(actual,expected)
        self.assertEqual((events,items,NODES),before)
        return actual['map']['M.test']

    def test_basic_hint_guess_reason_and_native(self):
        self.compare([]); self.compare([event()]);self.compare([event(),review()])
        for change in [{'hint':'H1'},{'hint':'H2'},{'hint':'H3'},{'hint':'H4'},{'guessed':True},{'seenBefore':True},{'reason':''},{'nodes':['M.other']}]:
            with self.subTest(change=change):self.compare([event(**change),review()])
        # The old JS engine only defines v5 events. Its baseline remains unchanged;
        # the new Python extension must ignore unrelated native management events.
        baseline = [event(), review()]
        self.compare(baseline)
        extended = baseline + [dict(type='native_profile_changed', arbitrary='ignored')]
        self.assertEqual(evidence_map(extended, ITEMS, NODES), evidence_map(baseline, ITEMS, NODES))
        self.assertEqual([item_status(extended, i) for i in ITEMS], [item_status(baseline, i) for i in ITEMS])

    def test_exposure_sessions_and_hint_repair(self):
        for level,session,date in [('stem','S','2026-10-14'),('stem','OTHER','2026-10-14'),('stem','S','2026-10-13'),('hint','S','2026-10-14'),('answer','S','2026-10-14')]:
            exp=auxiliary('exposure','x',date,itemId='a',level=level,sessionId=session);exp['createdAt']='2026-10-14T09:00:00Z'
            with self.subTest(level=level,session=session,date=date):self.compare([exp,event(sessionId='S'),review()])
        exp=auxiliary('exposure','x',itemId='a',level='hint',sessionId='S');exp['createdAt']='2026-10-14T09:00:00Z'
        self.compare([exp,event(sessionId='S',hint='H1'),review()])
        historical=auxiliary('exposure','x','2026-10-13',itemId='a')
        self.compare([historical,event(),review()])

    def test_delayed_early_schedule_and_multiple_groups(self):
        events=[]
        cases=[('a','2026-10-01',1,'2026-10-02'),('b','2026-10-02',2,'2026-10-05'),('c','2026-10-05',3,'2026-10-12'),('d','2026-10-06',3,'2026-10-12'),('e','2026-10-12',4,'2026-10-26'),('f','2026-10-30',5,'2026-11-29'),('g','2026-12-01',5,'2026-12-31')]
        for id,date,stage,due in cases:
            events += [event('e'+id,id,date),review('e'+id,'r'+id,date)]
            row=self.compare(events);self.assertEqual((row['stage'],row['due']),(stage,due))
        base=[event(),review()]
        for item,date in [('a','2026-10-15'),('b','2026-10-14'),('b','2026-10-15')]:
            self.compare(base+[event('e2',item,date),review('e2','r2',date)])
        more=copy.deepcopy(ITEMS);more[1]['groupId']='OTHER'
        self.compare(base+[event('e2','b','2026-10-16'),review('e2','r2','2026-10-16')],more)

    def test_dispute_correction_retract_versions_and_repairs(self):
        base=[event(),review()]
        issue=auxiliary('issue','i',itemId='a',reason='分母疑似错误')
        self.compare(base+[issue])
        for outcome in ['confirmed_valid','corrected']:
            self.compare(base+[issue,auxiliary('resolve','c',itemId='a',issueId='i',outcome=outcome,reference='原题与答案行',explanation='保留来源')])
        self.compare(base+[auxiliary('retract','t',targetId='e1',reason='误记未见')])
        for field,value in [('version','2'),('status','indexed')]:
            changed=copy.deepcopy(ITEMS);changed[0][field]=value;self.compare(base,changed)
        self.compare(base,[])
        for change in [{'result':'incorrect'},{'hint':'H1'},{'guessed':True}]:
            self.compare(base+[event('e2','b','2026-10-15',**change),review('e2','r2','2026-10-15')])
        self.compare(base+[event('e2','a',hint='H2',seenBefore=True,createdAt='2026-10-14T10:00:02Z'),review('e2','r2')])

    def test_reason_review_effective_result_and_order(self):
        e=event(result='ungraded',grading='self_report');self.compare([e,review()])
        r=review(id='r2',result='correct');r['createdAt']='2026-10-14T10:01:00Z'
        self.compare([e,review(),r])
        neg=review(id='r3');neg.update(agrees=False,createdAt='2026-10-14T10:02:00Z')
        self.compare([e,review(),r,neg])
        # Date/createdAt/id lexical ordering is deliberately inherited from JS.
        self.compare([r,e,review()])

    def test_representative_validation_parity(self):
        fixtures=[state(),state([event(),review()])]
        for change in [{'schema':99},{'revision':True},{'revision':-1},{'createdDate':'2026-02-30'},{'profile':{'subjects':['invalid'],'minutes':15}},{'draft':[]}]:fixtures.append(state()|change)
        for change in [{'hint':'H3'},{'seconds':'0'},{'createdAt':'invalid'},{'date':'2026-02-30'},{'type':[]},{'answer':'a'*20001}]:fixtures.append(state([event(**change)]))
        fixtures.append(state([event(),event()]))
        fixtures.append(state([auxiliary('resolve','r',itemId='a',issueId='missing',outcome='corrected',reference='x',explanation='y')]))
        fixtures.append(state()|{'createdDate':'0000-02-29'})
        for s in fixtures:
            with self.subTest(state=s):
                want=js(dict(op='validate',state=s))['valid']
                try:validate_state(s);actual=True
                except ValueError:actual=False
                self.assertEqual(actual,want)


if __name__ == '__main__':
    unittest.main()
