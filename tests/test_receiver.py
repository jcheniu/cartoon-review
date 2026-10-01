import copy,json,pathlib,sys,tempfile,unittest,uuid
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'server'))
from receiver import Receiver,ReviewError,validate_rows
ROOT=pathlib.Path(__file__).resolve().parents[1]

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.writes=[]
        self.service=Receiver(self.temp.name,writer=lambda path,content:self.writes.append((path,content)) or 'https://github.com/jcheniu/cartoon-review/blob/main/'+path)
        self.manifest=json.loads((ROOT/'data/manifest.json').read_text())
        self.item=next(i for i in self.manifest['items'] if i['candidates'])
        self.session=str(uuid.uuid4());self.key='a'*64
        start=(self.item['image_number']//25)*25
        self.row=dict(photo_id=self.item['id'],round_id=self.manifest['round']['id'],
            dataset_sha256=self.manifest['dataset_sha256'],source_sha256=self.item['sha256'],
            candidate_hashes={v:self.item['candidates'][v]['sha256'] for v in ('a','b')},
            accepted='a',preferred='a',rejection_reasons=[],caption='a cartoon cat',
            annotation_session_id=self.session,review_version=1,reviewed_at='2026-10-01T00:00:00Z',
            notes='DO NOT PUBLISH THIS REMOVED FIELD',path='../escape',github_token='NEVER COPY')
        self.body=dict(session_id=self.session,range=dict(start=start,end=start+24),records=[self.row])
    def tearDown(self):self.temp.cleanup()
    def test_upload_only_safe_fields_and_fixed_path(self):
        r=self.service.submit(self.body,self.key)
        self.assertEqual(r['state'],'pending')
        self.assertTrue(self.service.process_one())
        path,content=self.writes[0]
        self.assertEqual(path,f'result/round_1/{self.session}/000_024.jsonl')
        row=json.loads(content)
        self.assertNotIn('notes',row);self.assertNotIn('github_token',row);self.assertNotIn('path',row)
        self.assertEqual(row['split'],self.item['split'])
        self.assertEqual(self.service.submit(self.body,self.key)['state'],'uploaded')
        self.assertFalse(self.service.process_one())
    def test_session_key_prevents_overwriting_another_browser(self):
        self.service.submit(self.body,self.key)
        with self.assertRaises(ReviewError):self.service.submit(self.body,'b'*64)
        with self.assertRaises(ReviewError):self.service.status(self.session,0,24,'b'*64)
    def test_bad_hash_rejection_and_range_are_rejected(self):
        for field,value in [('source_sha256','wrong'),('accepted','neither'),('photo_id','499')]:
            body=copy.deepcopy(self.body);body['records'][0][field]=value
            with self.assertRaises(ReviewError):self.service.submit(body,self.key)
        body=copy.deepcopy(self.body);body['range']['end']=500
        with self.assertRaises(ReviewError):self.service.submit(body,self.key)
    def test_new_save_during_upload_remains_pending(self):
        self.service.submit(self.body,self.key)
        def write(path,content):
            body=copy.deepcopy(self.body);body['records'][0]['review_version']=2
            body['records'][0]['preferred']=body['records'][0]['accepted']='b'
            self.service.submit(body,self.key)
            return 'https://github.com/example'
        self.service.writer=write;self.service.process_one()
        self.assertEqual(self.service.status(self.session,0,24,self.key)['state'],'pending')
        self.service.writer=lambda *args:'https://github.com/example'
        self.service.process_one()
        self.assertEqual(self.service.status(self.session,0,24,self.key)['state'],'uploaded')
        with self.assertRaises(ReviewError):self.service.submit(self.body,self.key)
    def test_failure_is_durable_and_retried(self):
        self.service.submit(self.body,self.key)
        def fail(*args):raise RuntimeError('upstream')
        self.service.writer=fail;self.service.process_one()
        restored=Receiver(self.temp.name,writer=lambda *args:'https://github.com/example')
        self.assertEqual(restored.status(self.session,0,24,self.key)['state'],'pending')
        restored.process_one()
        self.assertEqual(restored.status(self.session,0,24,self.key)['state'],'uploaded')

if __name__=='__main__':unittest.main()
