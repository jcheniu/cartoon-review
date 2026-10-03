import copy,json,pathlib,sqlite3,sys,tempfile,unittest,uuid
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'server'))
from receiver import Receiver,ReviewError,canonical,sha,github_write,migrate_database
ROOT=pathlib.Path(__file__).resolve().parents[1]

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.writes=[];self.now=1000
        self.service=Receiver(self.temp.name,writer=self.write,clock=lambda:self.now)
        self.manifest=json.loads((ROOT/'data/manifest.json').read_text())
        self.items=[i for i in self.manifest['items'] if 0<=i['image_number']<=24]
        self.item=self.items[0]
        self.session=str(uuid.uuid4());self.key='a'*64
        self.body=self.body_for(25)
    def tearDown(self):self.service.close();self.temp.cleanup()
    def write(self,path,content):
        snapshot=self.service.snapshot_path(self.session,0,24)
        self.assertEqual(snapshot.read_text(),content,'Commit requires the durable JSONL snapshot')
        self.writes.append((path,content))
        return 'https://github.com/jcheniu/cartoon-review/blob/main/'+path
    def row_for(self,item):
        return dict(photo_id=item['id'],round_id=self.manifest['round']['id'],
            dataset_sha256=self.manifest['dataset_sha256'],source_sha256=item['sha256'],
            candidate_hashes={v:item['candidates'][v]['sha256'] for v in ('a','b')},
            accepted='a',preferred='a',rejection_reasons=[],caption='a cartoon subject',
            annotation_session_id=self.session,review_version=1,reviewed_at='2026-10-01T00:00:00Z',
            notes='REMOVED FIELD',path='../escape',github_token='NEVER COPY')
    def body_for(self,count):
        return dict(session_id=self.session,range=dict(start=0,end=24),records=[self.row_for(i) for i in self.items[:count]])
    def publish(self):
        self.now+=30
        self.assertTrue(self.service.process_one())
        while self.service.flush_metadata_once(): pass
    def test_partial_is_durable_but_never_committed(self):
        r=self.service.submit(self.body_for(24),self.key)
        self.assertEqual((r['state'],r['count']),('draft',24))
        self.now+=100
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.writes,[])
        self.assertEqual(len(self.service.snapshot_path(self.session,0,24).read_text().splitlines()),24)
    def test_25_incremental_saves_commit_immediately_when_complete(self):
        for index,item in enumerate(self.items):
            body=self.body_for(1);body['records']=[self.row_for(item)]
            result=self.service.submit(body,self.key)
            if index<24:
                self.assertFalse(self.service.process_one())
            else:
                self.assertEqual(result['ready_at'],self.now)
                self.assertTrue(self.service.process_one())
            self.now+=1
        self.assertFalse(self.service.process_one())
        self.assertEqual(len(self.writes),1)
        path,content=self.writes[0]
        self.assertEqual(path,f'result/round_1/{self.session}/000_024.jsonl')
        self.assertEqual(len(content.splitlines()),25)
        row=json.loads(content.splitlines()[0])
        for name in ['notes','github_token','path']:self.assertNotIn(name,row)
        self.assertEqual(row['split'],self.item['split'])
    def test_repeated_requests_and_metadata_only_changes_do_not_commit(self):
        self.service.submit(self.body,self.key);self.publish()
        for _ in range(4):
            self.assertEqual(self.service.submit(self.body,self.key)['state'],'uploaded')
            self.assertFalse(self.service.process_one())
        newer=copy.deepcopy(self.body)
        for row in newer['records']:
            row['review_version']=2;row['reviewed_at']='2026-10-02T00:00:00Z'
        self.assertEqual(self.service.submit(newer,self.key)['state'],'uploaded')
        self.now+=100
        self.assertFalse(self.service.process_one())
        self.assertEqual(len(self.writes),1)
    def test_continuous_edits_are_coalesced(self):
        self.service.submit(self.body,self.key);self.publish()
        amended=copy.deepcopy(self.body)
        for version in range(2,5):
            self.now+=10
            amended['records'][0]['review_version']=version
            amended['records'][0]['caption']='a revised cartoon '+str(version)
            self.service.submit(amended,self.key)
            self.assertFalse(self.service.process_one())
        self.now+=29
        self.assertFalse(self.service.process_one())
        self.now+=1
        self.assertTrue(self.service.process_one())
        self.assertEqual(len(self.writes),2)
        self.assertEqual(json.loads(self.writes[-1][1].splitlines()[0])['review_version'],4)
    def test_save_during_commit_preserves_new_pending_snapshot(self):
        self.service.submit(self.body,self.key)
        def write(path,content):
            amended=copy.deepcopy(self.body)
            amended['records'][0].update(review_version=2,accepted='b',preferred='b')
            self.service.submit(amended,self.key)
            return 'https://github.com/example'
        self.service.writer=write;self.publish()
        self.assertEqual(self.service.status(self.session,0,24,self.key)['state'],'pending')
        self.service.writer=self.write
        self.assertFalse(self.service.process_one())
        self.publish()
        self.assertEqual(self.service.status(self.session,0,24,self.key)['state'],'uploaded')
        with self.assertRaises(ReviewError):self.service.submit(self.body,self.key)
    def test_failure_backoff_survives_restart(self):
        self.service.submit(self.body,self.key)
        def fail(*args):raise RuntimeError('upstream')
        self.service.writer=fail;self.publish()
        restored=Receiver(self.temp.name,writer=self.write,clock=lambda:self.now)
        self.addCleanup(restored.close)
        self.assertEqual(restored.status(self.session,0,24,self.key)['state'],'pending')
        self.assertFalse(restored.process_one())
        self.now+=60
        self.assertTrue(restored.process_one())
        self.assertEqual(len(self.writes),1)
    def test_snapshot_failure_prevents_publication(self):
        with patch.object(self.service,'save_snapshot',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.service.submit(self.body,self.key)
        self.now+=100
        self.assertFalse(self.service.process_one())
        self.assertEqual(self.writes,[])
    def test_existing_uploaded_full_groups_do_not_recommit_after_restart(self):
        self.service.submit(self.body,self.key);self.publish()
        restored=Receiver(self.temp.name,writer=self.write,clock=lambda:self.now)
        self.addCleanup(restored.close)
        self.assertFalse(restored.process_one())
        self.assertEqual(restored.submit(self.body,self.key)['state'],'uploaded')
        self.assertEqual(len(self.writes),1)
    def test_legacy_partial_upload_migrates_to_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            row=self.row_for(self.item);row['image_number']=self.item['image_number']
            content=canonical(row)+'\n'
            with sqlite3.connect(pathlib.Path(directory)/'uploads.sqlite3') as db:
                db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,key_hash TEXT);CREATE TABLE uploads(session TEXT,start INTEGER,end INTEGER,content TEXT,digest TEXT,state TEXT,url TEXT,error TEXT,updated REAL,PRIMARY KEY(session,start,end));')
                db.execute('INSERT INTO sessions VALUES(?,?)',(self.session,sha(self.key)))
                db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?,?,?,?)',(self.session,0,24,content,sha(content),'uploaded','https://github.com/old','',1))
            migrate_database(pathlib.Path(directory)/'uploads.sqlite3',directory)
            restored=Receiver(directory,writer=lambda *_:self.fail('Legacy partial must not commit'),clock=lambda:self.now)
            self.addCleanup(restored.close)
            self.assertEqual(restored.status(self.session,0,24,self.key)['state'],'draft')
            self.assertFalse(restored.process_one())
            self.assertEqual(restored.snapshot_path(self.session,0,24).read_text(),content)
    def test_reverting_queued_edit_to_published_content_cancels_commit(self):
        self.service.submit(self.body,self.key);self.publish()
        amended=copy.deepcopy(self.body)
        amended['records'][0].update(review_version=2,accepted='b',preferred='b')
        self.service.submit(amended,self.key)
        reverted=copy.deepcopy(self.body)
        reverted['records'][0]['review_version']=3
        self.assertEqual(self.service.submit(reverted,self.key)['state'],'uploaded')
        self.now+=60
        self.assertFalse(self.service.process_one())
        self.assertEqual(len(self.writes),1)
    def test_github_writer_deduplicates_timestamp_only_changes(self):
        import base64
        rows=self.body['records']
        for row,item in zip(rows,self.items):row['image_number']=item['image_number']
        previous=''.join(canonical(r)+'\n' for r in rows)
        newer=copy.deepcopy(rows)
        for row in newer:row['review_version']=2;row['reviewed_at']='2026-10-02T00:00:00Z'
        content=''.join(canonical(r)+'\n' for r in newer)
        old=dict(content=base64.b64encode(previous.encode()).decode(),html_url='https://github.com/existing',sha='old')
        with patch('receiver.github_json',return_value=old) as api:
            self.assertEqual(github_write(f'result/round_1/{self.session}/000_024.jsonl',content),old['html_url'])
            self.assertEqual(api.call_count,1)
            self.assertEqual(api.call_args.args[0],'GET')
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
    def test_github_writer_rejects_discovery_and_partial_files(self):
        with self.assertRaises(ValueError):github_write('result/round_1/service.json','{}')
        rows=self.body_for(24)['records']
        for row,item in zip(rows,self.items):row['image_number']=item['image_number']
        with self.assertRaises(ValueError):github_write(f'result/round_1/{self.session}/000_024.jsonl',''.join(canonical(r)+'\n' for r in rows))


