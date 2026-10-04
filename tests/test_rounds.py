import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import threading
import urllib.request
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"server"))
from receiver import ROOT,Receiver,CollectionReceiver,ReviewError,canonical,github_write,handler
from unittest.mock import patch

class RoundIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.session=str(uuid.uuid4());self.key="a"*64;self.published=[]
        legacy=json.loads((ROOT/"data/manifest.json").read_text())
        self.receivers=[]
        for number in (2,3):
            manifest=copy.deepcopy(legacy)
            manifest["items"]=manifest["items"][:250];manifest["total"]=250
            manifest["round"]["id"]=f"generation-{number}"
            manifest["dataset_sha256"]=str(number)*64
            path=self.root/f"manifest-{number}.json";path.write_text(json.dumps(manifest))
            receiver=Receiver(self.root/f"state-{number}",manifest_path=path,
                              destination=f"result/round_{number}",runtime_dir=self.root/"runtime",
                              writer=lambda path,content:self.published.append((path,content)) or "https://example.test/result")
            self.receivers.append(receiver)
        self.router=CollectionReceiver(self.receivers)
    def tearDown(self):
        for receiver in self.receivers:receiver.close()
        self.temp.cleanup()
    def body(self,index,complete=False):
        receiver=self.receivers[index];manifest=receiver.manifest
        rows=[]
        for item in manifest["items"][:25 if complete else 1]:
            rows.append(dict(photo_id=item["id"],image_number=item["image_number"],round_id=manifest["round"]["id"],
                dataset_sha256=manifest["dataset_sha256"],source_sha256=item["sha256"],
                candidate_hashes={v:item["candidates"][v]["sha256"] for v in ("a","b")},
                accepted="a",preferred="a",rejection_reasons=[],caption="a cartoon pet",
                review_version=1,reviewed_at="2026-10-05T06:30:00+08:00",annotation_session_id=self.session))
        return dict(session_id=self.session,round_id=manifest["round"]["id"],range=dict(start=0,end=24),records=rows)
    def test_same_number_and_session_are_isolated_and_publish_to_correct_round(self):
        for index in (0,1):
            result=self.router.submit(self.body(index,True),self.key)
            self.assertEqual(result["path"],f"result/round_{index+2}/{self.session}/000_024.jsonl")
            self.assertTrue(self.receivers[index].process_one())
        self.assertEqual([p for p,c in self.published],
                         [f"result/round_{n}/{self.session}/000_024.jsonl" for n in (2,3)])
        for index in (0,1):
            result=self.router.status(self.session,0,24,self.key,round_id=f"generation-{index+2}")
            self.assertEqual(result["state"],"uploaded")
        self.assertEqual(self.router.health()["status"],"ok")
    def test_cross_round_hashes_unknown_rounds_and_out_of_range_are_rejected(self):
        body=self.body(0);body["round_id"]="generation-3"
        with self.assertRaises(ReviewError):self.router.submit(body,self.key)
        body=self.body(0);body["round_id"]="unknown"
        with self.assertRaises(ReviewError):self.router.submit(body,self.key)
        body=self.body(0);body["range"]=dict(start=250,end=274)
        with self.assertRaises(ReviewError):self.router.submit(body,self.key)
        self.assertEqual(self.published,[])
    def test_old_client_can_infer_round_from_records(self):
        body=self.body(0);body.pop("round_id")
        self.assertEqual(self.router.submit(body,self.key)["state"],"draft")
    def test_http_status_routes_to_the_requested_round(self):
        for index in (0,1):self.router.submit(self.body(index),self.key)
        server=ThreadingHTTPServer(("127.0.0.1",0),handler(self.router,{"http://localhost"}))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for number in (2,3):
                url=f"http://127.0.0.1:{server.server_port}/api/status?session={self.session}&start=0&end=24&round_id=generation-{number}"
                request=urllib.request.Request(url,headers={"Origin":"http://localhost","X-Upload-Key":self.key})
                with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
                self.assertEqual(result["path"],f"result/round_{number}/{self.session}/000_024.jsonl")
        finally:server.shutdown();server.server_close();thread.join()
    def test_public_preview_and_original_hashes_are_preserved_separately(self):
        receiver=self.receivers[0]
        receiver.manifest["items"][0]["preview_sha256"]="d"*64
        self.router.submit(self.body(0),self.key)
        path=receiver.data_dir/"jsonl"/self.session/"000_024.jsonl"
        record=json.loads(path.read_text())
        self.assertEqual(record["source"]["sha256"],"d"*64)
        self.assertEqual(record["source"]["original_sha256"],record["source_sha256"])
    def test_github_writer_accepts_only_allowlisted_complete_round_groups(self):
        content="".join(canonical(r)+"\n" for r in self.body(0,True)["records"])
        with patch("receiver.github_json",side_effect=[None,{"content":{"html_url":"https://example.test/saved"}}]) as api:
            self.assertEqual(github_write(f"result/round_2/{self.session}/000_024.jsonl",content),"https://example.test/saved")
            self.assertIn("round 2",api.call_args.args[2]["message"])
        for name in ("round_0","round_5","../round_2"):
            with self.assertRaises(ValueError):github_write(f"result/{name}/{self.session}/000_024.jsonl",content)
if __name__=="__main__":unittest.main()
