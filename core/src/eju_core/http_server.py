"""Authenticated loopback-only client. No remote binding, CORS or arbitrary paths."""
from __future__ import annotations
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import threading
import sqlite3
from .official_import import import_pdf_bytes, setup_status, MAX_PDF_BYTES
from .safe_files import is_link, private_mode, private_text_create
from urllib.parse import urlsplit, parse_qs
from .errors import CoreError

CALLS = {'resume_learning','read_workbench','workbench_action','read_workbench_records','read_documents',
         'read_writing_resume','read_writing_drafts','read_writing_context','save_writing_draft',
         'read_study_review','get_next_practice','start_next_practice','read_review_queue','read_coaching_context','start_review_practice'}
STATIC = {'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),
          '/style.css':('style.css','text/css; charset=utf-8')}


def make_server(core, port=0, token=None):
    token = token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.0'
        def log_message(self,*args): pass  # Do not log credentials or personal responses.
        def setup(self):
            super().setup();self.connection.settimeout(15)
        def send_data(self,code,data,kind='application/json; charset=utf-8'):
            if isinstance(data,dict):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(code)
            for k,v in {'Content-Type':kind,'Content-Length':str(len(data)), 'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
                self.send_header(k,v)
            self.end_headers();self.wfile.write(data)
        def failure(self,code,tag,message):self.send_data(code,dict(ok=False,error=dict(code=tag,message=message)))
        def boundary(self,write=False):
            host=f'127.0.0.1:{self.server.server_port}';origin='http://'+host
            if not self.path.startswith('/') or self.path.startswith('//') or '#' in self.path:
                self.failure(400,'invalid_target','请求路径不合法。');return False
            if self.headers.get_all('Host') != [host]:
                self.failure(403,'host_rejected','仅允许本机工作台地址。');return False
            incoming=self.headers.get_all('Origin')
            if (write and incoming != [origin]) or (incoming is not None and incoming != [origin]):
                self.failure(403,'origin_rejected','请求来源不匹配。');return False
            return True
        def authorized(self):
            values=self.headers.get_all('Authorization') or []
            supplied=values[0] if len(values)==1 else ''
            if len(values)!=1 or len(supplied)>200 or not hmac.compare_digest(supplied.encode('utf-8'),'Bearer '.encode()+token.encode('ascii')):
                self.failure(401,'authentication_required','连接凭证缺失或过期，请从本机启动入口重新打开。');return False
            return True
        def do_GET(self):
            if not self.boundary():return
            route=urlsplit(self.path)
            if route.query and route.path in STATIC:
                self.failure(400,'query_rejected','入口不接受查询参数。');return
            if route.path in STATIC:
                name,kind=STATIC[route.path];data=files('eju_core').joinpath('web/'+name).read_bytes()
                self.send_data(200,data,kind);return
            if not self.authorized():return
            if route.path == '/api/setup' and not route.query:
                try:self.send_data(200,setup_status(core))
                except CoreError as exc:self.send_data(400,exc.result())
                except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):self.failure(400,'setup_unavailable','学习库状态无法读取，请检查本地安装。')
                return
            if route.path == '/api/question-image':
                try:
                    args = parse_qs(route.query, strict_parsing=True)
                    if set(args) != {'item_id', 'index', 'session_id'} or any(len(v) != 1 for v in args.values()):raise ValueError()
                    index = int(args['index'][0])
                    result = core.read_question_images(args['session_id'][0], args['item_id'][0])
                    if not 0 <= index < len(result['images']):raise ValueError()
                    info=result['images'][index]
                    data = Path(info['path']).read_bytes()
                    if hashlib.sha256(data).hexdigest()!=info['sha256']:
                        raise CoreError('question_image_invalid','读取期间题目图片发生变化。')
                    self.send_data(200, data, 'image/png')
                except CoreError as exc:self.send_data(400, exc.result())
                except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):self.failure(400, 'invalid_request', '题目图片读取失败。')
                return
            if route.path!='/api/asset':self.failure(404,'not_found','不存在此入口。');return
            try:
                args=parse_qs(route.query,strict_parsing=True)
                if set(args)!={'id','session_id'} or any(len(v)!=1 for v in args.values()):raise ValueError()
                with core.connection() as db:
                    core.check_session(db,args['session_id'][0])
                    row=db.execute('SELECT * FROM artifacts WHERE id=?',(args['id'][0],)).fetchone()
                    if row is None:raise CoreError('material_missing','资料没有登记。')
                    if row['media_type'] not in ('image/png','image/jpeg','image/webp'):
                        raise CoreError('preview_unsupported','此入口仅预览已登记的图片。')
                    path=core.verify_artifact(row);data=path.read_bytes()
                    if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
                        raise CoreError('material_corrupt','读取期间图片发生变化。')
                self.send_data(200,data,row['media_type'])
            except CoreError as exc:self.send_data(400,exc.result())
            except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):self.failure(400,'invalid_request','图片请求未完成。')
        def do_POST(self):
            if not self.boundary(write=True) or not self.authorized():return
            if self.path=='/api/import-official':
                if self.headers.get_all('Content-Type')!=['application/pdf']:
                    self.failure(415,'pdf_required','请选择PDF文件。');return
                try:
                    lengths=self.headers.get_all('Content-Length')
                    if self.headers.get_all('Transfer-Encoding') or not lengths or len(lengths)!=1:raise ValueError()
                    length=int(lengths[0])
                    if not 0<length<=MAX_PDF_BYTES:
                        self.failure(413,'too_large','PDF超过16 MiB限制。');return
                    if not self.server.import_slot.acquire(blocking=False):
                        self.failure(409,'import_busy','另一份原页正在导入，请稍后重试。');return
                    try:
                        raw=self.rfile.read(length)
                        if len(raw)!=length:raise ValueError()
                        self.send_data(200,import_pdf_bytes(raw,core.catalog))
                    finally:self.server.import_slot.release()
                except CoreError as exc:self.send_data(400,exc.result())
                except (OSError,ValueError,TypeError,ImportError,KeyError,IndexError):
                    self.failure(400,'import_failed','原页导入未完成。请检查文件和本地依赖后重试，原件未修改。')
                return
            if self.path!='/api/call':self.failure(404,'not_found','不存在此入口。');return
            if len(self.headers.get_all('Content-Type') or [])!=1 or self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
                self.failure(415,'json_required','写入需要 JSON。');return
            try:
                lengths=self.headers.get_all('Content-Length')
                if self.headers.get('Transfer-Encoding') or not lengths or len(lengths)!=1:raise ValueError()
                length=int(lengths[0])
                if not 0<length<=120000:self.failure(413,'too_large','请求超过大小限制。');return
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError()
                def reject(value):raise ValueError('nonfinite')
                def pairs(items):
                    d={}
                    for k,v in items:
                        if k in d:raise ValueError('duplicate key')
                        d[k]=v
                    return d
                data=json.loads(raw,parse_constant=reject,object_pairs_hook=pairs)
                if not isinstance(data,dict) or set(data)!={'method','params'} or data['method'] not in CALLS or not isinstance(data['params'],dict):raise ValueError()
                result=getattr(core,data['method'])(**data['params'])
                self.send_data(200,result)
            except CoreError as exc:self.send_data(409 if exc.code=='revision_conflict' else 400,exc.result())
            except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error):self.failure(400,'invalid_request','操作未完成，输入未确认保存。')
        def do_OPTIONS(self):self.failure(405,'method_rejected','不支持跨来源访问。')
    class BoundedServer(ThreadingHTTPServer):
        # Slow local clients cannot allocate an unbounded number of threads.
        slots=threading.BoundedSemaphore(16)
        import_slot=threading.BoundedSemaphore(1)
        def process_request(self,request,client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request);return
            try:super().process_request(request,client_address)
            except BaseException:
                self.slots.release();raise
        def process_request_thread(self,request,client_address):
            try:super().process_request_thread(request,client_address)
            finally:self.slots.release()
    server=BoundedServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server,token


def serve(core,port=0):
    server,token=make_server(core,port)
    directory=core.root/'runtime'
    if is_link(directory):raise CoreError('unsafe_path','运行目录不能是符号链接。')
    directory.mkdir(exist_ok=True,mode=0o700)
    private_mode(directory,directory=True)
    path=directory/f'workbench-{server.server_port}.json'
    url=f'http://127.0.0.1:{server.server_port}/#token={token}'
    with private_text_create(path) as f:json.dump({'url':url,'pid':os.getpid()},f)
    print(json.dumps({'ok':True,'url':url,'runtime_file':str(path),'message':'保持此终端运行。页面访问仅限本机。'},ensure_ascii=False),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close();path.unlink(missing_ok=True)
