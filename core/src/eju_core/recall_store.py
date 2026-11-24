"""Recall facts and scheduling share the same revisioned event log as problem solving."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import json
from .errors import CoreError

class RecallMixin:
    def _recall_source(self,db,source):
        from .evidence import item_status
        if not isinstance(source,dict):raise CoreError('invalid_source','回忆项目必须有可核对的来源。')
        if source.get('type')=='catalog':
            item=self.items.get(source.get('item_id'))
            if not item or item['version']!=source.get('item_version') or item_status(self.events(db),item) not in ('reviewed_original','reviewed_official'):
                raise CoreError('source_unverified','题目来源不存在、版本变化或有争议。')
            basis='\n'.join([item.get('stem',''),item.get('solution','')]+[p.get('reason','') for p in item.get('prerequisites',[])])
        elif source.get('type')=='document_segment':
            doc=self.document_state(db,source.get('document_id'))
            segment=next((s for s in doc['segments'] if s['id']==source.get('segment_id')),None)
            if not segment or segment['status'] not in ('confirmed','corrected') or not source.get('review_id') or source.get('review_id')!=segment.get('review_id'):
                raise CoreError('source_unverified','原图转录待核对或核对版本已变化，暂不安排此回忆项目。')
            page=next(p for p in doc['pages'] if p['page_no']==segment['page_no'])
            row=db.execute('SELECT * FROM artifacts WHERE id=?',(page['artifact_id'],)).fetchone();self.verify_artifact(row)
            basis=segment['text']
        else:raise CoreError('invalid_source','只支持已核对题目或原图片段作为来源。')
        quote=source.get('quote')
        if not isinstance(quote,str) or not quote.strip() or len(quote)>2000 or quote not in basis:
            raise CoreError('source_quote_mismatch','来源引文不在已核对文本中。')

    def _recall_card(self,db,card_id):
        row=db.execute('SELECT body FROM events WHERE id=?',(card_id,)).fetchone();card=json.loads(row[0]) if row else None
        if not card or card['type']!='recall_card':raise CoreError('card_missing','没有这个回忆项目。')
        for e in self.events(db):
            if e['type']=='recall_review' and e['card_id']==card_id:
                card['state']=e['state'];card['eligible_after']=e['eligible_after']
        return card

    def create_recall_card(self,session_id,expected_revision,request_id,kind,prompt,answer,source,review_note):
        from .recall import new_state,is_eligible_kind
        from .store import short_text,stamp,today
        if not is_eligible_kind(kind):raise CoreError('recall_ineligible','FSRS 仅用于词汇、定义和公式条件；复杂解题继续使用原证据规则。')
        for key,value in [('prompt',prompt),('answer',answer),('review_note',review_note)]:short_text(value,key,2000)
        payload=dict(kind=kind,prompt=prompt,answer=answer,source=source,review_note=review_note)
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,request_id,'create_recall',payload,expected_revision,session_id)
            if replay:return replay
            self._recall_source(db,source)
            state=new_state(request_id,datetime.now(timezone.utc))
            self.insert_event(db,dict(payload,id=request_id,type='recall_card',state=state,createdAt=stamp(),date=today(),sessionId=session_id,
                answer_validation='tutor_reported_source_review'),'native_recall')
            return self.finish_request(db,request_id,digest,card_id=request_id,due=state['card']['due'],legacy_schedule_changed=False)

    def read_recall_cards(self,session_id,card_id=None,section='prompt',offset=0,limit=10):
        from .store import integer
        integer(offset,'offset',0,25000);integer(limit,'limit',1,30)
        if section not in ('prompt','coach'):raise CoreError('invalid_section','section 为 prompt 或 coach。')
        with self.connection() as db:
            self.check_session(db,session_id)
            ids=[e['id'] for e in self.events(db) if e['type']=='recall_card'] if card_id is None else [card_id]
            result=[]
            for cid in ids[offset:offset+limit]:
                card=self._recall_card(db,cid)
                try:self._recall_source(db,card['source']);available=True;blocked=None
                except CoreError as exc:available=False;blocked=exc.code
                data=dict(card_id=cid,kind=card['kind'],prompt=card['prompt'],due=card['state']['card']['due'],
                    eligible_after=card.get('eligible_after',card['state']['card']['due']),review_count=card['state']['review_count'],available=available,blocked_reason=blocked,source=card['source'])
                if section=='coach':data.update(answer=card['answer'],state=card['state'],review_note=card['review_note'],audience='coach_only')
                result.append(data)
            return dict(ok=True,revision=self.meta(db,'revision'),cards=result,next_offset=min(len(ids),offset+limit),has_more=offset+limit<len(ids),
                limits='FSRS 到期时间用于回忆安排，不表示解题能力或 EJU 分数。')

    def start_recall(self,session_id,expected_revision,request_id,card_id):
        from .store import stamp,today
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,request_id,'start_recall',dict(card_id=card_id),expected_revision,session_id)
            if replay:return replay
            card=self._recall_card(db,card_id);self._recall_source(db,card['source']);events=self.events(db)
            trials=[e for e in events if e['type']=='recall_trial' and e['card_id']==card_id]
            if trials and not any(e['type']=='recall_review' and e['trial_id']==trials[-1]['id'] for e in events):
                raise CoreError('recall_active','此项目已有未核对的一轮，先读取记录并接续，不能重开清除提示。',trial_id=trials[-1]['id'])
            if datetime.now(timezone.utc)<datetime.fromisoformat(card.get('eligible_after',card['state']['card']['due'])):
                raise CoreError('recall_not_due','还未到复习时间。需要讲解可读取内容，不提前伪造独立复测。')
            self.insert_event(db,dict(id=request_id,type='recall_trial',card_id=card_id,createdAt=stamp(),date=today(),sessionId=session_id),'native_recall')
            return self.finish_request(db,request_id,digest,trial_id=request_id,prompt=card['prompt'],learner_exposure_recorded=False)

    def record_recall_event(self,session_id,expected_revision,event):
        from .store import identifier,short_text,stamp,today
        if not isinstance(event,dict):raise CoreError('invalid_event','回忆事件必须是对象。')
        rid=identifier(event.get('id'));kind=event.get('type')
        if kind not in ('recall_exposure','recall_attempt') or any(k in event for k in ('createdAt','date','sessionId','hint_floor','seen_floor')):
            raise CoreError('invalid_event','回忆事件类型或字段无效。')
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,rid,'recall_event',event,expected_revision,session_id)
            if replay:return replay
            events=self.events(db);trial=next((e for e in events if e['id']==event.get('trial_id') and e['type']=='recall_trial'),None)
            if not trial:raise CoreError('trial_missing','没有这个复习轮次。')
            card=self._recall_card(db,trial['card_id'])
            if any(e['type']=='recall_review' and e['trial_id']==trial['id'] for e in events):raise CoreError('trial_closed','此轮已经核对，不能追加更早的曝光或作答。')
            e=dict(event,card_id=card['id'],createdAt=stamp(),date=today(),sessionId=session_id)
            if kind=='recall_exposure':
                if e.get('level') not in ('prompt','hint','answer') or e.get('source') not in ('codex_report','learner_report'):
                    raise CoreError('invalid_exposure','请说明展示类型与来源。')
            else:
                if any(x['type']=='recall_attempt' and x['trial_id']==trial['id'] for x in events):raise CoreError('attempt_exists','本轮已有首次回答，请保留它并进行核对。')
                short_text(e.get('answer'),'answer',10000,empty=True)
                if e.get('hint') not in ('H0','H1','H2','H3','H4') or type(e.get('answer_seen_before')) is not bool or type(e.get('guessed')) is not bool:
                    raise CoreError('invalid_evidence','需要明确提示等级、是否预先见到答案和是否猜测。')
                prior=[x for x in events if x['type']=='recall_exposure' and x['trial_id']==trial['id']]
                e['hint_floor']='H'+str(max([int(e['hint'][1:])]+[{'prompt':0,'hint':1,'answer':4}[x['level']] for x in prior]))
                e['seen_floor']=e['answer_seen_before'] or any(x['level']=='answer' for x in prior) or trial['sessionId']!=session_id
            self.insert_event(db,e,'native_recall')
            return self.finish_request(db,rid,digest,event_id=rid,card_id=card['id'])

    def review_recall_attempt(self,session_id,expected_revision,request_id,attempt_id,rating,result,note):
        from .recall import apply_review,RecallError
        from .store import short_text,stamp,today
        if result not in ('correct','incorrect','ungraded'):raise CoreError('invalid_result','结果为 correct、incorrect 或 ungraded。')
        short_text(note,'note',2000)
        if result=='incorrect' and rating!='Again':raise CoreError('rating_mismatch','未回忆正确不能使用成功等级。')
        payload=dict(attempt_id=attempt_id,rating=rating,result=result,note=note)
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,request_id,'recall_review',payload,expected_revision,session_id)
            if replay:return replay
            events=self.events(db);attempt=next((e for e in events if e['id']==attempt_id and e['type']=='recall_attempt'),None)
            if not attempt:raise CoreError('attempt_missing','首次回答尚未保存。')
            if any(e['type']=='recall_review' and e['attempt_id']==attempt_id for e in events):raise CoreError('attempt_reviewed','该作答已调度；不重复应用。')
            card=self._recall_card(db,attempt['card_id']);source_ok=True
            try:self._recall_source(db,card['source'])
            except CoreError:source_ok=False
            evidence=dict(kind=card['kind'],hint=attempt['hint_floor'],answer_seen_before=attempt['seen_floor'],guessed=attempt['guessed'],grading='verified' if result!='ungraded' and source_ok else 'ungraded')
            try:applied=apply_review(card['state'],rating,datetime.now(timezone.utc),evidence)
            except RecallError as exc:raise CoreError('invalid_recall_state',str(exc)) from exc
            eligible_after=applied['state']['card']['due'] if applied['applied'] else (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
            self.insert_event(db,dict(payload,id=request_id,type='recall_review',card_id=card['id'],trial_id=attempt['trial_id'],
                state=applied['state'],log=applied['log'],source_available=source_ok,eligible_after=eligible_after,createdAt=stamp(),date=today(),sessionId=session_id),'native_recall')
            return self.finish_request(db,request_id,digest,card_id=card['id'],applied=applied['applied'],reason=applied['reason'],due=applied['state']['card']['due'],eligible_after=eligible_after)

    @staticmethod
    def recall_summary(events):
        cards=[e for e in events if e['type']=='recall_card']
        closed={e['trial_id'] for e in events if e['type']=='recall_review'}
        open_trials=[dict(trial_id=e['id'],card_id=e['card_id']) for e in events if e['type']=='recall_trial' and e['id'] not in closed]
        return dict(card_count=len(cards),open_trials=open_trials[:10],open_trials_truncated=len(open_trials)>10,details_tool='read_recall_cards')
