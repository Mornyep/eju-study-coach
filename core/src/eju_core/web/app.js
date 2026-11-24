'use strict';
const $ = id => document.getElementById(id);
const text = (id,value) => {$(id).textContent=value ?? '';};
const hidden = (id,value) => {$(id).hidden=value;};
const id = () => crypto.randomUUID();
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get('token') || sessionStorage.getItem('eju-workbench-token');
if(fragment.has('token')){sessionStorage.setItem('eju-workbench-token',token);history.replaceState(null,'',location.pathname);}
let session=null, revision=0, state=null, dirty=false, busy=false, timer=null, chain=Promise.resolve(), blocked=false;
const tabs=['study','review','writing','documents','records','setup'];
let queueCursor=0;
let reviewCursor=0, rawRecords=false, nextSelection=null, nextBasis=null, nextTaskId=null;
let nextFocusSubject=null, nextFocusOptions=[];
let recordCursor=0, currentDocument=null, docOffset=0, pageIndex=0, imageURL=null;
let requestRecovery=null, draftOwner=null, conflictingDraft=false;
let reflectionDirty=false, reflectionOwner=null, reflectionBaseId=null, reflectionConflict=false;
let writingContext=null, writingNew=null, writingItems=[], writingCursor=0, writingVersionCursor=0, writingDirty=false, writingPending=null, writingConflict=false, writingRemote=null;
let writingImageURLs=[], writingMediaReady=false;
let questionImageKey=null, questionImageURLs=[], mediaReady=false;
const input = () => ({answer:$('answer-input').value,reason:$('reason-input').value,guessed:$('guessed').checked});
function issue(error){if(error.code==='revision_conflict'&&requestRecovery?.method==='workbench_action'&&requestRecovery.params.action!=='reflect')conflictingDraft=true;blocked=true;hidden('error',false);text('error',error.message||'连接中断，输入还在当前页面，尚未确认保存。');hidden('recover',false);text('save-state',dirty?'输入未确认保存，请保持此页面打开。':'请重新连接后继续。');hidden('switch-task',error.code!=='task_changed');updateControls();}
function clearError(){hidden('draft-conflict',true);hidden('error',true);hidden('recover',true);hidden('switch-task',true);blocked=false;updateControls();}
async function call(method,params){
  let response;
  try{response=await fetch('/api/call',{method:'POST',headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({method,params})});}
  catch{throw new Error('本机连接中断，尚未确认操作结果。请保留页面并重新连接。');}
  let data;try{data=await response.json();}catch{throw new Error('服务未返回保存回执。请保持页面，重新连接后重试。');}
  if(!response.ok||!data.ok){const error=new Error(data.error?.message||'操作未完成。');error.code=data.error?.code;throw error;}
  if(data.revision!==undefined)revision=data.current_revision??data.revision;
  return data;
}
function enqueue(fn){chain=chain.then(async()=>{if(blocked)return;busy=true;updateControls();try{await fn();}catch(error){issue(error);}finally{busy=false;updateControls();}});return chain;}
async function action(kind,payload={}){
  if(!state?.task)throw new Error('还没有当前任务。');
  if(['show','hint','answer','submit'].includes(kind)&&!mediaReady)throw new Error('题目图片尚未加载完成，请稍候。');
  const params={session_id:session,expected_revision:revision,request_id:id(),task_id:state.task.id,action:kind,payload};
  requestRecovery={method:'workbench_action',params};
  const result=await call('workbench_action',params);requestRecovery=null;return result;
}
async function saveDraft(){
  clearTimeout(timer);if(!dirty||!state?.task||state.task.status==='completed')return;
  if(draftOwner && draftOwner!==state.task.id){const e=new Error('输入属于另一道题。请复制保留后读取新任务。');e.code='task_changed';throw e;}
  const captured=input();text('save-state','正在保存草稿…');
  await action('draft',captured);
  dirty=JSON.stringify(input())!==JSON.stringify(captured);if(!dirty)draftOwner=null;
  text('save-state',dirty?'还有新的输入待保存':'草稿已保存');
}
function render(keepInput=false){
  const task=state?.task;hidden('empty',!!task);hidden('task',!task);renderNextAvailability();if(!task)return;
  text('task-title',state.material.title);text('instruction',task.instruction);renderMaterial('stem',state.material.stem);
  text('task-status',(task.practice_mode==='review'?'已见题复习 · ':'')+{active:'进行中',paused:'已暂停',completed:'已提交'}[task.status]);
  if(!keepInput&&!dirty){$('answer-input').value=task.answer;$('reason-input').value=task.reason;$('guessed').checked=task.guessed;}
  const complete=task.status==='completed', paused=task.status==='paused';
  updateControls();hidden('resume',!paused);
  const exposed=task.exposure==='hint'||task.exposure==='answer';hidden('support',!exposed);
  text('support-title',task.exposure==='answer'?'解析':'提示');text('support-text',task.exposure==='answer'?state.solution:state.hint);
  renderReflection();
  renderNextAvailability();
  renderGuidance();
  hidden('support-text',!!state.coaching);
  text('answer-help',state.material.grading_mode==='choice'?`填写选项编号${state.material.choiceCount?'（1–'+state.material.choiceCount+'）':''}，把判断依据写在思路中。`:state.material.subjects?.includes('japanese_writing')?'记述由 Codex 按题目要求、主张与理由、结构和表达核对；这里不自动换算 EJU 分数。':'');
  text('feedback',complete?(state.attempt?.result==='correct'?'答案对照通过。你的思路仍留给 Codex 核对。':state.attempt?.result==='incorrect'?'作答已保存。可以回到 Codex，一起检查最早出现分歧的步骤。':'作答已保存，等待 Codex 核对。'):paused?'已经暂停。下次可以从这里继续。':'');
  if(!dirty)text('save-state',complete?'这次作答与思路已保存':'草稿已保存');
}
function updateControls(){
  updateWritingControls();
  const task=state?.task, locked=busy||blocked||!mediaReady||!task;
  const complete=task?.status==='completed', paused=task?.status==='paused';
  for(const field of ['answer-input','reason-input','guessed'])$(field).disabled=locked||complete||paused;
  for(const field of ['submit','pause'])$(field).disabled=locked||complete||paused;
  $('resume').disabled=locked;
  const nextLocked=busy||blocked||dirty||reflectionDirty||!!(task&&task.status!=='completed');
  $('next-subject').disabled=nextLocked;$('next-find').disabled=nextLocked;$('next-focus').disabled=nextLocked||(!nextFocusOptions.length&&!$('next-focus').value);
  for(const button of $('next-candidates').querySelectorAll('button'))button.disabled=nextLocked;
  for(const button of document.querySelectorAll('[data-review-start]'))button.disabled=nextLocked||button.dataset.unavailable==='true';
  for(const button of document.querySelectorAll('[data-adopt-focus]'))button.disabled=nextLocked||button.dataset.unavailable==='true';
  for(const button of document.querySelectorAll('[data-review-read]'))button.disabled=busy||blocked;
  for(const field of ['review-subject','review-checked','review-refresh','more-review'])$(field).disabled=busy||blocked;
  $('reflection-discard').disabled=busy||blocked;
  $('hint').disabled=locked||paused||!state?.material?.hint_available;
  $('answer').disabled=locked||paused;
  for(const field of ['reflection-cause','reflection-evidence','reflection-next','reflection-save'])$(field).disabled=locked||!complete||task?.exposure!=='answer'||reflectionConflict;
}
async function refresh(keepInput=false){
  mediaReady=false;updateControls();
  const incoming=await call('read_workbench',{session_id:session});
  if(dirty && draftOwner && (draftOwner!==incoming.task?.id || incoming.task?.status==='completed')){
    const e=new Error('当前任务已被另一会话更新。你的输入仍属于原题，请先复制保留，再读取新任务。');e.code='task_changed';throw e;
  }
  if(reflectionDirty && reflectionOwner!==incoming.task?.id){const e=new Error('复盘尚未保存且任务已变化。请复制保留后读取新任务。');e.code='task_changed';throw e;}
  state=incoming;render(keepInput);await loadQuestionImages();mediaReady=true;
  if(!state.task||state.task.status==='completed')await loadFocusOptions();
  updateControls();text('connection','已连接本机学习库');
}
async function loadQuestionImages(){
  const images=state?.material?.images||[];
  const key=JSON.stringify([state?.task?.itemId,images.map(x=>x.sha256)]);
  if(key===questionImageKey)return;
  closeImage();
  for(const url of questionImageURLs)URL.revokeObjectURL(url);
  questionImageURLs=[];questionImageKey=null;$('question-images').replaceChildren();
  const parts=[];
  try{
    for(const info of images){
      const query=new URLSearchParams({item_id:state.material.id,index:String(info.index),session_id:session});
      const response=await fetch('/api/question-image?'+query,{headers:{Authorization:'Bearer '+token}});
      if(!response.ok)throw new Error('题目图片未能加载，请恢复连接后再作答。');
      const url=URL.createObjectURL(await response.blob());questionImageURLs.push(url);
      const picture=document.createElement('img');picture.alt=info.alt;picture.src=url;await picture.decode();
      if(picture.naturalWidth!==info.width||picture.naturalHeight!==info.height)throw new Error('题目图片尺寸不符。');
      const figure=document.createElement('figure'), link=node('button',info.pdf_page?'放大查看原页':'放大查看配图','quiet');
      link.onclick=()=>openImage(url,info.alt);figure.append(picture,link);parts.push(figure);
    }
    $('question-images').append(...parts);questionImageKey=key;
  }catch(error){for(const url of questionImageURLs)URL.revokeObjectURL(url);questionImageURLs=[];$('question-images').replaceChildren();throw error;}
}
async function connect(recover=false){
  if(!token){issue(new Error('请通过本机工作台启动入口打开此页。'));return;}
  const oldTask=state?.task?.id;const preserved=dirty?input():null;
  let invalidatedStart=null;
  clearError();
  try{
    const r=await call('resume_learning',{});session=r.session_id;renderWritingResume(r.writing_resume);
    if(!state){const subject=r.workbench_task?.itemId?null:r.profile?.subjects?.find(x=>['japanese_reading','integrated','physics'].includes(x));if(subject)$('next-subject').value=subject;}
    if(recover&&requestRecovery){
      const pending=requestRecovery;
      try{await call(pending.method,{...pending.params,session_id:session});requestRecovery=null;if(['start_next_practice','start_review_practice'].includes(pending.method))showTab('study');if(pending.params.action==='reflect'&&JSON.stringify(reflectionInput())===JSON.stringify({cause:pending.params.payload.cause,evidence:pending.params.payload.evidence,next_action:pending.params.payload.next_action})){reflectionDirty=false;reflectionConflict=false;}}
      catch(e){
        if(['start_next_practice','start_review_practice'].includes(pending.method)&&e.code){
          requestRecovery=null;nextSelection=null;nextBasis=null;invalidatedStart=pending.method;
          $('next-candidates').replaceChildren();$('review-list').replaceChildren();hidden('coaching-context',true);
        }else{
          if(!['revision_conflict','reflection_conflict'].includes(e.code))throw e;
          if(pending.method==='workbench_action'&&pending.params.action!=='reflect')conflictingDraft=true;
          requestRecovery=null;
        }
      }
    }
    await refresh(!!preserved);
    if(recover&&(!state.task||state.task.status==='completed'))await loadFocusOptions(true);
    await setupStatus(!recover);
    if(invalidatedStart){text(invalidatedStart==='start_review_practice'?'review-state':'next-state','上次选择已失效。已读取当前状态，请刷新后重新选择。');if(invalidatedStart==='start_review_practice')await reviewQueue();}
    if(preserved){
      $('answer-input').value=preserved.answer;$('reason-input').value=preserved.reason;$('guessed').checked=preserved.guessed;dirty=true;
      if(oldTask!==state?.task?.id||state?.task?.status==='completed'){issue(new Error('任务已经变化。你的输入还保留在页面，请复制交给 Codex；未覆盖新任务。'));return;}
      const saved={answer:state.task.answer,reason:state.task.reason,guessed:state.task.guessed};
      if(conflictingDraft&&JSON.stringify(saved)!==JSON.stringify(preserved)){
        blocked=true;hidden('draft-conflict',false);hidden('recover',true);
        text('saved-draft',`答案：${saved.answer||'（空）'}\n思路：${saved.reason||'（空）'}\n含有猜测：${saved.guessed?'是':'否'}`);
        text('save-state','本页输入未覆盖学习库。请先选择要继续使用的草稿。');updateControls();return;
      }
      conflictingDraft=false;await saveDraft();
    }
    if(state?.task?.status==='active'){await action('show');await refresh();}
  }catch(e){issue(e);}
}
for(const field of ['answer-input','reason-input','guessed'])$(field).addEventListener('input',()=>{dirty=true;draftOwner=state?.task?.id;text('save-state','输入中，尚未保存…');clearTimeout(timer);timer=setTimeout(()=>enqueue(saveDraft),650);});
$('submit').onclick=()=>enqueue(async()=>{await saveDraft();await action('submit',input());dirty=false;await refresh();});
$('pause').onclick=()=>enqueue(async()=>{await saveDraft();await action('pause');await refresh();});
$('resume').onclick=()=>enqueue(async()=>{await action('resume');await refresh();await action('show');});
for(const kind of ['hint','answer'])$(kind).onclick=()=>enqueue(async()=>{await saveDraft();await action(kind);await refresh();});
$('refresh').onclick=()=>enqueue(async()=>{await saveDraft();await refresh();if(state?.task?.status==='active'){await action('show');await refresh();}else await loadFocusOptions(true);});
$('switch-task').onclick=()=>{conflictingDraft=false;clearTimeout(timer);dirty=false;draftOwner=null;requestRecovery=null;reflectionDirty=false;reflectionOwner=null;reflectionConflict=false;clearError();enqueue(()=>connect(true));};
$('keep-local-draft').onclick=()=>{clearError();conflictingDraft=false;enqueue(async()=>{await saveDraft();await refresh();});};
$('use-saved-draft').onclick=()=>{clearTimeout(timer);dirty=false;draftOwner=null;requestRecovery=null;conflictingDraft=false;clearError();enqueue(()=>refresh());};
$('recover').onclick=()=>{clearError();enqueue(()=>connect(true));};
window.addEventListener('beforeunload',e=>{if(dirty||reflectionDirty||requestRecovery||writingDirty||writingPending){e.preventDefault();e.returnValue='';}});
function renderMaterial(target,value){
  const host=$(target);host.replaceChildren();const lines=String(value??'').split('\n');
  for(let i=0;i<lines.length;){
    if(lines[i].trim().startsWith('|')&&lines[i].trim().endsWith('|')){
      const rows=[];while(i<lines.length&&lines[i].trim().startsWith('|')&&lines[i].trim().endsWith('|'))rows.push(lines[i++].trim().slice(1,-1).split('|').map(s=>s.trim()));
      const table=document.createElement('table');rows.filter(r=>!r.every(c=>/^:?-+:?$/.test(c))).forEach((r,index)=>{const row=document.createElement('tr');for(const cell of r){const el=node(index===0?'th':'td',cell);if(index===0)el.scope='col';row.append(el);}table.append(row);});host.append(table);
    }else{host.append(document.createTextNode(lines[i++]+(i<lines.length?'\n':'')));}
  }
}
function node(tag,value,cls){const n=document.createElement(tag);n.textContent=value??'';if(cls)n.className=cls;return n;}
async function rawRecordList(reset=true){
  if(reset){recordCursor=0;$('record-list').replaceChildren();}
  const r=await call('read_workbench_records',{session_id:session,after:recordCursor,limit:10});
  const list=r.records||[];
  for(const entry of list){const e=entry.event||entry;const n=node('div','','record');
    n.append(node('strong',({task:'安排练习',task_draft:'草稿',task_status:'任务状态',attempt:'作答',exposure:'题目或提示展示',document:'资料解析',document_review:'资料核对',plan:'接续安排',profile:'学习方向',reflection:'本人复盘（自述）'})[e.type]||'学习记录'));
    n.append(node('small',[e.date,e.itemId,e.status].filter(Boolean).join(' · ')));
    if(e.type==='reflection'){n.append(node('p','复盘方向：'+(causeLabels[e.cause]||e.cause)));n.append(node('p',e.evidence));}if(e.answer)n.append(node('p',e.answer));if(e.reason)n.append(node('p',e.reason));if(e.next_action)n.append(node('p',e.next_action));
    $('record-list').append(n);
  }
  if(reset&&!list.length)$('record-list').append(node('p','还没有学习记录。'));
  recordCursor=r.next_cursor;hidden('more-records',!r.has_more);
}
async function documents(){
  const r=await call('read_documents',{session_id:session});$('document-list').replaceChildren();
  for(const d of r.documents){const button=node('button','查看资料 · '+new Date(d.createdAt).toLocaleString(),'document-choice');button.onclick=()=>enqueue(()=>openDocument(d.id));$('document-list').append(button);}
  if(!r.documents.length)$('document-list').append(node('p','还没有解析资料。把 PDF 或照片交给 Codex，让它保存原件并按需解析。'));
  if(r.has_more)$('document-list').append(node('p','还有更多资料，可请 Codex 按资料名查找。'));
}
async function openDocument(documentId,more=false){
  const r=await call('read_documents',{session_id:session,document_id:documentId,offset:more?docOffset:0,limit:20});
  if(more)currentDocument.segments.push(...r.document.segments);else{currentDocument=r.document;pageIndex=0;}
  docOffset=r.next_offset;hidden('more-segments',!r.has_more);hidden('document-detail',false);
  text('segment-range',`已读取 ${currentDocument.segments.length} / ${r.segment_count} 个片段`);await showPage();
}
async function showPage(){
  const page=currentDocument.pages[pageIndex];text('page-label',`第 ${page.page_no} 页`);
  $('prev-page').disabled=pageIndex===0;$('next-page').disabled=pageIndex===currentDocument.pages.length-1;
  const response=await fetch('/api/asset?'+new URLSearchParams({id:page.artifact_id,session_id:session}),{headers:{Authorization:'Bearer '+token}});
  if(!response.ok)throw new Error('原页读取失败，不能用识别文字代替原图。');
  closeImage();if(imageURL)URL.revokeObjectURL(imageURL);imageURL=URL.createObjectURL(await response.blob());$('page-image').src=imageURL;$('page-open').onclick=()=>openImage(imageURL,`资料第 ${page.page_no} 页原图`);
  $('page-overlay').replaceChildren();$('page-overlay').setAttribute('viewBox',`0 0 ${page.width} ${page.height}`);
  $('page-image').alt=`资料第 ${page.page_no} 页原图`;$('segments').replaceChildren();
  for(const s of currentDocument.segments.filter(s=>s.page_no===page.page_no)){
    const n=node('div','','segment');n.append(node('strong',({unreviewed:'待核对',uncertain:'仍有疑义',confirmed:'转录已核对',corrected:'转录已更正'})[s.status]||s.status));
    n.append(node('p',s.text||'图形或笔画区域，请对照原页。'));const locate=node('button','查看原页区域');locate.onclick=()=>{for(const x of $('segments').children)x.classList.remove('selected');n.classList.add('selected');const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');rect.setAttribute('class','region-highlight');for(const [k,v] of Object.entries({x:s.bbox[0],y:s.bbox[1],width:s.bbox[2]-s.bbox[0],height:s.bbox[3]-s.bbox[1]}))rect.setAttribute(k,v);$('page-overlay').replaceChildren(rect);$('page-image').scrollIntoView({block:'center',behavior:'auto'});};n.append(locate);n.append(node('small',`原页区域：${s.bbox.map(x=>Math.round(x)).join(', ')}${s.note?' · '+s.note:''}`));$('segments').append(n);
  }
}
for(const tab of tabs)$('nav-'+tab).onclick=()=>enqueue(async()=>{await saveDraft();for(const x of tabs){hidden(x,x!==tab);$('nav-'+x).setAttribute('aria-pressed',String(x===tab));}if(tab==='review')await reviewQueue();if(tab==='writing')await writingList();if(tab==='documents')await documents();if(tab==='records')await records();if(tab==='setup')await setupStatus();hidden('next-practice',tab!=='study'||!!(state?.task&&state.task.status!=='completed'));});
$('more-records').onclick=()=>enqueue(()=>records(false));$('more-segments').onclick=()=>enqueue(()=>openDocument(currentDocument.id,true));
$('prev-page').onclick=()=>enqueue(async()=>{pageIndex--;await showPage();});$('next-page').onclick=()=>enqueue(async()=>{pageIndex++;await showPage();});
function openImage(url,alt){
  $('zoom-image').src=url;$('zoom-image').alt=alt;
  $('zoom-scroll').classList.remove('natural');text('zoom-size','原始大小');
  $('image-dialog').showModal();
}
function closeImage(){if($('image-dialog').open)$('image-dialog').close();$('zoom-image').removeAttribute('src');}
$('zoom-close').onclick=closeImage;
$('zoom-size').onclick=()=>{const natural=$('zoom-scroll').classList.toggle('natural');text('zoom-size',natural?'适合窗口':'原始大小');};
$('image-dialog').addEventListener('close',()=>{$('zoom-image').removeAttribute('src');});
async function setupStatus(showIfEmpty=false){
  const response=await fetch('/api/setup',{headers:{Authorization:'Bearer '+token}});
  const data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error?.message||'准备状态读取失败，请重新检查。');
  text('setup-vault',data.vault_readable?'学习库可读，工作台已连接。':'学习库未准备好。');
  text('setup-count',`已接入 ${data.official_ready} / ${data.official_registered} 道完整原题。当前小规模题库包含日语读解、综合科目与物理。`);
  $('setup-sources').replaceChildren();
  for(const source of data.sources){
    const url=new URL(source.url);if(url.protocol!=='https:'||url.hostname!=='www.jasso.go.jp')throw new Error('官方来源地址不匹配。');
    const labels={physics:'物理（理科 PDF）',integrated:'综合科目',japanese_reading:'日语读解',japanese_writing:'日语记述'};
    const line=node('p'), link=node('a',(source.subjects||[source.subject]).map(s=>labels[s]||s).join('、')+' · '+url.pathname.split('/').pop());
    link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';line.append(link,document.createTextNode(` — 已接入 ${source.ready} / ${source.registered} 道`));$('setup-sources').append(line);
  }
  if(showIfEmpty&&!state?.task&&data.official_ready===0){for(const x of tabs){hidden(x,x!=='setup');$('nav-'+x).setAttribute('aria-pressed',String(x==='setup'));}}
}
$('setup-refresh').onclick=()=>enqueue(()=>setupStatus());
$('import-pdf').onclick=async()=>{
  const file=$('official-pdf').files[0];
  if(!file){text('import-state','请先选择一份已下载的官方 PDF。');return;}
  if(!file.size||file.size>16*1024*1024){text('import-state','文件需要大于 0 且不超过 16 MiB。');return;}
  $('import-pdf').disabled=true;$('official-pdf').disabled=true;text('import-state','正在本机核对文件版本和完整原页…');
  try{
    const response=await fetch('/api/import-official',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/pdf'},body:file});
    const result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error?.message||'未收到导入完成回执，请重新检查状态后重试。');
    text('import-state',`已核对并接入 ${result.items_ready.length} 道原题。可以继续导入另一科，或回到“当前练习”查找原题。`);
    $('official-pdf').value='';await setupStatus();
  }catch(error){text('import-state',error.message||'导入未完成，请检查本地连接后重试。');}
  finally{$('import-pdf').disabled=false;$('official-pdf').disabled=false;}
};
function reflectionInput(){return {cause:$('reflection-cause').value,evidence:$('reflection-evidence').value,next_action:$('reflection-next').value};}
function renderReflection(){
  const available=state.task.status==='completed'&&state.task.exposure==='answer';hidden('reflection',!available);if(!available)return;
  const saved=state.reflection, latest=saved?.id??null;
  if(!reflectionDirty){
    $('reflection-cause').replaceChildren(new Option('请选择复盘方向',''));
    for(const option of state.reflection_options||[])$('reflection-cause').add(new Option(option.label,option.value));
    $('reflection-cause').value=saved?.cause||'';$('reflection-evidence').value=saved?.evidence||'';$('reflection-next').value=saved?.next_action||'';
    reflectionBaseId=latest;reflectionOwner=state.task.id;reflectionConflict=false;text('reflection-state',saved?'本人复盘已保存':'填写后点击保存；不会自动提交。');
  }else if(latest!==reflectionBaseId){
    reflectionConflict=true;text('saved-reflection',saved?`方向：${(state.reflection_options||[]).find(o=>o.value===saved.cause)?.label||saved.cause}\n依据：${saved.evidence}\n下一步：${saved.next_action}`:'尚无已保存复盘');
  }
  hidden('reflection-conflict',!reflectionConflict);hidden('reflection-discard',!reflectionDirty);
}
async function saveReflection(){
  if(!reflectionDirty)return;
  const value=reflectionInput();
  if(!value.cause||!value.evidence.trim()){text('reflection-state','请选择复盘方向，并写下依据或仍不确定的地方。');return;}
  try{await action('reflect',{...value,base_reflection_id:reflectionBaseId});}
  catch(error){if(['invalid_field','reflection_not_ready'].includes(error.code)){requestRecovery=null;text('reflection-state',error.message);return;}throw error;}
  reflectionDirty=false;reflectionConflict=false;await refresh();
}
for(const field of ['reflection-cause','reflection-evidence','reflection-next'])$(field).addEventListener('input',()=>{reflectionDirty=true;reflectionOwner=state?.task?.id;text('reflection-state','复盘尚未保存。');renderNextAvailability();updateControls();});
$('reflection-save').onclick=()=>enqueue(saveReflection);
$('keep-local-reflection').onclick=()=>{reflectionBaseId=state.reflection?.id??null;reflectionConflict=false;clearError();enqueue(saveReflection);};
$('use-saved-reflection').onclick=()=>{reflectionDirty=false;reflectionConflict=false;requestRecovery=null;clearError();enqueue(()=>refresh());};
function renderGuidance(){
  const guide=state.coaching, host=$('study-guidance');host.replaceChildren();hidden('study-guidance',!guide);if(!guide)return;
  for(const [key,label] of [['questionFocus','先看问什么'],['evidence','解题依据'],['knowledge','相关知识'],['optionAnalysis','选项核对'],['nextPractice','下一步练习']]){
    if(!guide[key])continue;host.append(node('h3',label));const values=Array.isArray(guide[key])?guide[key]:[guide[key]];
    for(const value of values)host.append(node('p',typeof value==='string'?value:(value.choice?`选项 ${value.choice} · ${value.correct?'符合':'不符合'}：${value.reason}`:[value.pdfPage?'PDF 第 '+value.pdfPage+' 页':'',value.locator,value.summary].filter(Boolean).join(' · '))));
  }
}

