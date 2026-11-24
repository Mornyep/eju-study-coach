"""Local worker boundary; extraction is immutable evidence, never a verified answer."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from .errors import CoreError
from .safe_files import is_link, regular_file, portable_relative


class DocumentMixin:
    def extract_document(self, session_id, expected_revision, request_id, resource_id, max_pages=5):
        from .store import integer, sha, stamp, today
        integer(max_pages, 'max_pages', 1, 5)
        config = {key: os.environ.get(key) for key in ('EJU_DOCLING_PYTHON','EJU_DOCLING_RUNNER','EJU_DOCLING_MODELS')}
        payload = dict(resource_id=resource_id, max_pages=max_pages)
        with self.connection() as db:
            digest, replay = self.begin_request(db, request_id, 'extract_document', payload, expected_revision, session_id)
            if replay:
                return replay
            row = db.execute('SELECT * FROM artifacts WHERE id=?', (resource_id,)).fetchone()
            if not row:
                raise CoreError('material_missing', '先将需要处理的原件登记到资料库。')
            source = self.verify_artifact(row)
            source_hash = row['sha256']
        if source.suffix.lower() not in ('.pdf','.png','.jpg','.jpeg'):
            raise CoreError('format_unsupported', '此资料处理器仅支持 PDF、PNG 和 JPEG。')
        if not all(config.values()) or not Path(config['EJU_DOCLING_PYTHON']).is_file() or not Path(config['EJU_DOCLING_RUNNER']).is_file():
            raise CoreError('worker_unavailable', '本地 Docling 尚未配置；原件已保存，未识别，也未切换云端。')
        # Model install is explicit outside this operation. The worker enforces offline conversion.
        jobroot = Path(tempfile.mkdtemp(prefix='eju-docling-'))
        output = jobroot/'output'
        try:
            env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            try:
                p = subprocess.run([config['EJU_DOCLING_PYTHON'], config['EJU_DOCLING_RUNNER'], 'convert',
                    '--source', str(source), '--output', str(output), '--models', config['EJU_DOCLING_MODELS'],
                    '--max-pages', str(max_pages)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=240, env=env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise CoreError('extraction_failed', '本地解析未完成；原件保留，没有写入识别成功记录。') from exc
            if p.returncode != 0:
                raise CoreError('extraction_failed', '本地解析返回失败；请运行处理器诊断，原件仍保留。')
            return self._accept_document(session_id, expected_revision, request_id, payload, output, source_hash)
        finally:
            shutil.rmtree(jobroot)

    def _accept_document(self, session_id, expected_revision, request_id, payload, output, source_hash):
        from .store import canonical, integer, short_text, sha, stamp, today
        manifest_file = output/'manifest.json'
        if not manifest_file.is_file() or is_link(manifest_file) or manifest_file.stat().st_size > 4_000_000:
            raise CoreError('invalid_extraction', '解析清单缺失、过大或不安全。')
        if is_link(output):raise CoreError('invalid_extraction','解析目录不能是链接。')
        regular_file(manifest_file,4_000_000)
        m = json.loads(manifest_file.read_text(encoding='utf-8'))
        if not isinstance(m,dict) or m.get('schema_version')!=1 or m.get('source_sha256')!=source_hash or not isinstance(m.get('converter'),dict):
            raise CoreError('invalid_extraction', '解析版本或原件哈希不匹配。')
        pages = m.get('pages'); segments = m.get('segments')
        if not isinstance(pages,list) or not 1<=len(pages)<=payload['max_pages'] or not isinstance(segments,list) or len(segments)>4000:
            raise CoreError('invalid_extraction', '页数或片段数不受支持。')
        ids=set(); page_map={}; copies=[]
        for page in pages:
            if not isinstance(page,dict):raise CoreError('invalid_extraction','页面记录格式不合法。')
            number=integer(page.get('page_no'),'page_no',1,10000)
            if number in page_map:
                raise CoreError('invalid_extraction','页码重复。')
            for dim in ('width','height'):
                if type(page.get(dim)) not in (float,int) or not math.isfinite(page[dim]) or not 0<page[dim]<=50000:
                    raise CoreError('invalid_extraction','页面尺寸无效。')
            relative=page.get('image')
            try:part=portable_relative(relative)
            except CoreError as exc:raise CoreError('invalid_extraction','页面图片路径不合法。') from exc
            file=output/part
            if part.is_absolute() or '..' in part.parts or not file.resolve().is_relative_to(output.resolve()) or any(is_link(output/Path(*part.parts[:i])) for i in range(1,len(part.parts)+1)):
                raise CoreError('invalid_extraction','页面图片路径越界。')
            if not file.is_file() or file.stat().st_size>30_000_000:
                raise CoreError('invalid_extraction','页面图片缺失或过大。')
            regular_file(file,30_000_000)
            data=file.read_bytes()
            if not data.startswith(b'\x89PNG\r\n\x1a\n') or sha(data)!=page.get('sha256'):
                raise CoreError('invalid_extraction','页面图片校验失败。')
            artifact_id='page:'+sha(data); relative='originals/'+sha(data)+'.png'
            copies.append((file,artifact_id,relative,sha(data),len(data)))
            page_map[number]=dict(page_no=number,width=page['width'],height=page['height'],sha256=sha(data),artifact_id=artifact_id)
        clean=[]
        for segment in segments:
            if not isinstance(segment,dict):raise CoreError('invalid_extraction','片段记录格式不合法。')
            sid=short_text(segment.get('id'),'segment.id',150)
            page=page_map.get(segment.get('page_no'));bbox=segment.get('bbox')
            if sid in ids or page is None or not isinstance(bbox,list) or len(bbox)!=4 or any(type(n) not in (int,float) or not math.isfinite(n) for n in bbox):
                raise CoreError('invalid_extraction','片段定位无效。')
            if not 0<=bbox[0]<=bbox[2]<=page['width']+0.1 or not 0<=bbox[1]<=bbox[3]<=page['height']+0.1:
                raise CoreError('invalid_extraction','片段区域越界。')
            coordinates=m['converter'].get('config',{}).get('coordinates','page_units_top_left')
            if coordinates=='normalized_top_left':
                if any(n<0 or n>1 for n in bbox):raise CoreError('invalid_extraction','归一化区域越界。')
                bbox=[bbox[0]*page['width'],bbox[1]*page['height'],bbox[2]*page['width'],bbox[3]*page['height']]
            elif coordinates!='page_units_top_left':raise CoreError('invalid_extraction','未知坐标体系。')
            text=short_text(segment.get('text'),'segment.text',20000,empty=True);ids.add(sid)
            clean.append(dict(id=sid,page_no=segment['page_no'],bbox=bbox,text=text,
                kind=str(segment.get('kind','text'))[:100],status='unreviewed'))
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,request_id,'extract_document',payload,expected_revision,session_id)
            if replay:return replay
            original=db.execute('SELECT * FROM artifacts WHERE id=?',(payload['resource_id'],)).fetchone()
            if not original or original['sha256']!=source_hash:
                raise CoreError('source_changed','原件登记发生变化。')
            self.verify_artifact(original)
            for source,aid,relative,digest_image,size in copies:
                dest=self.registered_path(relative)
                if dest.exists():
                    if sha(dest.read_bytes())!=digest_image:raise CoreError('artifact_corrupt','已有页面图片损坏。')
                else:
                    with dest.open('xb') as f:f.write(source.read_bytes())
                    dest.chmod(0o600)
                existing=db.execute('SELECT * FROM artifacts WHERE relative_path=?',(relative,)).fetchone()
                if existing:
                    if existing['sha256']!=digest_image or existing['size']!=size:
                        raise CoreError('artifact_conflict','页面与已有登记内容冲突。')
                    for page in page_map.values():
                        if page['artifact_id']==aid:page['artifact_id']=existing['id']
                    continue
                if db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0]>=10000:
                    raise CoreError('capacity_reached','登记资料数量已达本版上限，未删除旧资料。')
                db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)',
                    (aid,relative,'Docling page '+aid[5:17]+'.png',size,digest_image,'image/png',stamp()))
            # Full immutable normalized extraction lives in the consistent DB backup.
            event=dict(id=request_id,type='document',resource_id=payload['resource_id'],source_sha256=source_hash,
                converter=m['converter'],pages=list(page_map.values()),segments=clean,createdAt=stamp(),date=today(),sessionId=session_id,
                coordinate_system='top-left original page units',status='unreviewed')
            self.insert_event(db,event,'local_docling')
            return self.finish_request(db,request_id,digest,document_id=request_id,pages=len(pages),segments=len(clean),status='unreviewed')

    def document_state(self, db, document_id):
        row=db.execute('SELECT body FROM events WHERE id=?',(document_id,)).fetchone()
        doc=json.loads(row[0]) if row else None
        if not doc or doc['type']!='document':raise CoreError('document_missing','没有这份解析记录。')
        for e in self.events(db):
            if e['type']=='document_review' and e['document_id']==document_id:
                segment=next(s for s in doc['segments'] if s['id']==e['segment_id'])
                segment.update(text=e['text'],status=e['status'],review_id=e['id'],reviewer=e['reviewer'],note=e['note'])
        original=db.execute('SELECT * FROM artifacts WHERE id=?',(doc['resource_id'],)).fetchone()
        if not original or original['sha256']!=doc['source_sha256']:raise CoreError('source_changed','原件登记不匹配。')
        self.verify_artifact(original)
        return doc

    def read_documents(self,session_id,document_id=None,offset=0,limit=20):
        from .store import integer
        integer(offset,'offset',0,25000);integer(limit,'limit',1,50)
        with self.connection() as db:
            self.check_session(db,session_id)
            if document_id is None:
                docs=[e for e in self.events(db) if e['type']=='document']
                return dict(ok=True,revision=self.meta(db,'revision'),documents=[{k:e[k] for k in
                    ('id','resource_id','source_sha256','createdAt','status')} for e in docs[offset:offset+limit]],
                    has_more=offset+limit<len(docs),next_offset=min(len(docs),offset+limit))
            doc=self.document_state(db,document_id)
            segments=doc.pop('segments');doc['segments']=segments[offset:offset+limit]
            return dict(ok=True,revision=self.meta(db,'revision'),document=doc,has_more=offset+limit<len(segments),
                next_offset=min(len(segments),offset+limit),segment_count=len(segments),assessment_eligible=False,
                message='核对文本只确认转录；不代表题目答案已验证。')

    def review_document_segment(self,session_id,expected_revision,request_id,document_id,segment_id,status,text,page_sha256,note,reviewer='tutor'):
        from .store import short_text,stamp,today
        if status not in ('confirmed','corrected','uncertain') or reviewer not in ('tutor','learner'):
            raise CoreError('invalid_review','核对状态或来源无效。')
        short_text(text,'text',20000,empty=True);short_text(note,'note',2000)
        payload=dict(document_id=document_id,segment_id=segment_id,status=status,text=text,page_sha256=page_sha256,note=note,reviewer=reviewer)
        with self.connection(write=True) as db:
            digest,replay=self.begin_request(db,request_id,'document_review',payload,expected_revision,session_id)
            if replay:return replay
            doc=self.document_state(db,document_id);segment=next((s for s in doc['segments'] if s['id']==segment_id),None)
            if segment is None:raise CoreError('segment_missing','没有这个片段。')
            page=next(p for p in doc['pages'] if p['page_no']==segment['page_no'])
            if page_sha256!=page['sha256']:raise CoreError('source_changed','核对的图片版本不同。')
            artifact=db.execute('SELECT * FROM artifacts WHERE id=?',(page['artifact_id'],)).fetchone();self.verify_artifact(artifact)
            self.insert_event(db,dict(payload,id=request_id,type='document_review',source_sha256=doc['source_sha256'],
                createdAt=stamp(),date=today(),sessionId=session_id,review_evidence='reported_visual_review'), 'native_review')
            return self.finish_request(db,request_id,digest,segment_id=segment_id,status=status,assessment_eligible=False)

    def document_page(self,session_id,document_id,page_no):
        from .store import integer
        integer(page_no,'page_no',1,10000)
        with self.connection() as db:
            self.check_session(db,session_id);doc=self.document_state(db,document_id)
            page=next((p for p in doc['pages'] if p['page_no']==page_no),None)
            if page is None:raise CoreError('page_missing','没有这页原图。')
            row=db.execute('SELECT * FROM artifacts WHERE id=?',(page['artifact_id'],)).fetchone()
            path=self.verify_artifact(row)
            return dict(ok=True,document_id=document_id,source_sha256=doc['source_sha256'],page=page,path=str(path),
                        untrusted_source_material=True,review_recorded=False)
