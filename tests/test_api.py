# -*- coding: utf-8 -*-
"""HTTP API 端到端测试：临时端口起真实服务。"""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from costapp.reports import seed_demo
from costapp.server import _make_handler
from costapp.store import Store


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.store = Store(cls.dir)
        seed_demo(cls.store)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(cls.store))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path):
        with urllib.request.urlopen(self._url(path), timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def _post(self, path, body):
        req = urllib.request.Request(
            self._url(path),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_01_health_and_lists(self):
        self.assertEqual(self._get("/api/health")[0], 200)
        status, ev = self._get("/api/evidence")
        self.assertEqual(status, 200)
        self.assertEqual(len(ev["items"]), 3)
        status, reps = self._get("/api/reports")
        self.assertEqual(len(reps["items"]), 9)

    def test_02_report_conservation_via_api(self):
        _, rep = self._get("/api/report?id=RPT-EV3-CAUSAL")
        self.assertTrue(rep["body"]["conservation"]["balanced"])
        self.assertEqual(rep["body"]["conservation"]["total_cost_cents"], 4181)

    def test_03_create_idempotent(self):
        s1, r1 = self._post("/api/report", {"version": "EV3", "scheme": "CAUSAL"})
        s2, r2 = self._post("/api/report", {"version": "EV3", "scheme": "CAUSAL"})
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(r1["report_hash"], r2["report_hash"])

    def test_04_confirm_then_conflict(self):
        self._post("/api/report/confirm", {"id": "RPT-EV3-MARGINAL"})
        code, body = self._post("/api/report/confirm", {"id": "RPT-EV3-MARGINAL"})
        self.assertEqual(code, 409)
        self.assertIn("不可回退", body["error"])
        # 播种时已确认的旧报告同样拒绝
        code, _ = self._post("/api/report/confirm",
                             {"id": "RPT-EV1-OCCUPANCY"})
        self.assertEqual(code, 409)

    def test_05_missing_and_static(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/api/report?id=NOPE")
        self.assertEqual(ctx.exception.code, 404)
        with urllib.request.urlopen(self._url("/"), timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn("text/html", r.headers["Content-Type"])
        with urllib.request.urlopen(self._url("/static/app.js"), timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_06_verify_and_diff(self):
        _, v = self._get("/api/verify")
        self.assertTrue(v["ok"])
        _, d = self._get("/api/diff?a=RPT-EV1-OCCUPANCY&b=RPT-EV3-OCCUPANCY")
        self.assertEqual(d["total_diff"], 213)
        self.assertEqual(sum(r["diff_cents"] for r in d["rows"]), 213)


if __name__ == "__main__":
    unittest.main()
