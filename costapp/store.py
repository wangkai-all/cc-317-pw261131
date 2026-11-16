# -*- coding: utf-8 -*-
"""追加式存储与哈希链台账。

设计要点：
  * 证据与报告均写一次（write-once），任何修订只产生新版本，不覆盖旧文件；
  * ledger.jsonl 是仅追加的哈希链，确认动作只追加事件，没有“取消确认”入口，
    因此旧计算的确认结果不可能被回退；
  * 报告哈希绑定证据哈希与方案，重算必须复现同一哈希，否则完整性校验报警；
  * 所有 JSON 规范化后取 SHA-256，跨机器可复现。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

from .allocator import compute_report
from .models import canonical_json, sha256_hex, short


class StoreError(Exception):
    pass


class ConflictError(StoreError):
    pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


class Store:
    def __init__(self, root: str):
        self.root = root
        self.evidence_dir = os.path.join(root, "evidence")
        self.report_dir = os.path.join(root, "reports")
        self.ledger_path = os.path.join(root, "ledger.jsonl")
        os.makedirs(self.evidence_dir, exist_ok=True)
        os.makedirs(self.report_dir, exist_ok=True)
        if not os.path.exists(self.ledger_path):
            open(self.ledger_path, "a", encoding="utf-8").close()

    # ------------------------------------------------------------ 底层读写
    @staticmethod
    def _write_json(path: str, obj: Any) -> None:
        if os.path.exists(path):
            raise ConflictError(f"文件已存在，写一次策略禁止覆盖: {path}")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

    @staticmethod
    def _read_json(path: str) -> Any:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _append_ledger(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        lines = self._read_ledger()
        prev_hash = lines[-1]["hash"] if lines else "GENESIS"
        seq = len(lines) + 1
        body = {
            "seq": seq,
            "time": _now_iso(),
            "type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        body["hash"] = sha256_hex({k: v for k, v in body.items()})
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(canonical_json(body) + "\n")
        return body

    def _read_ledger(self) -> List[Dict[str, Any]]:
        out = []
        with open(self.ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    # ------------------------------------------------------------ 证据
    def add_evidence(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """固化一个证据版；同版本重复导入幂等返回既有记录。"""
        version = snapshot["version"]
        path = os.path.join(self.evidence_dir, f"{version}.json")
        if os.path.exists(path):
            return self._read_json(path)

        stored = self.list_evidence()
        prev_hash = stored[-1]["evidence_hash"] if stored else "GENESIS"
        envelope = {
            "version": version,
            "previous_evidence_hash": prev_hash,
            "evidence_hash": sha256_hex(snapshot),
            "payload": snapshot,
        }
        self._write_json(path, envelope)
        self._append_ledger("EVIDENCE_FROZEN", {
            "version": version,
            "evidence_hash": envelope["evidence_hash"],
            "previous_evidence_hash": prev_hash,
        })
        return envelope

    def list_evidence(self) -> List[Dict[str, Any]]:
        files = sorted(f for f in os.listdir(self.evidence_dir) if f.endswith(".json"))
        return [self._read_json(os.path.join(self.evidence_dir, f)) for f in files]

    def get_evidence(self, version: str) -> Dict[str, Any]:
        path = os.path.join(self.evidence_dir, f"{version}.json")
        if not os.path.exists(path):
            raise StoreError(f"证据版不存在: {version}")
        return self._read_json(path)

    # ------------------------------------------------------------ 报告
    @staticmethod
    def report_id(version: str, scheme: str) -> str:
        return f"RPT-{version}-{scheme}"

    def create_report(self, version: str, scheme: str) -> Dict[str, Any]:
        """在固定证据版上生成报告；同一 (版本, 方案) 幂等且内容不可变。"""
        envelope = self.get_evidence(version)
        rid = self.report_id(version, scheme)
        path = os.path.join(self.report_dir, f"{rid}.json")
        if os.path.exists(path):
            return self._read_json(path)

        body = compute_report(envelope["payload"], scheme)
        body["report_id"] = rid
        body["evidence_hash"] = envelope["evidence_hash"]
        report_hash = sha256_hex(body)
        record = {
            "report_id": rid,
            "report_hash": report_hash,
            "short_hash": short(report_hash),
            "status": "DRAFT",
            "body": body,
        }
        self._write_json(path, record)
        self._append_ledger("REPORT_CREATED", {
            "report_id": rid,
            "version": version,
            "scheme": scheme,
            "report_hash": report_hash,
            "evidence_hash": envelope["evidence_hash"],
        })
        return record

    def list_reports(self) -> List[Dict[str, Any]]:
        # 状态由仅追加台账派生，报告文件本身永不修改
        confirmed = set()
        for e in self._read_ledger():
            if e["type"] == "REPORT_CONFIRMED":
                confirmed.add(e["payload"]["report_id"])
        files = sorted(f for f in os.listdir(self.report_dir) if f.endswith(".json"))
        out = []
        for f in files:
            rec = self._read_json(os.path.join(self.report_dir, f))
            rec = dict(rec)
            rec["status"] = "CONFIRMED" if rec["report_id"] in confirmed else "DRAFT"
            out.append(rec)
        return out

    def get_report(self, rid: str) -> Dict[str, Any]:
        path = os.path.join(self.report_dir, f"{rid}.json")
        if not os.path.exists(path):
            raise StoreError(f"报告不存在: {rid}")
        rec = dict(self._read_json(path))
        confirmed = any(
            e["type"] == "REPORT_CONFIRMED" and e["payload"]["report_id"] == rid
            for e in self._read_ledger())
        rec["status"] = "CONFIRMED" if confirmed else "DRAFT"
        return rec

    def confirm_report(self, rid: str, note: str = "") -> Dict[str, Any]:
        """确认报告。已确认报告拒绝任何状态变更——旧计算不可回退。"""
        rec = self.get_report(rid)
        if rec["status"] == "CONFIRMED":
            raise ConflictError(
                f"{rid} 已确认并固化，确认结果不可回退或重复确认")
        event = self._append_ledger("REPORT_CONFIRMED", {
            "report_id": rid,
            "report_hash": rec["report_hash"],
            "evidence_hash": rec["body"]["evidence_hash"],
            "note": note,
        })
        return self.get_report(rid), event

    # ------------------------------------------------------------ 完整性
    def verify_integrity(self) -> Dict[str, Any]:
        problems: List[str] = []

        # 1) 证据哈希与证据链
        prev = "GENESIS"
        for env in self.list_evidence():
            if sha256_hex(env["payload"]) != env["evidence_hash"]:
                problems.append(f"证据 {env['version']} 内容与哈希不符（被篡改或损坏）")
            if env["previous_evidence_hash"] != prev:
                problems.append(f"证据 {env['version']} 前序哈希断链")
            prev = env["evidence_hash"]

        # 2) 报告哈希 + 与证据的可复算性
        all_reports = self.list_reports()
        for rec in all_reports:
            body = rec["body"]
            if sha256_hex(body) != rec["report_hash"]:
                problems.append(f"报告 {rec['report_id']} 内容与哈希不符")
                continue
            env = self.get_evidence(body["evidence_version"])
            if env["evidence_hash"] != body["evidence_hash"]:
                problems.append(
                    f"报告 {rec['report_id']} 绑定的证据哈希与当前证据不一致")
            else:
                rebuilt = compute_report(env["payload"], body["scheme"])
                rebuilt["report_id"] = rec["report_id"]
                rebuilt["evidence_hash"] = body["evidence_hash"]
                if sha256_hex(rebuilt) != rec["report_hash"]:
                    problems.append(
                        f"报告 {rec['report_id']} 无法由固定证据复现（计算逻辑漂移）")
            if not body["conservation"]["balanced"]:
                problems.append(f"报告 {rec['report_id']} 守恒校验不平衡")

        # 3) 台账哈希链
        ledger = self._read_ledger()
        prev = "GENESIS"
        for e in ledger:
            if e["prev_hash"] != prev:
                problems.append(f"台账第 {e['seq']} 条前序哈希断链")
            expect = sha256_hex({k: v for k, v in e.items() if k != "hash"})
            if expect != e["hash"]:
                problems.append(f"台账第 {e['seq']} 条哈希不符")
            prev = e["hash"]

        return {
            "ok": not problems,
            "problems": problems,
            "evidence_count": len(self.list_evidence()),
            "report_count": len(all_reports),
            "ledger_entries": len(ledger),
        }