const causeLabels={question_direction:'题目问的方向',not_sure:'还不确定',independent_ok:'目前能自己说明依据',reference:'指代对象',relation:'句段关系',scope:'选项范围或程度',evidence:'原文或解题依据',vocabulary:'词语理解',knowledge:'知识点',data_reading:'图表读取',time_place:'时间或地点',calculation:'计算',elimination:'选项排除',task_requirements:'题目要求',argument_support:'主张与理由',structure:'文章结构',expression:'表达'};
function renderNextAvailability(){
  const task=state?.task, allowed=!task||task.status==='completed';
  hidden('next-practice',$('study').hidden||!allowed);
  hidden('reflection-discard',!reflectionDirty);
  if(task?.id!==nextTaskId){
    nextTaskId=task?.id??null;nextSelection=null;nextBasis=null;$('next-candidates').replaceChildren();text('next-state','');
    const subject=state?.material?.subjects?.find(x=>['japanese_reading','integrated','physics'].includes(x));
    if(subject)$('next-subject').value=subject;
  }
  if(nextSelection?.basis_attempt_id&&nextSelection.basis_attempt_id===task?.attempt_id&&nextSelection.basis_reflection_id!==(state?.reflection?.id??null)){
    nextSelection=null;$('next-candidates').replaceChildren();text('next-state','复盘已更新，请按最新记录重新查找下一题。');
  }
  if(reflectionDirty){nextSelection=null;$('next-candidates').replaceChildren();text('next-state','先保存本页复盘，或明确撤销未保存的修改，再选择下一题。');}
  else if($('next-state').textContent.startsWith('先保存本页复盘'))text('next-state','复盘已保存，可以查找下一题。');
}
$('reflection-discard').onclick=()=>enqueue(async()=>{reflectionDirty=false;reflectionConflict=false;requestRecovery=null;await refresh();text('next-state','已撤销未保存的复盘修改。');});
function clearNextPreview(message=''){
  nextSelection=null;$('next-candidates').replaceChildren();text('next-state',message);
}
function describeFocus(){
  const selected=nextFocusOptions.find(x=>x.id===$('next-focus').value);
  text('focus-description',selected?selected.description+' 这是你本次选择的练习方向，不代表已经诊断出弱项。':$('next-focus').value?'此训练重点已不可用，请重新选择。':nextFocusOptions.length?'可按需要选择训练重点；不选择时，在本科目的未见原题中查找。':'本科目暂未提供训练重点，可查找未见原题。');
}
function applyFocusOptions(result,subject){
  const previous=nextFocusSubject===subject?$('next-focus').value:'';
  nextFocusSubject=subject;nextFocusOptions=result.focus_options||[];
  $('next-focus').replaceChildren(new Option('不限训练重点',''));
  for(const focus of nextFocusOptions)$('next-focus').add(new Option(focus.label,focus.id));
  if(previous&&!nextFocusOptions.some(x=>x.id===previous))$('next-focus').add(new Option('原选择已不可用，请重新选择',previous));
  $('next-focus').value=previous;describeFocus();
}
async function loadFocusOptions(force=false){
  const subject=$('next-subject').value;
  if(!force&&nextFocusSubject===subject)return;
  const result=await call('get_next_practice',{session_id:session,subject});
  applyFocusOptions(result,subject);
  return result;
}
$('next-subject').onchange=()=>{nextBasis=null;clearNextPreview('只改变本次练习科目，长期选科不变。');$('next-focus').value='';enqueue(()=>loadFocusOptions());};
$('next-focus').onchange=()=>{clearNextPreview('训练重点已改变，请重新查找下一题。');describeFocus();};
async function findNext(expectedFocusVersion=null,expectedBasisRevision=null){
  if(reflectionDirty){text('next-state','请先保存或撤销本页复盘修改。');return;}
  const subject=$('next-subject').value;
  await loadFocusOptions();
  const basis=nextBasis||(state?.material?.subjects?.includes(subject)?state?.task?.attempt_id:null)||null;
  nextSelection=await call('get_next_practice',{session_id:session,subject,basis_attempt_id:basis,focus_id:$('next-focus').value||null});
  if(expectedBasisRevision!==null&&nextSelection.revision!==expectedBasisRevision){clearNextPreview('学习记录已变化，请回到讲解重新确认建议。');return false;}
  applyFocusOptions(nextSelection,subject);
  if(expectedFocusVersion&&nextSelection.focus_version!==expectedFocusVersion){clearNextPreview('练习方向资料已变化，请回到讲解重新确认建议。');return false;}
  $('next-candidates').replaceChildren();
  const source=nextSelection.basis;
  const focus=nextSelection.selected_focus;
  text('next-state',nextSelection.no_candidate?nextSelection.message:focus?`本次训练重点：${focus.label}。以下原题已按题目要求核对过这一练习方向；它不代表对你弱项的诊断。`:source?`参考「${source.title}」涉及的知识点${source.reflection_cause_label?'；你保存的复盘方向是「'+source.reflection_cause_label+'」':''}。推荐尚未证明与复盘原因精确对应。`:'以下是可用的未见原题。查看候选不会记录题目曝光。');
  for(const candidate of nextSelection.candidates||[]){
    const row=node('article','','next-candidate');row.append(node('h3',candidate.title));
    const relation=candidate.relation==='shared_nodes'?'与你上题涉及的知识点相关':'同科练习';
    const labels=(candidate.shared_nodes||[]).map(x=>x.label).join('、');
    row.append(node('p',relation+(labels?' · '+labels:''),'review-result'));
    row.append(node('p',candidate.reason));
    const button=node('button','开始这道原题','primary');button.onclick=()=>enqueue(()=>startNext(candidate));row.append(button);$('next-candidates').append(row);
  }
  return true;
}
async function startNext(candidate){
  if(!nextSelection||reflectionDirty)throw new Error('练习依据已变化，请重新查找下一题。');
  const params={session_id:session,expected_revision:nextSelection.revision,request_id:id(),item_id:candidate.item_id,item_version:candidate.item_version,subject:nextSelection.subject,base_task_id:nextSelection.base_task_id,basis_attempt_id:nextSelection.basis_attempt_id,basis_reflection_id:nextSelection.basis_reflection_id};
  if(nextSelection.selected_focus){params.focus_id=nextSelection.selected_focus.id;params.expected_focus_version=nextSelection.focus_version;}
  requestRecovery={method:'start_next_practice',params};
  try{await call('start_next_practice',params);requestRecovery=null;}
  catch(error){
    // Deterministic rejections have made no write. Never replay an obsolete choice on reconnect.
    if(error.code){requestRecovery=null;nextSelection=null;nextBasis=null;$('next-candidates').replaceChildren();text('next-state','状态或候选已经变化，请重新连接并查找。');}
    throw error;
  }
  nextSelection=null;nextBasis=null;reflectionDirty=false;reflectionOwner=null;reflectionConflict=false;
  await refresh();await action('show');await refresh();$('task-title').scrollIntoView({block:'start'});
}
$('next-find').onclick=()=>enqueue(findNext);
async function records(reset=true){
  text('raw-records',rawRecords?'返回作答与复盘':'查看原始事件');
  $('record-subject').disabled=rawRecords;
  text('record-mode',rawRecords?'全部科目的原始事件按保存顺序展示，包含草稿和展示回执。':'最近作答在前；本人复盘与理由核对分别呈现。');
  text('more-records',rawRecords?'读取后续事件':'读取更早的记录');
  if(rawRecords)return rawRecordList(reset);
  if(reset){reviewCursor=0;$('record-list').replaceChildren();}
  const result=await call('read_study_review',{session_id:session,subject:$('record-subject').value||null,before:reviewCursor,limit:10});
  if($('record-subject').options.length===1)for(const [value,label] of Object.entries(result.subjects||{}))$('record-subject').add(new Option(label,value));
  for(const review of result.reviews||[]){
    const row=node('article','','record');row.append(node('h2',review.title));
    row.append(node('small',[review.date,...(review.subjects||[]).map(x=>result.subjects?.[x]||x)].filter(Boolean).join(' · ')));
    row.append(node('p',({correct:'答案对照通过，仍需核对思路',incorrect:'答案对照未通过',ungraded:'作答已保存，待核对'}[review.result]||'作答已保存'),'review-result'));
    row.append(node('p',[review.hint&&review.hint!=='H0'?'使用过提示或解析':'无提示记录',review.seen_before?'有先前接触记录':'未标记先前接触',review.guessed?'本人标记含猜测':''].filter(Boolean).join(' · '),'muted'));
    if(review.invalid_reason)row.append(node('p','证据状态：'+review.invalid_reason));
    const details=node('details');details.append(node('summary','查看当时的回答与思路'));
    details.append(node('p','回答：'+(review.answer||'（空）')),node('p','思路：'+(review.reason||'（未填写）')));
    if(review.text_truncated)details.append(node('p','此处显示摘要，完整内容可请 Codex 按作答记录 ID 读取。','muted'));
    details.append(node('small','作答记录 ID：'+review.attempt_id));row.append(details);
    const reflection=review.reflection, parts=node('div','','review-parts');parts.append(node('h3','本人复盘 · 自述'));
    if(reflection){parts.append(node('p',reflection.cause_label||causeLabels[reflection.cause]||reflection.cause),node('p',reflection.evidence));if(reflection.next_action)parts.append(node('p','下次准备：'+reflection.next_action));}
    else parts.append(node('p','尚未保存本人复盘。'));
    parts.append(node('h3','理由核对 · 单独记录'));
    const checked=review.reason_review;
    if(checked){parts.append(node('p',(checked.reviewer==='self'?'本人核对':'辅导核对')+' · '+(checked.agrees?'记录为认可':'记录为仍有分歧')),node('p',checked.note));if(checked.note_truncated)parts.append(node('p','核对说明较长，这里仅显示摘要。','muted'));}
    else parts.append(node('p','尚无已保存的理由核对，答案对照和本人复盘不能替代它。'));
    row.append(parts);
    const contextButton=node('button','查看讲解与接续','quiet');contextButton.dataset.reviewRead='true';contextButton.onclick=()=>enqueue(async()=>{showTab('review');await reviewQueue();await coachingContext(review.attempt_id);});row.append(contextButton);
    const subject=review.subjects?.find(x=>['japanese_reading','integrated','physics'].includes(x));
    if(subject&&!review.invalid_reason){
      const next=node('button','参考这次作答查找下一题','quiet');
      next.dataset.reviewStart='true';next.disabled=!!(state?.task&&state.task.status!=='completed')||reflectionDirty;
      next.onclick=()=>enqueue(async()=>{nextBasis=review.attempt_id;$('next-subject').value=subject;for(const tab of tabs){hidden(tab,tab!=='study');$('nav-'+tab).setAttribute('aria-pressed',String(tab==='study'));}renderNextAvailability();nextBasis=review.attempt_id;await findNext();$('next-title').scrollIntoView({block:'start'});});
      row.append(next);
    }
    $('record-list').append(row);
  }
  if(reset&&!result.reviews?.length)$('record-list').append(node('p','这个范围内还没有已提交的作答。草稿可以在当前练习继续。'));
  reviewCursor=result.next_before;hidden('more-records',!result.has_more);
}
$('raw-records').onclick=()=>enqueue(async()=>{rawRecords=!rawRecords;await records();});
$('record-subject').onchange=()=>enqueue(()=>records());
function showTab(tab){for(const x of tabs){hidden(x,x!==tab);$('nav-'+x).setAttribute('aria-pressed',String(x===tab));}renderNextAvailability();}
function appendCoaching(host,note,historical=false){
  if(!note){host.append(node('p','尚无已保存的讲解摘要。'));return;}
  if(historical)host.append(node('p','这是上一轮作答的讲解，供回顾；不代表本次作答已核对。','muted'));
  if(note.stale)host.append(node('p',note.stale_reason||'依据已变化，需要重新确认这份讲解。','review-warning'));
  host.append(node('small',[note.date,'辅导摘要'].filter(Boolean).join(' · ')),node('p',note.summary));
  const assessment=note.reasoning_assessment;
  if(assessment){
    host.append(node('h4','对这段思路的反馈'),node('p',({supported:'辅导核对：这段理由有依据',needs_revision:'辅导核对：这段理由需要修正',needs_clarification:'还需要你补充一点思路'}[assessment.status]||'已保存的理由反馈')));
    if(assessment.reason_quote)host.append(node('blockquote',assessment.reason_quote,'reason-quote'));
    if(assessment.error_stage&&assessment.error_stage!=='none'&&assessment.error_stage!=='unknown')host.append(node('p','本次先核对：'+({question_reading:'题目问的方向',knowledge:'使用的知识',method:'推理方法',calculation:'计算步骤',graph:'图表读取'}[assessment.error_stage]||'待确认的步骤')));
  }
  if(note.hypothesis)host.append(node('p','辅导推测（待验证）：'+note.hypothesis));
  if(note.open_question)host.append(node('p','仍待确认：'+note.open_question));
  if(note.next_step)host.append(node('p','下一步：'+note.next_step));
  if(historical&&note.practice_recommendation)host.append(node('p','当时建议的方向：'+note.practice_recommendation.label+'。请从最新作答重新核对。','muted'));
}
async function coachingContext(attemptId){
  const result=await call('read_coaching_context',{session_id:session,attempt_id:attemptId});
  renderCoachingContext(result.context);
}
function recommendationKey(context){
  return JSON.stringify([context.attempt.attempt_id,context.base_note_id,context.basis_reflection_id,context.basis_reason_review_id,context.coaching_note?.practice_recommendation]);
}
function renderCoachingContext(context){
  const attempt=context.attempt, host=$('context-body');host.replaceChildren();
  text('context-title',attempt.title+' · 作答与讲解');hidden('coaching-context',false);
  host.append(node('small','作答记录 ID：'+attempt.attempt_id));
  if(attempt.invalid_reason)host.append(node('p','证据状态：'+attempt.invalid_reason,'review-warning'));
  host.append(node('h3','这次回答与思路'),node('p','回答：'+(attempt.answer||'（空）')),node('p','思路：'+(attempt.reason||'（未填写）')));
  if(attempt.text_truncated)host.append(node('p','较长内容仅显示摘要；完整记录可请 Codex 按作答 ID 读取。','muted'));
  const reflection=attempt.reflection, reason=attempt.reason_review;
  host.append(node('h3','本人复盘 · 自述'));
  host.append(node('p',reflection?[reflection.cause_label||causeLabels[reflection.cause]||reflection.cause,reflection.evidence,reflection.next_action?'下次准备：'+reflection.next_action:''].filter(Boolean).join('\n'):'尚未保存本人复盘。'));
  host.append(node('h3','理由核对 · 单独记录'),node('p',reason?((reason.reviewer==='self'?'本人核对':'辅导核对')+' · '+(reason.agrees?'记录为认可':'记录为仍有分歧')+'\n'+reason.note):'尚无已保存的理由核对。'));
  host.append(node('h3','这次的讲解与接续'));appendCoaching(host,context.coaching_note);
  const recommendation=context.coaching_note?.practice_recommendation;
  if(recommendation){
    const card=node('section','','practice-recommendation');card.append(node('h4','可以接着练：'+recommendation.label),node('p',recommendation.description));
    const available=recommendation.available&&!context.coaching_note.stale&&context.review_available;
    card.append(node('p',available?'采用后会查找这个方向的未见原题，再由你选择是否开始。':recommendation.reason||'这条建议的依据已变化，请先重新核对。',available?'muted':'review-warning'));
    const adopt=node('button','采用这个方向查找原题','primary');adopt.dataset.adoptFocus='true';adopt.dataset.unavailable=String(!available);
    adopt.onclick=()=>enqueue(()=>adoptRecommendation(context));card.append(adopt);host.append(card);
  }
  if(context.previous_coaching_note){const previous=node('details');previous.append(node('summary','回看上一轮作答的讲解'));appendCoaching(previous,context.previous_coaching_note,true);host.append(previous);}
  if(!context.review_available)host.append(node('p',context.review_unavailable_reason,'muted'));
  updateControls();
  $('coaching-context').scrollIntoView({block:'start'});
}
async function adoptRecommendation(shown){
  if(dirty||reflectionDirty)return;
  await refresh();
  const freshResult=await call('read_coaching_context',{session_id:session,attempt_id:shown.attempt.attempt_id});
  const fresh=freshResult.context;
  const recommendation=fresh.coaching_note?.practice_recommendation;
  if(state?.task&&state.task.status!=='completed'){
    renderCoachingContext(fresh);$('context-body').append(node('p','先完成或继续当前练习，再采用这个方向。','review-warning'));return;
  }
  if(!recommendation?.available||fresh.coaching_note.stale||!fresh.review_available||recommendationKey(fresh)!==recommendationKey(shown)){
    renderCoachingContext(fresh);$('context-body').append(node('p','讲解或依据已经变化。已显示最新内容，请重新确认。','review-warning'));return;
  }
  $('next-subject').value=recommendation.subject;
  const options=await loadFocusOptions(true);
  if(options.revision!==freshResult.revision){
    clearNextPreview();nextBasis=null;$('next-focus').value='';describeFocus();await coachingContext(fresh.attempt.attempt_id);$('context-body').append(node('p','学习记录已变化，已重新读取讲解，请确认后再采用。','review-warning'));return;
  }
  if(options.focus_version!==recommendation.version||!nextFocusOptions.some(option=>option.id===recommendation.id)){
    clearNextPreview();$('next-focus').value='';describeFocus();renderCoachingContext(fresh);$('context-body').append(node('p','练习方向资料已变化，请重新核对这条建议。','review-warning'));return;
  }
  $('next-focus').value=recommendation.id;describeFocus();nextBasis=fresh.attempt.attempt_id;
  const found=await findNext(recommendation.version,freshResult.revision);
  if(!found){nextBasis=null;$('next-focus').value='';describeFocus();await coachingContext(fresh.attempt.attempt_id);$('context-body').append(node('p','学习记录或练习方向已变化，已重新读取讲解，请确认后再采用。','review-warning'));return;}
  showTab('study');$('next-title').scrollIntoView({block:'start'});
}
async function reviewQueue(reset=true){
  if(reset){queueCursor=0;$('review-list').replaceChildren();hidden('coaching-context',true);}
  const result=await call('read_review_queue',{session_id:session,subject:$('review-subject').value||null,before:queueCursor,limit:10,include_checked:$('review-checked').checked});
  if($('review-subject').options.length===1)for(const [value,label] of Object.entries(result.subjects||{}))$('review-subject').add(new Option(label,value));
  text('review-counts',`待核对 ${result.pending_count} · 本次理由已核对 ${result.checked_count} · 暂不可重做 ${result.unavailable_count}`);
  text('review-state',state?.task&&state.task.status!=='completed'?'当前有未完成或暂停的练习。可以查看记录，完成当前练习后再重做。':reflectionDirty?'请先保存或撤销当前练习中未保存的复盘。':'按最近作答排序；只显示每道题的最新一次作答。');
  for(const entry of result.entries||[]){
    const row=node('article','','record');row.append(node('h2',entry.title),node('small',[entry.date,...entry.subjects.map(x=>result.subjects[x]||x)].join(' · ')));
    row.append(node('p',{pending:'待核对',checked:'本次理由已核对',unavailable:'暂不可重做'}[entry.status],'review-result'));
    for(const reason of entry.reasons)row.append(node('p',reason.label));
    row.append(node('p',[entry.seen_before?'有先前接触记录':'未标记先前接触',entry.hint!=='H0'?'使用过提示或解析':'无提示记录',entry.guessed?'本人标记含猜测':''].filter(Boolean).join(' · '),'muted'));
    if(entry.coaching_note){const brief=node('div','','coaching-brief');brief.append(node('h3','上次保存的接续'));if(entry.coaching_note.stale)brief.append(node('p','依据已变化，讲解待重新确认。','review-warning'));brief.append(node('p',entry.coaching_note.next_step));if(entry.coaching_note.open_question)brief.append(node('p','仍待确认：'+entry.coaching_note.open_question));row.append(brief);}
    const actions=node('div','','action-row'), read=node('button','查看作答与讲解','quiet');read.dataset.reviewRead='true';read.onclick=()=>enqueue(()=>coachingContext(entry.attempt_id));actions.append(read);
    const start=node('button','重做这道旧题');start.dataset.reviewStart='true';start.dataset.unavailable=String(!entry.review_available);start.onclick=()=>enqueue(()=>startReview(entry,result));actions.append(start);row.append(actions);$('review-list').append(row);
  }
  if(reset&&!result.entries?.length)$('review-list').append(node('p','这个范围内没有待复习记录。你可以显示理由已核对的作答，或完成当前练习后再查看。'));
  queueCursor=result.next_before;hidden('more-review',!result.has_more);updateControls();
}
async function startReview(entry,selection){
  if(dirty||reflectionDirty)throw new Error('请先保存本页输入和复盘，再选择重做。');
  const params={session_id:session,expected_revision:selection.revision,request_id:id(),source_attempt_id:entry.attempt_id,item_version:entry.item_version,base_task_id:selection.base_task_id,base_note_id:entry.base_note_id,basis_reflection_id:entry.basis_reflection_id,basis_reason_review_id:entry.basis_reason_review_id};
  requestRecovery={method:'start_review_practice',params};
  try{await call('start_review_practice',params);requestRecovery=null;}
  catch(error){if(error.code){requestRecovery=null;$('review-list').replaceChildren();hidden('coaching-context',true);text('review-state','来源或当前任务已变化，请重新连接后刷新复习记录。');}throw error;}
  reflectionDirty=false;reflectionOwner=null;reflectionConflict=false;showTab('study');
  await refresh();await action('show');await refresh();$('task-title').scrollIntoView({block:'start'});
}
$('review-refresh').onclick=()=>enqueue(()=>reviewQueue());
$('review-subject').onchange=()=>enqueue(()=>reviewQueue());
$('review-checked').onchange=()=>enqueue(()=>reviewQueue());
$('more-review').onclick=()=>enqueue(()=>reviewQueue(false));
enqueue(()=>connect());

