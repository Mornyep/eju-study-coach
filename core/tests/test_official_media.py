import copy, hashlib, json, shutil, threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from test_core import CoreCase
import test_mcp
import unittest
from eju_core import question_media
from eju_core.http_server import make_server

MAP='official-jasso-2018-1-integrated-a005'
CHART='official-jasso-2018-1-integrated-a024'
FIGURE='eju-original-geography-figure-01'

class OfficialMediaTests(CoreCase):
    def test_daily_default_selects_complete_official_pages_for_every_duration(self):
        for minutes in (1,5,10,30):
            result=self.core.get_task_candidates(self.sid,minutes,'integrated')
            self.assertEqual([x['item_id']for x in result['candidates']][:2],[MAP,CHART])
            self.assertGreater(len(result['candidates']),2)
            self.assertTrue(all(x['kind']=='official' and x['images_required'] and x['image_count']==1 for x in result['candidates']))
            self.assertFalse(any('answer' in x or 'solution' in x for x in result['candidates']))
        self.assertTrue(self.core.get_task_candidates(self.sid,10,'chemistry')['no_candidate'])
        physics=self.core.get_task_candidates(self.sid,10,'physics')['candidates']
        self.assertGreater(len(physics),1);self.assertEqual(physics[0]['kind'],'official')
        self.assertEqual(self.resume()['event_count'],0)

    def test_missing_assets_and_exhaustion_never_fall_back_to_originals(self):
        with patch.object(question_media,'MEDIA_ROOT',self.base/'empty'):
            self.assertTrue(self.core.get_task_candidates(self.sid,10,'integrated')['no_candidate'])
            self.assertFalse(self.core.read_material(self.sid,MAP)['available'])
            self.assert_error('question_image_missing',self.core.read_question_images,self.sid,MAP)
            self.assert_error('item_unavailable',self.core.present_learning_task,self.sid,self.rev,'blocked',MAP,'show')
        for index,item in enumerate(x['id'] for x in self.core.items.values() if x.get('kind')=='official' and 'integrated' in x['subjects']):
            self.save({'id':'seen'+str(index),'type':'exposure','itemId':item,'level':'stem','source':'codex_report'})
        self.assertTrue(self.core.get_task_candidates(self.sid,10,'integrated')['no_candidate'])
        self.assertTrue(self.core.get_task_candidates(self.sid,10,'integrated','supplement')['candidates'])

    def test_images_hash_dimensions_source_type_and_no_internal_exposure(self):
        for item_id in (MAP,CHART,FIGURE):
            result=self.core.read_question_images(self.sid,item_id)
            self.assertTrue(result['images'])
            for image in result['images']:
                data=Path(image['path']).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(),image['sha256'])
                self.assertGreater(image['width'],1000)
            stem=self.core.read_material(self.sid,item_id)
            self.assertNotIn('answer',stem);self.assertNotIn('solution',stem)
        self.assertEqual(self.resume()['revision'],0)

    def test_tampered_or_escaping_asset_is_blocked(self):
        local=self.base/'media';shutil.copytree(question_media.MEDIA_ROOT,local)
        with patch.object(question_media,'MEDIA_ROOT',local):
            item=self.core.items[MAP];asset=item['delivery']['assets'][0];path=local/asset['path']
            path.write_bytes(path.read_bytes()+b'changed')
            self.assert_error('question_image_invalid',self.core.read_question_images,self.sid,MAP)
            asset['path']='../outside.png'
            self.assert_error('unsafe_path',self.core.read_question_images,self.sid,MAP)
            asset['path']='official/link.png';(local/'official/link.png').symlink_to(local/'official/2018_1question_jw-p16.png')
            self.assert_error('unsafe_path',self.core.read_question_images,self.sid,MAP)

    def test_figure_and_original_table_are_not_independent_unseen_variants(self):
        self.assertNotIn('|',self.core.items[FIGURE]['stem'])
        self.save({'id':'saw-table','type':'exposure','itemId':'eju-original-geography-01','level':'stem','source':'codex_report'})
        self.assertNotIn(FIGURE,[x['item_id']for x in self.core.get_task_candidates(self.sid,10,'integrated','supplement')['candidates']])

    def test_authenticated_http_picture_and_workbench_keep_full_pages(self):
        self.core.present_learning_task(self.sid,0,'task',MAP,'只作答问2(1)')
        self.assertEqual(self.core.read_workbench(self.sid)['material']['images'][0]['pdf_page'],6)
        server,token=make_server(self.core);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/api/question-image?item_id={MAP}&index=0&session_id={self.sid}'
            with self.assertRaises(HTTPError) as raised:urlopen(url)
            self.assertEqual(raised.exception.code,401)
            with urlopen(Request(url,headers={'Authorization':'Bearer '+token})) as response:
                data=response.read();self.assertEqual(response.headers['Content-Type'],'image/png')
            self.assertEqual(hashlib.sha256(data).hexdigest(),self.core.items[MAP]['delivery']['assets'][0]['sha256'])
            with patch.object(question_media,'MEDIA_ROOT',self.base/'empty'):
                self.assert_error('question_image_missing',self.core.workbench_action,self.sid,1,'show', 'task','show')
                self.assertEqual(self.resume()['event_count'],1)
        finally:server.shutdown();server.server_close();thread.join()

class ImageTransportTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_mcp.TransportTests.setUp
    connect = test_mcp.TransportTests.connect
    tool = test_mcp.TransportTests.tool
    async def test_native_stdio_returns_real_question_png_bytes(self):
        import base64
        async with self.connect() as session:
            r=await self.tool(session,'resume_learning');sid=r['session_id']
            candidates=await self.tool(session,'get_task_candidates',{'session_id':sid,'subject':'integrated'})
            self.assertEqual(candidates['candidates'][0]['item_id'],MAP)
            result=await session.call_tool('read_question_images',{'session_id':sid,'item_id':MAP})
            raw=result.model_dump(by_alias=True)
            pictures=[x for x in raw['content']if x['type']=='image']
            self.assertEqual(len(pictures),1)
            data=base64.b64decode(pictures[0]['data'])
            self.assertEqual(hashlib.sha256(data).hexdigest(),self.core_asset_hash())
            self.assertEqual((await self.tool(session,'resume_learning'))['event_count'],0)
    def core_asset_hash(self):
        from eju_core.store import Core
        return Core(self.vault).items[MAP]['delivery']['assets'][0]['sha256']
