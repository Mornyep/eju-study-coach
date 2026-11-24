import http.client,json,threading
from pathlib import Path
from unittest.mock import patch
from test_core import CoreCase
from eju_core.http_server import make_server
from eju_core.official_import import import_pdf_bytes,setup_status,MAX_PDF_BYTES
from eju_core import question_media

class OnboardingTests(CoreCase):
 def test_readiness_is_current_and_does_not_create_learning_evidence(self):
  result=setup_status(self.core)
  self.assertEqual(result['official_registered'],76);self.assertEqual(result['official_ready'],76)
  self.assertEqual(len(result['sources']),9)
  self.assertEqual({r['url'].rsplit('/',1)[-1]:r['registered'] for r in result['sources']},{'2018_1question_jw.pdf':20,'2018_1question_science.pdf':10,'2018_1question_jafl.pdf':8,'eju_2010_01question_jafl_1.pdf':16,'eju_2010_01question_jw_ja.pdf':6,'2017_1question_jafl.pdf':6,'2017_2question_jafl.pdf':4,'2017_1question_jw.pdf':2,'2017_2question_jw.pdf':4})
  for source in result['sources']:
   if source['url'].rsplit('/',1)[-1] in ('2017_1question_jafl.pdf','2017_2question_jafl.pdf'):
    self.assertEqual(source['subjects'],['japanese_reading','japanese_writing'])
  self.assertEqual(result['desktop_connection'],'not_observed')
  with patch.object(question_media,'MEDIA_ROOT',self.base/'missing'):
   self.assertEqual(setup_status(self.core)['official_ready'],0)
  self.assertEqual(self.core.export_json()['events'],[])
 def test_readiness_rechecks_deleted_database_instead_of_claiming_ready(self):
  self.core.db_path.unlink()
  self.assert_error('storage_error',setup_status,self.core)
  self.assertFalse(self.core.db_path.exists())
 def test_unknown_pdf_rejected_before_parser_and_filesystem(self):
  with patch('pypdfium2.PdfDocument') as parser:
   self.assert_error('source_unverified',import_pdf_bytes,b'%PDF unrelated',media_root=self.base/'absent')
   self.assert_error('too_large',import_pdf_bytes,b'',media_root=self.base/'absent')
   parser.assert_not_called()
  self.assertFalse((self.base/'absent').exists())
 def test_import_http_requires_origin_auth_type_length_and_bounds(self):
  server,token=make_server(self.core);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
  host='127.0.0.1:'+str(server.server_port)
  def request(extra=(),omit=(),body=b'%PDF unrelated',length=None):
   conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.putrequest('POST','/api/import-official',skip_host=True)
   headers={'Host':host,'Origin':'http://'+host,'Authorization':'Bearer '+token,'Content-Type':'application/pdf','Content-Length':str(len(body) if length is None else length)}
   for k,v in [*(x for x in headers.items() if x[0] not in omit),*extra]:conn.putheader(k,v)
   conn.endheaders(body);r=conn.getresponse();data=json.loads(r.read());conn.close();return r.status,data
  with patch('eju_core.http_server.import_pdf_bytes') as importer:
   for kwargs,status in [({'omit':('Authorization',)},401),({'omit':('Origin',)},403),({'extra':[('Origin','http://evil.test')]},403),({'extra':[('Content-Type','application/pdf')]},415),({'extra':[('Content-Length','1')]},400),({'extra':[('Transfer-Encoding','chunked')]},400),({'length':MAX_PDF_BYTES+1},413)]:
    self.assertEqual(request(**kwargs)[0],status)
   importer.assert_not_called()
  status,data=request();self.assertEqual(status,400);self.assertEqual(data['error']['code'],'source_unverified')
  conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('GET','/api/setup',headers={'Authorization':'Bearer '+token});r=conn.getresponse();self.assertEqual(r.status,200);self.assertEqual(json.loads(r.read())['official_registered'],76);conn.close()
  self.assertEqual(self.core.export_json()['events'],[])