function updateWritingControls(){
  const lock=busy||blocked;
  for(const name of ['writing-refresh','writing-more','writing-new','writing-item','writing-more-versions'])$(name).disabled=lock;
  $('writing-text').disabled=lock||!!writingPending||!!(writingContext&&!writingContext.source_available);
  $('writing-save').disabled=lock||!writingMediaReady||!!writingPending||writingConflict||(!writingDirty&&!!writingContext)||!!(writingContext&&!writingContext.source_available);
  $('writing-image-retry').disabled=lock||!!writingPending;
  $('writing-new').disabled=lock||!writingItems.length;
  $('writing-discard').disabled=lock||!!writingPending;
  $('writing-rebase').disabled=lock||!writingRemote?.source_available;
  $('writing-retry').disabled=busy;
  for(const b of document.querySelectorAll('[data-writing-open],[data-writing-version]'))b.disabled=lock;
}
function writingNotice(message){text('writing-state',message);}
function writingLength(){const count=Array.from($('writing-text').value).length;text('writing-length',`${count} 字符 · 按原样保存，最多 8000 字符；这不是官方计分。`);}
function writingGuard(){
  if(writingPending){writingNotice('上次保存结果尚未确认，请先核对回执。');return false;}
  if(writingDirty){writingNotice('当前文字尚未保存。请先保存，或明确放弃本页修改。');return false;}
  return true;
}
function renderWritingResume(summary){
  const host=$('writing-recent');host.replaceChildren();
  const labels={empty_draft:'空稿 · 可以继续写',awaiting_feedback:'这版尚无反馈',feedback_available:'这版有已保存反馈',source_unavailable:'来源暂不可用 · 可查看历史'};
  for(const draft of summary?.drafts||[]){
    const row=node('article','','writing-resume-card');
    row.append(node('h3',draft.title),node('p',(draft.kind==='official'?'官方原题':'原创辅助练习')+' · '+(labels[draft.state]||'已保存'),'muted'),node('p',draft.latest_excerpt||'（空稿）'),node('p',`${draft.version_count} 个版本 · ${draft.last_activity_at||''}`,'muted'));
    const button=node('button','继续这篇','quiet');button.dataset.writingOpen='true';
    button.onclick=()=>enqueue(async()=>{if(writingGuard())await openWriting(draft.draft_id);});row.append(button);host.append(row);
  }
  if(!(summary?.drafts||[]).length)host.append(node('p','还没有保存过记述草稿。选择下方题目，写下自己的第一版。','muted'));
  if(summary?.has_more)host.append(node('p','这里显示最近活动的草稿；其他稿子可在下方查找。','muted'));
}
async function writingList(reset=true){
  if(reset)renderWritingResume(await call('read_writing_resume',{session_id:session,limit:3}));
  const r=await call('read_writing_drafts',{session_id:session,before:reset?0:writingCursor,limit:10});
  if(reset){$('writing-list').replaceChildren();writingItems=r.available_items||[];const selected=$('writing-item').value;$('writing-item').replaceChildren();for(const item of writingItems)$('writing-item').add(new Option(item.kind==='official'?'官方原题 · '+item.title:'原创辅助 · '+({transfer:'完整记述',delayed:'完整记述',foundation:'写前准备',contrast:'审题核对'}[item.role]||'记述练习')+' · '+item.stem.slice(0,48)+'…',item.item_id));if(writingItems.some(x=>x.item_id===selected))$('writing-item').value=selected;}
  for(const draft of r.drafts||[]){const b=node('button',`${draft.title} · ${draft.version_count} 个版本\n${draft.latest_excerpt||'（空稿）'}\n${draft.updated_at||''}`,'quiet');b.dataset.writingOpen='true';b.onclick=()=>enqueue(async()=>{if(writingGuard())await openWriting(draft.draft_id);});$('writing-list').append(b);}
  if(reset&&!(r.drafts||[]).length)$('writing-list').append(node('p','还没有保存过记述草稿。','muted'));
  writingCursor=r.next_before;hidden('writing-more',!r.has_more);$('writing-new').disabled=busy||!writingItems.length;
  if(reset&&!writingItems.length)writingNotice('目前没有可用的记述材料。已保存的稿子仍可打开。');
}
function showWritingSource(c){
  clearWritingImages();
  text('writing-heading',c.title||'你的原稿');text('writing-source',c.kind==='official'?'官方原题 · 按完整题目要求修改':'原创辅助练习 · 不作为官方试题或官方评分');
  text('writing-prompt',c.prompt||c.stem||'');hidden('writing-editor',false);hidden('writing-unavailable',c.source_available!==false);
  if(c.source_available===false)text('writing-unavailable','材料已不可用于继续修改，已保存的原稿与历史反馈仍保留。请先让 Codex 核对题目来源。');
  const link=$('writing-source-link');hidden('writing-source-link',true);link.removeAttribute('href');
  if(c.kind==='official'&&c.source_url){try{const u=new URL(c.source_url);if(u.protocol==='https:'&&u.hostname==='www.jasso.go.jp'){link.href=u.href;hidden('writing-source-link',false);}}catch{}}
}
function clearWritingImages(){
  closeImage();for(const url of writingImageURLs)URL.revokeObjectURL(url);
  writingImageURLs=[];writingMediaReady=false;$('writing-images').replaceChildren();text('writing-media-state','');hidden('writing-image-retry',true);
}
async function loadWritingImages(c){
  clearWritingImages();
  if(c.source_available===false)return;
  const images=c.images||[];
  if(c.kind==='official'&&!images.length){text('writing-media-state','完整官方题面尚不可用，请先在“准备与连接”导入对应 PDF。');return;}
  if(!images.length){writingMediaReady=true;return;}
  text('writing-media-state','正在读取完整官方题面…');
  const parts=[];
  try{
    for(const info of images){
      const query=new URLSearchParams({item_id:c.item_id,index:String(info.index),session_id:session});
      const response=await fetch('/api/question-image?'+query,{headers:{Authorization:'Bearer '+token}});
      if(!response.ok)throw new Error('官方题面加载失败。');
      const blob=await response.blob();const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await blob.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('');
      if(digest!==info.sha256)throw new Error('官方题面已发生变化，请重新打开已保存的稿子核对来源。');
      const url=URL.createObjectURL(blob);writingImageURLs.push(url);
      const picture=document.createElement('img');picture.alt=info.alt;picture.src=url;await picture.decode();
      if(picture.naturalWidth!==info.width||picture.naturalHeight!==info.height)throw new Error('官方题面尺寸不符。');
      const figure=document.createElement('figure'),button=node('button','放大查看完整原页','quiet');button.onclick=()=>openImage(url,info.alt);figure.append(picture,button);parts.push(figure);
    }
    $('writing-images').append(...parts);writingMediaReady=true;text('writing-media-state',`完整题面已加载（${images.length} 页）。请核对题目选择、字数与作答要求。`);
  }catch(error){clearWritingImages();text('writing-media-state',error.message+' 已保存的文字仍保留；完整题面加载前不能保存新版本。');hidden('writing-image-retry',false);}
  updateWritingControls();
}
function renderWritingFeedback(host,feedback,historical=false){
  host.replaceChildren();
  if(!feedback){host.append(node('p',historical?'这版没有保存过反馈。':'这版尚无已保存的辅导反馈。可回到 Codex，请它先核对是否回答题目，再指出最多两处需要修改或澄清的地方。','muted'));return;}
  if(historical||feedback.stale||feedback.historical)host.append(node('p','历史反馈，仅针对当时那一版文字。','status'));
  const responses={answered:'已回应题目',partial:'部分回应题目',off_topic:'与题目要求有偏离',unclear:'还需要确认你的意思'};
  host.append(node('p','教练对题意的核对：'+(responses[feedback.task_response]||'等待核对')));
  if(feedback.task_evidence_quote)host.append(node('blockquote',feedback.task_evidence_quote));
  if(feedback.task_comment)host.append(node('p',feedback.task_comment));
  const categories={content:'内容',structure:'结构',language:'语言'};
  for(const [index,point] of (feedback.issues||[]).entries()){const row=node('article','','writing-feedback-point');row.append(node('h4',`${index+1}. ${categories[point.category]||'修改重点'}`),node('blockquote',point.quote),node('p',point.request));host.append(row);}
  host.append(node('p','这是对这版文字的教学反馈，不是 EJU 官方评分。','muted'));
}
function renderWritingContext(c,preserveInput=false){
  writingContext=c;writingNew=null;writingRemote=null;writingConflict=false;showWritingSource(c);
  if(!preserveInput){$('writing-text').value=c.latest_version.text;writingDirty=false;}
  writingLength();text('writing-save','保存修改稿');hidden('writing-discard',!writingDirty);hidden('writing-conflict',true);hidden('writing-retry',!writingPending);
  renderWritingFeedback($('writing-feedback-body'),c.current_feedback);
  hidden('writing-original-box',false);text('writing-original',c.original_version.text);
  const comparison=c.comparison;hidden('writing-compare',!comparison);
  if(comparison){text('writing-before',comparison.before_text);text('writing-after',comparison.after_text);text('writing-comparison-label',comparison.changed?'逐字对照保存内容，不自动判断是否进步或是否改变原意。':'两版文字相同；保留两次保存记录，不推断进步。');}
  hidden('writing-history',false);$('writing-versions').replaceChildren();renderWritingVersions(c);hidden('writing-history-detail',true);
}
function renderWritingVersions(c){
  for(const version of c.versions||[]){const b=node('button',(version.id===c.latest_version.id?'当前版':'历史版')+' · '+(version.created_at||version.id),'quiet');b.dataset.writingVersion='true';b.onclick=()=>enqueue(async()=>{const r=await call('read_writing_context',{session_id:session,draft_id:c.draft_id,version_id:version.id});const x=r.context;hidden('writing-history-detail',false);text('writing-history-title',version.id===x.latest_version.id?'已保存的当前版':'已保存的历史版');text('writing-history-text',x.selected_version.text);renderWritingFeedback($('writing-history-feedback'),x.selected_feedback,version.id!==x.latest_version.id);});$('writing-versions').append(b);}
  writingVersionCursor=c.versions_next_before;hidden('writing-more-versions',!c.versions_has_more);
}
async function openWriting(draftId){const r=await call('read_writing_context',{session_id:session,draft_id:draftId});renderWritingContext(r.context);await loadWritingImages(r.context);writingNotice(r.context.source_available===false?'已读取历史稿。题目来源恢复并重新核对前，不能保存新版本；原稿和历史反馈仍可回看。':'已读取保存的稿子。你可以在原意基础上继续修改；只有点击保存才会追加新版本。');}
async function newWriting(){
  if(!writingGuard())return;
  const item=writingItems.find(x=>x.item_id===$('writing-item').value);if(!item)return;
  writingNew=item;writingContext=null;writingRemote=null;writingConflict=false;writingDirty=true;
  showWritingSource(item);$('writing-text').value='';writingLength();text('writing-save','保存原稿');hidden('writing-compare',true);hidden('writing-history',true);hidden('writing-original-box',true);hidden('writing-conflict',true);hidden('writing-retry',true);hidden('writing-discard',false);renderWritingFeedback($('writing-feedback-body'),null);writingNotice('题目已打开，尚未创建草稿。请先写下自己的想法，也可以保存未完成的原稿。');
  await loadWritingImages(item);
}
async function writingConflictRead(){
  if(!writingContext)return;
  const r=await call('read_writing_context',{session_id:session,draft_id:writingContext.draft_id});writingRemote=r.context;writingConflict=true;
  text('writing-remote',r.context.latest_version.text);hidden('writing-conflict',false);
  writingNotice('保存依据已变化。本页文字仍保留，请与最新稿比较后再明确选择。');
}
async function saveWriting(){
  const c=writingContext||writingNew;if(!c)return;
  if(!writingPending&&!writingMediaReady){writingNotice('请先加载完整题面，再保存这一版。');return;}
  const value=$('writing-text').value;
  if(Array.from(value).length>8000){writingNotice('文字超过 8000 字符，请先缩短，尚未保存。');return;}
  const params=writingPending||{session_id:session,expected_revision:revision,request_id:id(),item_id:c.item_id,item_version:c.item_version,text:value,draft_id:writingContext?.draft_id||null,base_version_id:writingContext?.latest_version.id||null,source_fingerprint:c.source_fingerprint};
  writingPending=params;hidden('writing-retry',false);updateWritingControls();
  let receipt;
  try{
    receipt=await call('save_writing_draft',{...params,session_id:session});
    if(!receipt.saved)throw new Error('尚未收到已保存回执。');
  }catch(error){
    if(error.code==='resume_required'){writingNotice('会话需要重新连接。本页文字仍保留，请核对上次保存。');return;}
    if(error.code){writingPending=null;hidden('writing-retry',true);if(writingContext&&['revision_conflict','writing_version_conflict','version_conflict','writing_conflict'].includes(error.code)){await writingConflictRead();}else{writingNotice(error.message+' 本页文字仍保留。');if(error.code==='revision_conflict'){const r=await call('read_writing_drafts',{session_id:session});writingItems=r.available_items||[];}}}
    else writingNotice(error.message+' 请保持页面，点击“重新连接并核对上次保存”。');
    return;
  }
  writingPending=null;writingDirty=false;hidden('writing-retry',true);
  // A confirmed write stays confirmed even if the following read is interrupted.
  try{await openWriting(receipt.draft_id);await writingList();writingNotice(receipt.replayed?'已核对上次保存回执，没有重复创建版本。':'这一版已保存，原稿和此前版本都保留。');}
  catch(error){writingContext=null;writingNew=null;hidden('writing-editor',true);writingNotice('这一版已确认保存，但暂时无法回读。请刷新已保存的草稿后继续。');}
}
$('writing-text').addEventListener('input',()=>{writingDirty=true;writingLength();hidden('writing-discard',false);writingNotice('本页文字尚未保存。');updateWritingControls();});
$('writing-refresh').onclick=()=>enqueue(()=>writingList());
$('writing-more').onclick=()=>enqueue(()=>writingList(false));
$('writing-new').onclick=()=>enqueue(newWriting);
$('writing-save').onclick=()=>enqueue(saveWriting);
$('writing-retry').onclick=()=>enqueue(async()=>{const r=await call('resume_learning',{});session=r.session_id;await saveWriting();});
$('writing-discard').onclick=()=>enqueue(async()=>{writingDirty=false;writingConflict=false;hidden('writing-conflict',true);if(writingContext)await openWriting(writingContext.draft_id);else{writingNew=null;clearWritingImages();hidden('writing-editor',true);writingNotice('已放弃本页尚未保存的原稿。');}});
$('writing-rebase').onclick=()=>enqueue(async()=>{if(!writingRemote)return;const c=writingRemote;renderWritingContext(c,true);writingDirty=true;hidden('writing-discard',false);await loadWritingImages(c);writingNotice('已明确采用最新稿为修改基础，本页文字仍未保存。请核对后点击“保存修改稿”。');});
$('writing-image-retry').onclick=()=>enqueue(async()=>{const c=writingContext||writingNew;if(c)await loadWritingImages(c);});
$('writing-more-versions').onclick=()=>enqueue(async()=>{if(!writingContext)return;const r=await call('read_writing_context',{session_id:session,draft_id:writingContext.draft_id,versions_before:writingVersionCursor});renderWritingVersions(r.context);});
