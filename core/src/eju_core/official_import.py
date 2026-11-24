"""Exact reviewed public PDF import; never stores or uploads the PDF."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from importlib.resources import files
from .errors import CoreError
from .safe_files import is_link, regular_file, portable_relative
from . import question_media

MAX_PDF_BYTES = 16 * 1024 * 1024

def setup_status(core):
    # Check the live DB on each request, not just the server's startup result.
    with core.connection() as db:
        revision=core.meta(db,'revision')
    groups={}
    for item in core.items.values():
        if item.get('kind')!='official':continue
        delivery=item['delivery'];key=delivery['source_sha256']
        entry=groups.setdefault(key,{'source_sha256':key,'url':delivery['source_url'],'subject':item['subjects'][0], 'subjects':[], 'registered':0,'ready':0})
        entry['subjects']=sorted(set(entry['subjects'])|set(item['subjects']))
        entry['registered']+=1;entry['ready']+=int(question_media.ready(item))
    return {'ok':True,'core_version':'1.0.0b11','vault_readable':True,'revision':revision,'sources':list(groups.values()),
            'official_registered':sum(x['registered'] for x in groups.values()),
            'official_ready':sum(x['ready'] for x in groups.values()),'desktop_connection':'not_observed',
            'pdf_uploaded_to_cloud':False}

def import_pdf_bytes(data, catalog=None, media_root=None):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_PDF_BYTES:
        raise CoreError('too_large','请选择不超过16 MiB的官方PDF。')
    digest=hashlib.sha256(data).hexdigest()
    catalog=catalog or json.loads(files('eju_core').joinpath('data/catalog.json').read_text(encoding='utf-8'))
    items=[x for x in catalog['items'] if x.get('kind')=='official' and x.get('delivery',{}).get('source_sha256')==digest]
    if not items:raise CoreError('source_unverified','此PDF与本版已核对的官方文件不一致，未导入。请使用准备页中的官方版本。')
    from importlib.metadata import version
    if version('pypdfium2')!='5.14.0' or version('Pillow')!='12.3.0':
        raise CoreError('renderer_version','请先安装随包锁定的 PDF 渲染依赖，避免图像与核对版本不一致。')
    import pypdfium2 as pdfium
    media=Path(media_root or question_media.MEDIA_ROOT)
    for component in [media,*list(media.parents)[:3]]:
        if is_link(component):raise CoreError('unsafe_path','图片目录不能是链接。')
    output=[]
    verified={}
    # Finish rendering and verification before writing any page.
    with pdfium.PdfDocument(data) as document:
        for item in items:
            for asset in item['delivery']['assets']:
                relative=portable_relative(asset['path']);target=media/relative
                if relative.parts[0]!='official' or len(relative.parts)!=2:raise CoreError('unsafe_path','原页目标不合法。')
                if is_link(target.parent) or is_link(target):raise CoreError('unsafe_path','图片目标不能是链接。')
                signature=(asset['pdf_page'],asset['sha256'],asset['width'],asset['height'])
                if target in verified:
                    if verified[target]!=signature:raise CoreError('render_mismatch','共用原页的登记信息不一致，未导入。')
                    continue
                verified[target]=signature
                page=document[asset['pdf_page']-1]
                try:
                    bitmap=page.render(scale=2)
                    try:
                        picture=bitmap.to_pil();stream=io.BytesIO();picture.save(stream,format='PNG');png=stream.getvalue()
                        if picture.size!=(asset['width'],asset['height']):raise CoreError('render_mismatch','原页尺寸与核对版本不同，未导入。')
                    finally:bitmap.close()
                finally:page.close()
                if hashlib.sha256(png).hexdigest()!=asset['sha256']:
                    raise CoreError('render_mismatch','本机原页渲染结果与已核对图像不一致，未导入；请保留原文件供核对。')
                if target.exists():
                    regular_file(target,8*1024*1024)
                    if target.read_bytes()!=png:raise CoreError('destination_exists','原页目标已有不同内容，未覆盖。')
                output.append((target,png,item['id']))
    for target,png,_ in output:
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if target.exists():continue
        descriptor,name=tempfile.mkstemp(prefix='.eju-page-',dir=target.parent)
        try:
            with os.fdopen(descriptor,'wb') as handle:handle.write(png);handle.flush();os.fsync(handle.fileno())
            # No overwrite: another process may have created a page after review.
            try:
                # Hard-linking the fully flushed temporary file publishes one
                # complete page atomically and refuses an existing destination.
                os.link(name,target)
            except FileExistsError:
                regular_file(target,8*1024*1024)
                if target.read_bytes()!=png:raise CoreError('destination_exists','原页目标发生变化，未覆盖。')
        finally:Path(name).unlink(missing_ok=True)
    return {'ok':True,'source_sha256':digest,'items_ready':[item['id'] for item in items],
            'pages_verified':len(output),'network_used':False,'original_modified':False,
            'message':'已核对并接入完整原页。可以回到 Codex，请它重新读取可用题目。'}

