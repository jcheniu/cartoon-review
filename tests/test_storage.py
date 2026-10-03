import copy,json,pathlib,sys,tempfile,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'server'))
from receiver import Receiver,ReviewError,canonical,atomic_text,persist_record
import test_receiver as fixtures

class StorageTests(unittest.TestCase):
    setUp=fixtures.ReceiverTests.setUp
    tearDown=fixtures.ReceiverTests.tearDown
    write=fixtures.ReceiverTests.write
    row_for=fixtures.ReceiverTests.row_for
    body_for=fixtures.ReceiverTests.body_for
    publish=fixtures.ReceiverTests.publish
    def test_blocked_group_does_not_block_status_or_another_group(self):
        self.service.submit(self.body_for(1),self.key)
        blocked=threading.Event();release=threading.Event();errors=[]
        original=self.service.save_snapshot
        def save(session,start,end,content):
            if start==0:blocked.set();release.wait(5)
            return original(session,start,end,content)
        def submit():
            try:self.service.submit(self.body,self.key)
            except Exception as error:errors.append(error)
        with patch.object(self.service,'save_snapshot',side_effect=save):
            thread=threading.Thread(target=submit);thread.start()
            self.assertTrue(blocked.wait(2))
            self.assertEqual(self.service.status(self.session,0,24,self.key)['count'],1)
            with self.assertRaises(ReviewError) as error:self.service.submit(self.body,self.key)
            self.assertEqual(error.exception.status,503)
            other=copy.deepcopy(self.body)
            other['range']={'start':25,'end':49}
            other['records']=[self.row_for(i) for i in self.manifest['items'] if 25<=i['image_number']<=49]
            self.assertEqual(self.service.submit(other,self.key)['count'],25)
            self.now+=21
            self.assertEqual(self.service.health()['slow_saves'],1)
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,[])
    def test_unchanged_post_never_touches_persistent_files(self):
        self.service.submit(self.body,self.key);self.publish()
        identity=(self.session,0,24)
        lock=self.service.group_locks[identity]
        lock.acquire()
        try:
            with patch('receiver.atomic_text',side_effect=AssertionError('Unexpected filesystem write')):
                result=self.service.submit(self.body,self.key)
                self.assertEqual(result['state'],'uploaded')
        finally:lock.release()
    def test_jsonl_recovers_after_metadata_write_failure(self):
        self.service.submit(self.body_for(1),self.key)
        with patch('receiver.persist_record',side_effect=OSError('interrupted metadata write')):
            with self.assertRaises(OSError):self.service.submit(self.body,self.key)
        self.assertEqual(self.service.status(self.session,0,24,self.key)['count'],1)
        restored=Receiver(self.temp.name,writer=self.write,clock=lambda:self.now)
        self.addCleanup(restored.close)
        self.assertEqual(restored.status(self.session,0,24,self.key)['count'],25)
        self.assertEqual(restored.status(self.session,0,24,self.key)['state'],'pending')
        self.assertEqual(list(pathlib.Path(self.temp.name).glob('*.sqlite*')),[])
    def test_metadata_flush_does_not_hold_the_shared_index_lock(self):
        self.service.submit(self.body,self.key)
        self.assertTrue(self.service.process_one())
        blocked=threading.Event();release=threading.Event()
        def slow(*args):
            blocked.set();release.wait(5)
        with patch('receiver.persist_record',side_effect=slow):
            thread=threading.Thread(target=self.service.flush_metadata_once);thread.start()
            self.assertTrue(blocked.wait(2))
            self.assertEqual(self.service.status(self.session,0,24,self.key)['state'],'uploaded')
            self.assertEqual(self.service.submit(self.body,self.key)['state'],'uploaded')
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive())

if __name__=='__main__':unittest.main()
