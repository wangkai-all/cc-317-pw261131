# -*- coding: utf-8 -*-
"""存储层测试：写一次、哈希链、确认不可回退、幂等、篡改检测。"""
import json
import os
import shutil
import tempfile
import unittest

from costapp.models import Scheme, sha256_hex
from costapp.reports import diff_reports, seed_demo
from costapp.store import ConflictError, Store
from costapp.simulator import build_all


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = Store(self.dir)
        seed_demo(self.store)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_01_seed_idempotent(self):
        before = len(self.store._read_ledger())
        seed_demo(self.store)
        after = len(self.store._read_ledger())
        self.assertEqual(before, after)  # 重复播种不追加台账
        self.assertEqual(len(self.store.list_evidence()), 3)
        self.assertEqual(len(self.store.list_reports()), 9)

    def test_02_evidence_write_once(self):
        snap = build_all()[0]
        with self.assertRaises(ConflictError):
            # 直接写同路径必须被写一次策略拒绝
            self.store._write_json(
                os.path.join(self.store.evidence_dir, "EV1.json"), snap)

    def test_03_confirm_then_no_revert(self):
        rid = "RPT-EV3-OCCUPANCY"
        rec = self.store.get_report(rid)
        self.assertEqual(rec["status"], "DRAFT")
        self.store.confirm_report(rid)
        self.assertEqual(self.store.get_report(rid)["status"], "CONFIRMED")
        with self.assertRaises(ConflictError):
            self.store.confirm_report(rid)  # 重复确认被拒
        # 报告文件内容未因确认而改变（状态只存在于仅追加台账）
        rec2 = self.store.get_report(rid)
        self.assertEqual(rec["report_hash"], rec2["report_hash"])

    def test_04_legacy_confirmed_untouched_by_new_version(self):
        """EV3 新证据不改变 EV1/EV2 已确认报告的任何字节与状态。"""
        old = self.store.get_report("RPT-EV1-OCCUPANCY")
        self.assertEqual(old["status"], "CONFIRMED")
        path = os.path.join(self.store.report_dir, "RPT-EV1-OCCUPANCY.json")
        mtime = os.path.getmtime(path)
        # 再次播种（模拟后续批次到来）
        seed_demo(self.store)
        self.assertEqual(os.path.getmtime(path), mtime)
        self.assertEqual(
            self.store.get_report("RPT-EV1-OCCUPANCY")["status"], "CONFIRMED")

    def test_05_verify_clean(self):
        v = self.store.verify_integrity()
        self.assertTrue(v["ok"], v["problems"])

    def test_06_tamper_detection(self):
        """篡改证据载荷后必须被完整性校验发现。"""
        path = os.path.join(self.store.evidence_dir, "EV2.json")
        env = json.load(open(path, encoding="utf-8"))
        env["payload"]["description"] = "被恶意修改"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(env, f, ensure_ascii=False)
        v = self.store.verify_integrity()
        self.assertFalse(v["ok"])
        self.assertTrue(any("EV2" in p for p in v["problems"]))

    def test_07_ledger_chain_tamper(self):
        """篡改台账条目后哈希链断裂可检测。"""
        lines = open(self.store.ledger_path, encoding="utf-8").read().splitlines()
        obj = json.loads(lines[2])
        obj["payload"]["note"] = "x"
        lines[2] = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        open(self.store.ledger_path, "w", encoding="utf-8").write(
            "\n".join(lines) + "\n")
        v = self.store.verify_integrity()
        self.assertFalse(v["ok"])

    def test_08_diff_versions(self):
        d = diff_reports(self.store, "RPT-EV1-OCCUPANCY",
                         "RPT-EV3-OCCUPANCY")
        self.assertEqual(d["total_diff"],
                         (235 - 217) + (660 - 620) + 155)
        # 产品行 + 公共池行
        self.assertEqual(len(d["rows"]), 4)
        # 产品+公共池差额之和 = 总额差额
        self.assertEqual(sum(r["diff_cents"] for r in d["rows"]),
                         d["total_diff"])

    def test_09_report_hash_binds_evidence(self):
        rec = self.store.get_report("RPT-EV2-CAUSAL")
        env = self.store.get_evidence("EV2")
        self.assertEqual(rec["body"]["evidence_hash"], env["evidence_hash"])


if __name__ == "__main__":
    unittest.main()