class EndpointPublisherTests(unittest.TestCase):
    def test_request_is_signed_and_identifies_the_application(self):
        import base64,io,subprocess
        from endpoint import publish,EDGE
        with tempfile.TemporaryDirectory() as directory:
            key=pathlib.Path(directory)/'key.pem'
            public=pathlib.Path(directory)/'public.pem'
            signature=pathlib.Path(directory)/'signature.bin'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],
                           capture_output=True,check=True)
            subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],capture_output=True,check=True)
            captured=[]
            class Opener:
                def open(self,request,timeout):
                    captured.append(request)
                    response=io.BytesIO(json.dumps(dict(status='ok',round_id='round-20260930T231132-d69c0f')).encode())
                    response.status=200
                    return response
            publish('https://test.lhr.life',key,Opener())
            request=captured[0]
            self.assertEqual(request.full_url,EDGE)
            self.assertTrue(request.get_header('User-agent').startswith('cartoon-review-address-publisher/'))
            self.assertEqual(json.loads(request.data)['endpoint'],'https://test.lhr.life')
            signature.write_bytes(base64.b64decode(request.get_header('X-endpoint-signature')))
            result=subprocess.run(['openssl','dgst','-sha256','-verify',str(public),'-signature',str(signature)],
                                  input=request.data,capture_output=True)
            self.assertEqual(result.returncode,0,'The endpoint payload must have a valid RSA/SHA-256 signature')
    def test_failed_acknowledgement_is_not_reported_as_published(self):
        import io,subprocess
        from endpoint import publish
        with tempfile.TemporaryDirectory() as directory:
            key=pathlib.Path(directory)/'key.pem'
            subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(key)],
                           capture_output=True,check=True)
            class Opener:
                def open(self,request,timeout):
                    response=io.BytesIO(b'{"status":"unavailable"}');response.status=503
                    return response
            with self.assertRaises(RuntimeError):publish('https://test.lhr.life',key,Opener())

if __name__=='__main__':unittest.main()
