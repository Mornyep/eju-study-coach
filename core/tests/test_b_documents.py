import base64,copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from eju_core.store import Core,sha
from eju_core.errors import CoreError
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1S0AAAAASUVORK5CYII=')
class DocumentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.vault=self.root/'vault';Core.initialize(self.vault);self.core=Core(self.vault);r=self.core.resume_learning();self.sid=r['session_id']
  (self.vault/'inbox/source.png').write_bytes(PNG);r=self.core.import_material(self.sid,0,'source','source.png');self.resource=r['resource_id'];self.rev=r['revision']
  self.output=self.root/'output';(self.output/'pages').mkdir(parents=True);(self.output/'pages/1.png').write_bytes(PNG)
  self.manifest=dict(schema_version=1,source_sha256=sha(PNG),converter=dict(name='contract-test',version='test-only',config={'coordinates':'page_units_top_left'}),pages=[dict(page_no=1,width=100,height=100,image='pages/1.png',sha256=sha(PNG))],segments=[dict(id='s1',page_no=1,bbox=[1,2,30,40],text='f(x)=x²',kind='formula',status='confirmed')])
 def accept(self,rid='doc1'):
  (self.output/'manifest.json').write_text(json.dumps(self.manifest));r=self.core._accept_document(self.sid,self.rev,rid,dict(resource_id=self.resource,max_pages=5),self.output,sha(PNG));self.rev=r['revision'];return r
 def review(self,status='confirmed',rid='review1',text='f(x)=x²'):
  r=self.core.review_document_segment(self.sid,self.rev,rid,'doc1','s1',status,text,sha(PNG),'已检查原图','tutor');self.rev=r['revision'];return r
 def test_unreviewed_normalization_review_lineage_and_backup(self):
  self.accept();d=self.core.read_documents(self.sid,'doc1');self.assertEqual(d['document']['segments'][0]['status'],'unreviewed');self.assertFalse(d['assessment_eligible'])
  self.review();self.review('uncertain','review2','f(x)=X²');d=self.core.read_documents(self.sid,'doc1');self.assertEqual(d['document']['segments'][0]['status'],'uncertain')
  original=next(e for e in self.core.export_json()['events'] if e['type']=='document');self.assertEqual(original['segments'][0]['text'],'f(x)=x²')
  self.core.backup(self.root/'backup');Core.restore(self.root/'backup',self.root/'copy');copycore=Core(self.root/'copy');s=copycore.resume_learning();self.assertEqual(copycore.read_documents(s['session_id'],'doc1')['document'],d['document'])
  self.assertTrue(Path(copycore.document_page(s['session_id'],'doc1',1)['path']).is_file())
 def test_invalid_paths_and_source_hash_no_partial_registration(self):
  before=self.core.export_json();self.manifest['pages'][0]['image']='../escape.png'
  with self.assertRaises(CoreError):self.accept()
  self.assertEqual(self.core.export_json()['events'],before['events'])
  self.manifest['pages'][0]['image']='pages/1.png';self.manifest['source_sha256']='bad'
  with self.assertRaises(CoreError):self.accept()
 def test_normalized_box_and_invalid_review_hash(self):
  self.manifest['converter']['config']['coordinates']='normalized_top_left';self.manifest['segments'][0]['bbox']=[.1,.2,.3,.4];self.accept()
  self.assertEqual(self.core.read_documents(self.sid,'doc1')['document']['segments'][0]['bbox'],[10,20,30,40])
  with self.assertRaises(CoreError):self.core.review_document_segment(self.sid,self.rev,'bad','doc1','s1','confirmed','x','bad','checked')
  self.assertEqual(self.core.resume_learning()['revision'],self.rev)
 def test_duplicate_conflict_and_corrupt_page(self):
  self.accept();self.assertTrue(self.accept()['replayed']);self.review()
  with self.assertRaises(CoreError):self.core.review_document_segment(self.sid,1,'stale','doc1','s1','confirmed','x',sha(PNG),'check')
  path=Path(self.core.document_page(self.sid,'doc1',1)['path']);path.write_bytes(b'corrupt')
  with self.assertRaises(CoreError):self.review('confirmed','new')
 def test_missing_worker_does_not_claim_success(self):
  with patch.dict(os.environ,{'EJU_DOCLING_PYTHON':'','EJU_DOCLING_RUNNER':'','EJU_DOCLING_MODELS':''}):
   with self.assertRaises(CoreError) as error:self.core.extract_document(self.sid,self.rev,'extract',self.resource)
  self.assertEqual(error.exception.code,'worker_unavailable');self.assertFalse(self.core.read_documents(self.sid)['documents'])
