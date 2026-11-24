(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.EJU = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCHEMA = 5;
  const TYPES = new Set(['attempt', 'exposure', 'issue', 'resolve', 'retract', 'worksheet', 'archive', 'reason_review']);
  const SUBJECTS = ['math1', 'math2', 'japanese_reading', 'japanese_writing', 'japanese_listening', 'japanese_listening_visual', 'physics', 'chemistry', 'biology', 'integrated'];
  function fail(message) { throw new Error(message); }
  function text(v, max = 20000) { return typeof v === 'string' && v.length <= max; }
  function dateOK(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
    const d = new Date(s + 'T12:00:00Z');
    return Number.isFinite(d.getTime()) && d.toISOString().slice(0, 10) === s;
  }
  function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  }
  function addDays(date, n) {
    if (!dateOK(date)) fail('日期无效，请使用真实的年月日。');
    const d = new Date(date + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function uid() { return (globalThis.crypto && globalThis.crypto.randomUUID) ? globalThis.crypto.randomUUID() : Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)+'-'+Math.random().toString(36).slice(2); }
  function clone(v) { return JSON.parse(JSON.stringify(v)); }
  function canonical(v) {
    if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
    if (v && typeof v === 'object') return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
    return JSON.stringify(v);
  }
  function cleanObject(v) {
    if (!v || typeof v !== 'object') return;
    for (const k of Object.keys(v)) {
      if (['__proto__','prototype','constructor'].includes(k)) fail('备份包含不支持的字段，未导入。');
      cleanObject(v[k]);
    }
  }
  function emptyState(date = today()) {
    return {schema: SCHEMA, id: uid(), revision: 0, profile: {subjects:['math1','japanese_reading'], minutes:15}, events:[], draft:{}, createdDate:date};
  }
  function validateEvent(e) {
    if (!e || typeof e !== 'object' || Array.isArray(e)) fail('记录事件格式无效。');
    if (!text(e.id,150) || !e.id || !TYPES.has(e.type) || !text(e.createdAt,40) || !Number.isFinite(Date.parse(e.createdAt))) fail('记录事件的标识、类型或时间无效。');
    if (!dateOK(e.date)) fail('记录事件的学习日期无效。');
    if (canonical(e).length > 100000) fail('单条记录过大，未导入。');
    if (['attempt','exposure','issue','resolve'].includes(e.type) && (!text(e.itemId,100) || !e.itemId)) fail('记录缺少题目ID。');
    if (e.type === 'attempt') {
      if (!Array.isArray(e.nodes) || e.nodes.length > 30 || e.nodes.some(n=>!text(n,100))) fail('作答知识点无效。');
      if (!['correct','incorrect','ungraded'].includes(e.result) || !['H0','H1','H2'].includes(e.hint)) fail('作答结果或提示级别无效。');
      if (![e.seenBefore,e.guessed].every(x=>typeof x==='boolean')) fail('作答缺少独立性条件。');
      if (!text(e.answer) || !text(e.reason) || !text(e.itemVersion,100) || !['automatic','self_report','external'].includes(e.grading)) fail('作答证据不完整。');
      if (e.seconds!==null && (!Number.isFinite(e.seconds) || e.seconds<0 || e.seconds>86400)) fail('作答用时无效。');
    }
    if (e.sessionId!==undefined && (!text(e.sessionId,150)||!e.sessionId)) fail('练习会话标识无效。');
    if (e.type==='exposure' && e.level!==undefined && !['stem','hint','answer'].includes(e.level)) fail('暴露类型无效。');
    if (e.type==='issue' && (!text(e.reason,5000) || !e.reason.trim())) fail('请说明题目哪里有问题。');
    if (e.type==='resolve' && (!text(e.issueId,150)||!e.issueId || !text(e.reference,5000)||!e.reference.trim() || !text(e.explanation,10000)||!e.explanation.trim() || !['confirmed_valid','corrected'].includes(e.outcome))) fail('纠正需要关联问题、核验来源和说明。');
    if (e.type==='retract' && (!text(e.targetId,150)||!text(e.reason,5000)||!e.reason.trim())) fail('撤销证据需要目标和原因。');
    if (e.type==='worksheet' && (!text(e.transcription,30000)||!text(e.feedback,30000)||typeof e.confirmed!=='boolean'||!Number.isInteger(e.firstError)||e.firstError < -1)) fail('步骤记录格式无效。');
    if (e.type==='reason_review' && (!text(e.targetId,150)||typeof e.agrees!=='boolean'||!text(e.note,5000)||!e.note.trim()||!['self','tutor'].includes(e.reviewer))) fail('理由核对需要关联作答、结论和来源说明。');
    if (e.type==='reason_review' && e.result!==undefined && !['correct','incorrect','ungraded'].includes(e.result)) fail('人工核对结果无效。');
    if (e.type==='archive' && (!text(e.content,50000)||!e.content.trim())) fail('聊天原文不能为空或超过50000字。');
    return e;
  }
  function validateState(s) {
    cleanObject(s);
    if (!s || s.schema!==SCHEMA || !text(s.id,150)||!s.id || !Number.isSafeInteger(s.revision)||s.revision<0) fail('这不是工作台v5备份。旧版聊天文字请用“保留聊天原文”。');
    if (!s.profile || !Array.isArray(s.profile.subjects)||!s.profile.subjects.length||s.profile.subjects.some(x=>!SUBJECTS.includes(x)) || !Number.isFinite(s.profile.minutes)||s.profile.minutes<3||s.profile.minutes>180) fail('备份中的科目或学习时长无效。');
    if (!Array.isArray(s.events)||s.events.length>25000) fail('备份事件列表无效或过大。');
    if (!s.draft || typeof s.draft!=='object' || Array.isArray(s.draft) || canonical(s.draft).length>200000) fail('备份草稿无效或过大。');
    if (s.draft.currentItem!==undefined && !text(s.draft.currentItem,100)) fail('当前题目草稿无效。');
    if (s.draft.worksheet!==undefined) {
      const w=s.draft.worksheet;
      if (!w || typeof w!=='object'||Array.isArray(w)||!text(w.itemId,100)||!text(w.transcription,30000)||!text(w.feedback,30000)||typeof w.confirmed!=='boolean'||!Number.isInteger(w.firstError)||w.firstError < -1) fail('手写工作区草稿无效。');
    }
    if (s.draft.practices!==undefined) {
      if (!s.draft.practices||typeof s.draft.practices!=='object'||Array.isArray(s.draft.practices)) fail('练习草稿无效。');
      for (const [id,p] of Object.entries(s.draft.practices)) {
        if(!text(id,100)||!p||!text(p.answer)||!text(p.reason)||!['H0','H1','H2'].includes(p.hint)||typeof p.seen!=='boolean'||typeof p.guessed!=='boolean'||!dateOK(p.date)) fail('练习草稿字段无效。');
        if(!p.timer||typeof p.timer.running!=='boolean'||!Number.isFinite(p.timer.elapsed)||p.timer.elapsed<0||p.timer.elapsed>86400||(p.timer.running&&(!Number.isFinite(p.timer.startedAt)||p.timer.startedAt<0))) fail('练习计时草稿无效。');
        if(p.submittedId!==undefined&&(!text(p.submittedId,150)||!s.events.some(e=>e.type==='attempt'&&e.id===p.submittedId&&e.itemId===id))) fail('练习草稿的已提交作答不存在。');
      }
    }
    if (!dateOK(s.createdDate)) fail('备份创建日期无效。');
    const ids=new Map();
    for(const e of s.events) {
      validateEvent(e);
      if(ids.has(e.id)) fail('备份内部有重复事件ID，未导入。');
      ids.set(e.id,e);
    }
    for(const e of s.events) {
      if(e.type==='resolve') {
        const issue=ids.get(e.issueId);
        if(!issue || issue.type!=='issue' || issue.itemId!==e.itemId) fail('纠正记录找不到对应问题，未导入。');
      }
      if(['retract','reason_review'].includes(e.type) && (!ids.has(e.targetId)||ids.get(e.targetId).type!=='attempt')) fail('撤销记录找不到对应作答，未导入。');
    }
    return s;
  }
  function append(s, e) {
    const next=clone(s);
    const item={id:uid(),createdAt:new Date().toISOString(),date:today(),...e};
    validateEvent(item);
    if(next.events.some(x=>x.id===item.id)) fail('事件重复，未保存。');
    next.events.push(item);next.revision++;
    validateState(next);return next;
  }
  function mergeBackup(s, incoming) {
    validateState(s);validateState(incoming);
    const map=new Map(s.events.map(e=>[e.id,e]));let added=0;
    for(const e of incoming.events) {
      if(map.has(e.id) && canonical(map.get(e.id))!==canonical(e)) fail(`事件 ${e.id} 在两个备份中内容冲突；没有合并或覆盖任何记录。`);
      if(!map.has(e.id)) { map.set(e.id,clone(e));added++; }
    }
    const next=clone(s);next.events=Array.from(map.values());
    // Empty workspaces restore complete settings/drafts; established workspaces keep their local settings.
    if(s.events.length===0 && Object.keys(s.draft).length===0 && s.revision===0) return {state:clone(incoming),added};
    if(added) next.revision++;
    validateState(next);return {state:next,added};
  }
  function parseBackup(raw) {
    if(!text(raw,12_000_000)) fail('备份超过12MB，未导入。');
    let doc;try {doc=JSON.parse(raw);}catch(e){fail('文件不是有效JSON，原记录未改变。');}
    if(doc.format!=='eju-workbench-backup'||!doc.state) fail('不是EJU工作台备份；请不要导入试卷或普通JSON。');
    return validateState(doc.state);
  }
  function backup(s) {validateState(s);return JSON.stringify({format:'eju-workbench-backup',version:'5.0',exportedAt:new Date().toISOString(),state:s},null,2);}
  function orderedEvents(s) { return s.events.slice().sort((a,b)=>a.date.localeCompare(b.date)||a.createdAt.localeCompare(b.createdAt)||a.id.localeCompare(b.id)); }
  function issues(s,itemId) {
    const all=orderedEvents(s), is=all.filter(e=>e.type==='issue'&&e.itemId===itemId);
    return is.map(e=>({issue:e,resolutions:all.filter(r=>r.type==='resolve'&&r.issueId===e.id)}));
  }
  function itemStatus(s,item) {
    if(issues(s,item.id).some(x=>!x.resolutions.length)) return 'disputed';
    const rs=orderedEvents(s).filter(e=>e.type==='resolve'&&e.itemId===item.id);
    if(rs.some(e=>e.outcome==='corrected')) return 'corrected_local';
    return item.status||'indexed';
  }
  function invalidReason(s,event,item) {
    if(s.events.some(e=>e.type==='retract'&&e.targetId===event.id)) return '已撤销，保留原证据';
    if(!item) return '题目不在当前内容版本中，保留待核';
    if(itemStatus(s,item)==='disputed') return '题目争议未解决，暂不计入';
    const corrections=s.events.filter(e=>e.type==='resolve'&&e.itemId===event.itemId&&e.outcome==='corrected');
    if(corrections.length) return '题目已有本地纠正，旧作答需在新核验题上重测';
    if(event.itemVersion!==item.version) return '题目版本已变化，待核对';
    if(!['reviewed_original','reviewed_official'].includes(item.status)) return '答案与解析尚未审完，不计能力证据';
    return '';
  }
  function exposed(s,id) {return s.events.some(e=>e.itemId===id&&['exposure','attempt'].includes(e.type));}
  function priorExposure(s,e,includeCurrentHints=true) {
    return s.events.some(x=>x.id!==e.id && x.itemId===e.itemId && ['exposure','attempt'].includes(x.type) && x.createdAt<=e.createdAt && !(x.type==='exposure' && (x.level==='stem'||(!includeCurrentHints&&x.level==='hint')) && e.sessionId && x.sessionId===e.sessionId && x.date===e.date));
  }
  function independent(e) {return e.type==='attempt'&&e.result==='correct'&&e.hint==='H0'&&!e.seenBefore&&!e.guessed&&Boolean(e.reason.trim());}
  function evidenceMap(s,items,nodes) {
    const byId=new Map(items.map(i=>[i.id,i]));
    const out=Object.fromEntries(nodes.map(n=>[n.id,{id:n.id,label:n.label,status:'untested',stage:0,due:null,lastDate:null,evidence:[],invalid:[],independent:[],progress:{}} ]));
    for(const e of orderedEvents(s).filter(e=>e.type==='attempt')) {
      const item=byId.get(e.itemId), invalid=invalidReason(s,e,item);
      // Only the current item's declared nodes can contribute; imported node labels cannot expand mastery.
      for(const id of (item?item.nodes:[])) {
        const row=out[id];if(!row)continue;
        if(invalid) {row.invalid.push({event:e.id,reason:invalid});continue;}
        row.evidence.push(e.id);row.lastDate=e.date;
        const reviews=orderedEvents(s).filter(x=>x.type==='reason_review'&&x.targetId===e.id);
        const lastReview=reviews[reviews.length-1],checked=Boolean(lastReview?.agrees);
        const effective={...e,result:lastReview&&!checked?'incorrect':e.result==='ungraded'?(lastReview?.result||'ungraded'):e.result};
        if(independent(effective)&&checked&&!priorExposure(s,e)) {
          const group=item.groupId||item.id, earlier=row.progress[group];
          row.independent.push({itemId:e.itemId,groupId:group,date:e.date,event:e.id});
          // Early practice is retained, but never moves the already scheduled interval.
          if(!earlier || (e.date>=earlier.due && e.date>earlier.date && e.itemId!==earlier.itemId)) {
            const stage=earlier?Math.min(5,earlier.stage+1):1;
            row.progress[group]={itemId:e.itemId,date:e.date,stage,due:addDays(e.date,[0,1,3,7,14,30][stage])};
            const progress=Object.values(row.progress);row.stage=Math.max(...progress.map(p=>p.stage));
            row.status=row.stage>=2?'delayed':'immediate';row.due=progress.map(p=>p.due).sort()[0];
          }
        } else if(effective.result==='incorrect'||(effective.result==='correct'&&(e.hint!=='H0'||e.guessed)&&!e.seenBefore&&!priorExposure(s,e,false))) {row.status='needs_repair';row.stage=0;row.due=addDays(e.date,1);row.independent=[];row.progress={};}
        else if(effective.result==='correct') {
          if(row.status==='untested'||row.status==='supported')row.status='supported';
          row.due=row.due||addDays(e.date,1);
        }
      }
    }
    return out;
  }
  function recommend(s,items,nodes,date=today()) {
    const map=evidenceMap(s,items,nodes);
    const eligible=items.filter(i=>i.kind==='original'&&s.profile.subjects.some(x=>i.subjects.includes(x))&&itemStatus(s,i)==='reviewed_original'&&!exposed(s,i.id));
    const scored=eligible.map(i=>({item:i,score:Math.max(...i.nodes.map(n=>{const r=map[n];return !r?0:r.status==='needs_repair'?4:r.due&&r.due<=date?3:r.status==='untested'?2:1;}),0)})).sort((a,b)=>b.score-a.score||a.item.order-b.item.order);
    const picked=[],used=new Set();
    for(const x of scored) {const key=x.item.groupId||x.item.nodes.join();if(used.has(key))continue;picked.push(x.item);used.add(key);if(picked.length>=Math.min(3,Math.max(1,Math.floor(s.profile.minutes/5))))break;}
    return picked;
  }
  function numeric(s) {
    if(typeof s!=='string')return null;
    s=s.normalize('NFKC').trim().replace(/−/g,'-').replace(/\s/g,'');
    if(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(s))return Number(s);
    const m=s.match(/^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\/([+-]?(?:\d+(?:\.\d*)?|\.\d+))$/);
    return m&&Number(m[2])!==0?Number(m[1])/Number(m[2]):null;
  }
  function grade(item,answer) {
    if(!item.grading || item.grading.mode==='manual')return 'ungraded';
    if(item.grading.mode==='choice')return answer.normalize('NFKC').trim()===String(item.grading.value)?'correct':'incorrect';
    const actual=numeric(answer), expected=Number(item.grading.value);
    if(actual===null||!Number.isFinite(expected))return 'ungraded';
    const tolerance=Number.isFinite(item.grading.tolerance)?Math.min(1e-4,Math.max(1e-10,item.grading.tolerance)):Math.max(1e-9,Math.abs(expected)*1e-8);
    return Math.abs(actual-expected)<=tolerance?'correct':'incorrect';
  }
  function chatExport(s,items,nodes) {
    const map=evidenceMap(s,items,nodes), byId=new Map(items.map(i=>[i.id,i]));
    const rows=Object.values(map).filter(r=>r.evidence.length||r.invalid.length);
    const lines=['# EJU学习记录 · 工作台v5',`记录ID ${s.id}；修订 ${s.revision}；导出日 ${today()}`,'以下是本地记录，含本人报告与自动数值对照；不是AI已验证本人推理。未出现的知识点默认未测。',`选科：${s.profile.subjects.join('、')}；每次预算：${s.profile.minutes}分钟。`,'\n## 目标证据'];
    for(const r of rows)lines.push(`- ${r.id} ${r.label}：${r.status}；阶段${r.stage}；到期${r.due||'未排'}；有效事件${r.evidence.join(',')||'无'}；排除${r.invalid.map(x=>x.event+'('+x.reason+')').join(',')||'无'}`);
    lines.push('\n## 全部原始事件（不可因摘要而丢弃独立条件）');
    for(const e of orderedEvents(s)) lines.push(JSON.stringify(e));
    lines.push('\n## 草稿（照片不含在备份中）',JSON.stringify(s.draft));
    lines.push('\n请先确认本轮日期与可用时间。沿用事件ID去重；争议或失效证据不得升级；数值答对但理由未核时先检查理由。只出当前一道未见题；我说停止时只登记已有变化，不新增任务。');
    return lines.join('\n');
  }
  function worksheetPrompt(w) {
    return ['请按EJU手写步骤反馈流程帮助我。','照片需要我在聊天中另行选择是否附上，本工作台没有上传任何图片。',`题目ID/定位：${w.itemId||'未提供，请先核对完整题面'}`,`我的转录已逐行确认：${w.confirmed?'是':'否，请先确认歧义，暂不判错'}`,'【题目与步骤转录】',w.transcription||'未提供',w.firstError>=0?`我怀疑第${w.firstError+1}行，请独立判断，不直接接受我的定位。`:'我尚未确定第一处错误。','【现有反馈/疑点】',w.feedback||'未提供','请先回读无法确定的符号，再找第一处真实错误。保留之前正确的步骤，把连锁错误和第一因分开；最多给两个修改点，先给H1方向，不替我重写全部答案。要求我修正关键一步，再用一道未见的同目标小题复验。不能把抄写修改当独立掌握。'].join('\n');
  }
  return {SCHEMA,SUBJECTS,today,dateOK,addDays,uid,clone,canonical,emptyState,validateState,append,mergeBackup,parseBackup,backup,orderedEvents,issues,itemStatus,invalidReason,exposed,priorExposure,independent,evidenceMap,recommend,numeric,grade,chatExport,worksheetPrompt};
});
