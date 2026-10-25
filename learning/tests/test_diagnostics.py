import errno
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from flask import Flask, abort
from learning.diagnostics import init_diagnostics
from learning.storage.json_progress import JsonProgressStore, ProgressStorageError


class StorageFailureTests(unittest.TestCase):
    def test_transient_read_permission_recovers(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder,retry_delays=(0,0))
            with patch.object(Path,"read_text",side_effect=[PermissionError(errno.EACCES,"locked"),'{"mastery":{"coffee":true}}']) as read:
                self.assertTrue(store.load("local-user","es",{})["mastery"]["coffee"])
                self.assertEqual(read.call_count,2)

    def test_permanent_read_failure_is_not_a_new_empty_profile(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder,retry_delays=(0,0))
            denied=PermissionError(errno.EACCES,"denied")
            with patch.object(Path,"read_text",side_effect=denied) as read:
                with self.assertRaises(ProgressStorageError) as caught:
                    store.load("local-user","es",{})
            self.assertEqual(read.call_count,3)
            self.assertEqual(caught.exception.operation,"read")
            self.assertEqual(caught.exception.errno,13)
            self.assertIs(caught.exception.__cause__,denied)

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder,retry_delays=(0,))
            store.save("local-user","es",{"score":12})
            path=store.path("local-user","es")
            before=path.read_bytes()
            with patch("learning.storage.json_progress.os.replace",side_effect=PermissionError(13,"locked")):
                with self.assertRaises(ProgressStorageError) as caught:
                    store.save("local-user","es",{"score":20})
            self.assertEqual(caught.exception.operation,"replace")
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(path.parent.glob("*.tmp")),[])

    def test_transient_replace_recovers_without_duplicate_updates(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder,retry_delays=(0,))
            import os
            replace=os.replace
            attempts=[]
            def flaky(source,target):
                attempts.append(1)
                if len(attempts)==1:
                    raise PermissionError(13,"sharing conflict")
                return replace(source,target)
            with patch("learning.storage.json_progress.os.replace",side_effect=flaky):
                store.save("local-user","es",{"score":1})
            self.assertEqual(store.load("local-user","es",{}),{"score":1})
            self.assertEqual(len(attempts),2)

    def test_corrupt_or_non_object_data_is_not_overwritten(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder)
            path=store.path("local-user","es")
            path.parent.mkdir(parents=True)
            for raw in [b'{broken',b'[]',b'\xff']:
                path.write_bytes(raw)
                with self.assertRaises(ProgressStorageError) as caught:
                    store.load("local-user","es",{})
                self.assertEqual(caught.exception.operation,"decode")
                self.assertEqual(path.read_bytes(),raw)

    def test_storage_check_does_not_modify_progress(self):
        with TemporaryDirectory() as folder:
            store=JsonProgressStore(folder)
            store.save("local-user","es",{"score":16})
            path=store.path("local-user","es")
            before=path.read_bytes()
            store.check_access("local-user","es")
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(path.parent.iterdir()),[path])


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.path=Path(self.temp.name)/"app.jsonl"
        templates=Path(__file__).resolve().parents[1]/"templates"
        self.app=Flask("diagnostics_"+uuid4().hex,template_folder=str(templates))
        self.app.config.update(TESTING=True,DIAGNOSTICS_LOG_PATH=self.path,DIAGNOSTICS_CONSOLE=False)
        init_diagnostics(self.app)
        @self.app.get("/menu", endpoint="learning.menu")
        def menu():
            return "OK"
        @self.app.route("/broken",methods=["GET","POST"])
        def broken():
            try:
                raise PermissionError(13,"denied",r"C:\private\es.json")
            except PermissionError as error:
                raise ProgressStorageError("read",r"C:\private\es.json",error) from error
        @self.app.get("/bug")
        def bug():
            raise RuntimeError("test failure")
        @self.app.get("/forbidden")
        def forbidden():
            abort(403)
        self.app.add_url_rule("/learning/static/<path:filename>",endpoint="learning.static",view_func=lambda filename: "")
        self.client=self.app.test_client()

    def tearDown(self):
        handler=self.app.extensions["diagnostics"]["handler"]
        self.app.logger.removeHandler(handler)
        handler.close()
        self.temp.cleanup()

    def events(self):
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_storage_error_links_browser_reference_to_exact_traceback(self):
        response=self.client.post("/broken?token=hidden-query",json={"password":"hidden-body"})
        self.assertEqual(response.status_code,503)
        data=response.get_json()
        self.assertEqual(data["request_id"],response.headers["X-Request-ID"])
        self.assertNotIn("C:\\private",data["error"])
        entry=next(e for e in self.events() if e["event"]=="progress_storage_failed")
        self.assertEqual(entry["request_id"],data["request_id"])
        self.assertEqual(entry["operation"],"read")
        self.assertEqual(entry["storage_path"],r"C:\private\es.json")
        self.assertIn("PermissionError",entry["traceback"])
        self.assertIn("broken",entry["traceback"])
        self.assertNotIn("hidden-query",str(self.events()))
        self.assertNotIn("hidden-body",str(self.events()))
        report=self.app.test_cli_runner().invoke(args=["show-error",data["request_id"]])
        self.assertEqual(report.exit_code,0)
        self.assertIn("PermissionError",report.output)
        self.assertIn("broken",report.output)

    def test_html_errors_and_expected_http_statuses(self):
        response=self.client.get("/broken")
        self.assertEqual(response.status_code,503)
        self.assertIn(response.headers["X-Request-ID"],response.get_data(as_text=True))
        self.assertNotIn("Traceback",response.get_data(as_text=True))
        self.assertEqual(self.client.get("/bug").status_code,500)
        self.assertEqual(self.client.get("/forbidden").status_code,403)
        self.assertEqual(self.client.get("/missing").status_code,404)
        ok=self.client.get("/menu")
        self.assertEqual(ok.status_code,200)
        self.assertTrue(ok.headers["X-Request-ID"])

    def test_browser_report_validation_and_rate_limit(self):
        for _ in range(20):
            response=self.client.post("/diagnostics/browser-error",json={"kind":"script_error","file":"/static/word-match.js","line":42,"column":7,"ignored":"hidden-content"})
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.post("/diagnostics/browser-error",json={"kind":"script_error"}).status_code,429)
        self.assertNotIn("hidden-content",str(self.events()))
        self.assertTrue(any(e.get("line")==42 and e.get("file")=="/static/word-match.js" for e in self.events()))
        self.assertEqual(self.client.post("/diagnostics/browser-error",json={"kind":"anything"}).status_code,400)
        self.assertEqual(self.client.post("/diagnostics/browser-error",json={"kind":"script_error","large":"x"*3000}).status_code,413)

    def test_error_reference_survives_error_template_failure(self):
        with patch("learning.diagnostics.render_template",side_effect=RuntimeError("template unavailable")):
            response=self.client.get("/broken")
        self.assertEqual(response.status_code,503)
        self.assertIn(response.headers["X-Request-ID"],response.get_data(as_text=True))
        self.assertTrue(any(e["event"]=="error_page_render_failed" for e in self.events()))


if __name__=="__main__":
    unittest.main()
