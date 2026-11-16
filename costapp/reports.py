# -*- coding: utf-8 -*-
"""报告组合：证据概览、跨版本修订比较、演示数据播种。"""
from __future__ import annotations

from typing import Any, Dict, List

from .allocator import PUBLIC_POOL, compute_report
from .models import ALL_SCHEMES, CATEGORY_LABEL, Scheme
from .simulator import build_all
from .store import Store


def evidence_overview(store: Store) -> List[Dict[str, Any]]:
    """每个证据版的摘要：总额、账单/预提构成、事件、关联报告状态。"""
    reports_by_version: Dict[str, List[Dict[str, Any]]] = {}
    for r in store.list_reports():
        reports_by_version.setdefault(r["body"]["evidence_version"], []).append(r)

    out = []
    for env in store.list_evidence():
        snap = env["payload"]
        # 用占用方案做摘要口径（仅摘要，不影响各方案独立报告）
        rep = compute_report(snap, Scheme.OCCUPANCY)
        out.append({
            "version": snap["version"],
            "description": snap["description"],
            "generated_at": snap["generated_at"],
            "evidence_hash": env["evidence_hash"],
            "previous_evidence_hash": env["previous_evidence_hash"],
            "event_count": len(snap["events"]),
            "attempt_count": len(snap["attempts"]),
            "bill_count": len(snap["bills"]),
            "edge_count": len(snap["edges"]),
            "conservation": rep["conservation"],
            "events": snap["events"],
            "reports": [
                {"report_id": r["report_id"], "scheme": r["body"]["scheme"],
                 "status": r["status"], "report_hash": r["report_hash"],
                 "short_hash": r["short_hash"]}
                for r in reports_by_version.get(snap["version"], [])
            ],
        })
    return out


def diff_reports(store: Store, rid_a: str, rid_b: str) -> Dict[str, Any]:
    """两份报告逐产品/公共池、逐成本类别比较（可跨版本或跨方案）。"""
    a, b = store.get_report(rid_a), store.get_report(rid_b)
    ba, bb = a["body"], b["body"]

    def index_products(body):
        d = {p["product_id"]: p for p in body["products"]}
        d[PUBLIC_POOL] = {"product_id": PUBLIC_POOL,
                          "name": body["public_pool"]["name"],
                          "total_cents": body["public_pool"]["total_cents"],
                          "by_category": body["public_pool"]["by_category"]}
        return d

    ia, ib = index_products(ba), index_products(bb)
    keys = sorted(set(ia) | set(ib))
    rows = []
    for k in keys:
        pa, pb = ia.get(k), ib.get(k)
        rows.append({
            "key": k,
            "name": (pa or pb)["name"],
            "total_a": pa["total_cents"] if pa else 0,
            "total_b": pb["total_cents"] if pb else 0,
            "diff_cents": (pb["total_cents"] if pb else 0)
                          - (pa["total_cents"] if pa else 0),
            "category_diff": {
                c: ((pb["by_category"][c] if pb else 0)
                    - (pa["by_category"][c] if pa else 0))
                for c in CATEGORY_LABEL
            },
        })
    return {
        "a": {"report_id": rid_a, "version": ba["evidence_version"],
              "scheme": ba["scheme"], "hash": a["report_hash"],
              "status": a["status"]},
        "b": {"report_id": rid_b, "version": bb["evidence_version"],
              "scheme": bb["scheme"], "hash": b["report_hash"],
              "status": b["status"]},
        "total_a": ba["conservation"]["total_cost_cents"],
        "total_b": bb["conservation"]["total_cost_cents"],
        "total_diff": (bb["conservation"]["total_cost_cents"]
                       - ba["conservation"]["total_cost_cents"]),
        "rows": rows,
    }


def seed_demo(store: Store) -> Dict[str, Any]:
    """播种：三版证据 + 每版三份方案报告；历史版本报告预先确认以演示不可回退。

    已存在的数据全部走幂等路径，重复执行不产生重复台账事件。
    """
    snapshots = build_all()
    for snap in snapshots:
        store.add_evidence(snap)

    created = []
    for snap in snapshots:
        for scheme in ALL_SCHEMES:
            rec = store.create_report(snap["version"], scheme)
            created.append((rec["report_id"], rec["status"]))

    # EV1/EV2 的占用方案报告是“已确认的旧计算”，EV3 保持草稿
    for rid, _ in created:
        if rid in ("RPT-EV1-OCCUPANCY", "RPT-EV2-OCCUPANCY"):
            try:
                store.confirm_report(rid, note="批次封账确认（演示）")
            except Exception:
                pass  # 已确认则幂等忽略

    return {"reports": [{"report_id": r, "status": s} for r, s in created]}
