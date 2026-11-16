# -*- coding: utf-8 -*-
"""虚构数据模拟器：确定性生成三个证据版的任务图、执行记录、资源账单与事件。

场景时间线：
  EV1  批次创建：9 个任务的 DAG、14 次执行（含失败重试/投机备份），
       其中服务聚合任务账单迟到，先按标准单价预提。
  EV2  追溯更正：任务图补录一条需求边；迟到账单到达，预提被真实账单替代。
  EV3  修订与重算：云厂商修订一张历史账单；经营分析产品发起一次并行重算。

所有数据均为虚构，仅用于演示分摊与修订追溯。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    AttemptStatus,
    AttemptType,
    BillStatus,
    tariff_amount,
)

# 固定时间戳，保证同输入同哈希、报告可复算
TS_EV1 = "2026-11-10T08:00:00+08:00"
TS_EV2 = "2026-11-12T09:30:00+08:00"
TS_EV3 = "2026-11-14T10:15:00+08:00"

PRODUCTS = [
    {"id": "P_RISK", "name": "实时风控", "owner": "风控平台部"},
    {"id": "P_REC", "name": "智能推荐", "owner": "增长算法部"},
    {"id": "P_BI", "name": "经营分析", "owner": "数据经营部"},
]

TASKS = [
    {"id": "t_ingest_log", "name": "日志接入", "layer": 1},
    {"id": "t_ingest_order", "name": "订单接入", "layer": 1},
    {"id": "t_clean_log", "name": "日志清洗", "layer": 2},
    {"id": "t_clean_order", "name": "订单清洗", "layer": 2},
    {"id": "t_join_wide", "name": "宽表关联", "layer": 3},
    {"id": "t_feature_risk", "name": "风控特征加工", "layer": 4},
    {"id": "t_feature_rec", "name": "推荐特征加工", "layer": 4},
    {"id": "t_aggregate_serving", "name": "服务聚合", "layer": 5},
    {"id": "t_report_bi", "name": "经营报表", "layer": 6},
]

# EV1 的任务边：(上游, 下游, 需求量)
EDGES_V1 = [
    ("t_ingest_log", "t_clean_log", 250),
    ("t_ingest_order", "t_clean_order", 180),
    ("t_clean_log", "t_join_wide", 240),
    ("t_clean_order", "t_join_wide", 170),
    ("t_join_wide", "t_feature_risk", 200),
    ("t_join_wide", "t_feature_rec", 120),
    ("t_feature_risk", "t_aggregate_serving", 190),
    ("t_feature_rec", "t_aggregate_serving", 110),
    ("t_aggregate_serving", "t_report_bi", 100),
]

# EV2 追溯更正补录的边
EDGE_ADDED_EV2 = ("t_feature_rec", "t_report_bi", 60)

# 产品对终端任务的消费量（驱动需求权重）
TERMINAL_DEMAND = [
    {"product_id": "P_RISK", "task_id": "t_feature_risk", "demand": 600},
    {"product_id": "P_REC", "task_id": "t_feature_rec", "demand": 300},
    {"product_id": "P_BI", "task_id": "t_report_bi", "demand": 100},
]


def _attempt(id_, task, type_, status, cpu, mem, note,
             caused_by_product: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": id_,
        "task_id": task,
        "type": type_,
        "status": status,
        "cpu_seconds": cpu,
        "mem_gb_seconds": mem,
        "accrual_cents": tariff_amount(cpu, mem),
        "caused_by_product": caused_by_product,
        "note": note,
    }


def _all_attempts() -> List[Dict[str, Any]]:
    A = AttemptType
    S = AttemptStatus
    return [
        _attempt("a_ingest_log_1", "t_ingest_log", A.INITIAL, S.SUCCESS,
                 100, 200, "首次执行成功"),
        _attempt("a_ingest_order_1", "t_ingest_order", A.INITIAL, S.SUCCESS,
                 80, 160, "首次执行成功"),
        _attempt("a_clean_log_1", "t_clean_log", A.INITIAL, S.FAILED,
                 40, 80, "日志字段缺失导致解析失败", caused_by_product="P_RISK"),
        _attempt("a_clean_log_2", "t_clean_log", A.RETRY, S.SUCCESS,
                 60, 120, "补数后重试成功"),
        _attempt("a_clean_order_1", "t_clean_order", A.INITIAL, S.SUCCESS,
                 50, 100, "首次执行成功"),
        _attempt("a_clean_order_2", "t_clean_order", A.SPEC, S.SPEC_KILLED,
                 40, 80, "慢节点投机备份，原尝试先完成，备份被杀死"),
        _attempt("a_join_wide_1", "t_join_wide", A.INITIAL, S.FAILED,
                 120, 240, "Shuffle 服务瞬时不可用（平台基础设施故障）"),
        _attempt("a_join_wide_2", "t_join_wide", A.RETRY, S.SUCCESS,
                 150, 300, "基础设施恢复后重试成功"),
        _attempt("a_feature_risk_1", "t_feature_risk", A.INITIAL, S.SUCCESS,
                 200, 400, "首次执行成功"),
        _attempt("a_feature_risk_2", "t_feature_risk", A.SPEC, S.SPEC_KILLED,
                 180, 360, "投机备份在长尾阶段被杀死"),
        _attempt("a_feature_rec_1", "t_feature_rec", A.INITIAL, S.FAILED,
                 30, 60, "推荐侧 Schema 变更未同步", caused_by_product="P_REC"),
        _attempt("a_feature_rec_2", "t_feature_rec", A.RETRY, S.SUCCESS,
                 100, 200, "Schema 修复后重试成功"),
        _attempt("a_aggregate_1", "t_aggregate_serving", A.INITIAL, S.SUCCESS,
                 70, 140, "首次执行成功；出账延迟，EV1 仅预提"),
        _attempt("a_report_bi_1", "t_report_bi", A.INITIAL, S.SUCCESS,
                 60, 120, "首次执行成功"),
    ]


def _bill(id_, attempt_id, amount, status, received_version, note,
          supersedes: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": id_,
        "attempt_id": attempt_id,
        "amount_cents": amount,
        "status": status,
        "received_version": received_version,
        "supersedes": supersedes,
        "note": note,
    }


def _bills_v1() -> List[Dict[str, Any]]:
    B = BillStatus
    # 金额 = 对应尝试的标准单价预提额（本版账单恰好与预提一致）
    return [
        _bill("b_ingest_log_1", "a_ingest_log_1", 310, B.BILLED, "EV1", "云资源账单"),
        _bill("b_ingest_order_1", "a_ingest_order_1", 248, B.BILLED, "EV1", "云资源账单"),
        _bill("b_clean_log_1", "a_clean_log_1", 124, B.BILLED, "EV1", "失败尝试仍计费"),
        _bill("b_clean_log_2", "a_clean_log_2", 186, B.BILLED, "EV1", "云资源账单"),
        _bill("b_clean_order_1", "a_clean_order_1", 155, B.BILLED, "EV1", "云资源账单"),
        _bill("b_clean_order_2", "a_clean_order_2", 124, B.BILLED, "EV1",
              "投机备份被杀死但资源已占用，全额计费"),
        _bill("b_join_wide_1", "a_join_wide_1", 372, B.BILLED, "EV1", "失败尝试仍计费"),
        _bill("b_join_wide_2", "a_join_wide_2", 465, B.BILLED, "EV1", "云资源账单"),
        _bill("b_feature_risk_1", "a_feature_risk_1", 620, B.BILLED, "EV1", "云资源账单"),
        _bill("b_feature_risk_2", "a_feature_risk_2", 558, B.BILLED, "EV1",
              "投机备份被杀死但资源已占用，全额计费"),
        _bill("b_feature_rec_1", "a_feature_rec_1", 93, B.BILLED, "EV1", "失败尝试仍计费"),
        _bill("b_feature_rec_2", "a_feature_rec_2", 310, B.BILLED, "EV1", "云资源账单"),
        # a_aggregate_1 无账单：账单迟到，引擎按标准单价预提 217
        _bill("b_report_bi_1", "a_report_bi_1", 186, B.BILLED, "EV1", "云资源账单"),
    ]


def _snapshot(version: str, ts: str, description: str,
              edges: List[List[Any]], bills: List[Dict[str, Any]],
              attempts: List[Dict[str, Any]], events: List[Dict[str, Any]],
              prev_hash: Optional[str]) -> Dict[str, Any]:
    return {
        "version": version,
        "generated_at": ts,
        "description": description,
        "pricing": {
            "cpu_cent_per_sec": 3,
            "mem_cent_per_20gbs": 1,
            "currency": "CNY-cent",
        },
        "products": PRODUCTS,
        "tasks": TASKS,
        "edges": [{"upstream": u, "downstream": d, "demand": w}
                  for (u, d, w) in edges],
        "terminal_demand": TERMINAL_DEMAND,
        "attempts": attempts,
        "bills": bills,
        "events": events,
        "previous_evidence_hash": prev_hash,
    }


def build_all() -> List[Dict[str, Any]]:
    """生成 EV1→EV2→EV3 三个证据快照（不含哈希，哈希由存储层固化时写入）。"""
    from .models import BillStatus

    attempts_v1 = _all_attempts()
    bills_v1 = _bills_v1()

    events_v1 = [
        {"id": "EVT-001", "version": "EV1", "time": TS_EV1,
         "type": "BATCH_OPEN", "summary": "11 月上旬成本批次建立，任务图 v1 接入"},
        {"id": "EVT-002", "version": "EV1", "time": TS_EV1,
         "type": "USAGE_CLOSED",
         "summary": "14 次执行尝试用量封账：含 3 次失败、2 次投机备份"},
        {"id": "EVT-003", "version": "EV1", "time": TS_EV1,
         "type": "BILL_PENDING",
         "summary": "服务聚合任务账单未到，按标准单价预提 ¥2.17，不抹除用量"},
    ]
    ev1 = _snapshot(
        "EV1", TS_EV1,
        "首版证据：任务图 v1、执行用量封账，1 张账单迟到按单价预提",
        EDGES_V1, bills_v1, attempts_v1, events_v1, None)

    # ---- EV2：关系更正 + 迟到账单
    edges_v2 = EDGES_V1 + [EDGE_ADDED_EV2]
    bills_v2 = [dict(b) for b in bills_v1]
    bills_v2.append(_bill(
        "b_aggregate_1_late", "a_aggregate_1", 235, BillStatus.BILLED, "EV2",
        "迟到真实账单：峰时单价上浮，替代 EV1 预提 ¥2.17"))
    events_v2 = list(events_v1) + [
        {"id": "EVT-101", "version": "EV2", "time": TS_EV2,
         "type": "GRAPH_CORRECTION",
         "summary": "任务关系追溯更正：补录 推荐特征加工→经营报表 需求边（需求量 60）"},
        {"id": "EVT-102", "version": "EV2", "time": TS_EV2,
         "type": "LATE_BILL",
         "summary": "服务聚合迟到账单到达 ¥2.35，预提 ¥2.17 被替代，差额 ¥0.18 入账"},
    ]
    ev2 = _snapshot(
        "EV2", TS_EV2,
        "第二版证据：补录任务需求边；迟到账单替代预提",
        edges_v2, bills_v2, attempts_v1, events_v2, None)

    # ---- EV3：账单修订 + 并行重算
    bills_v3 = [dict(b) for b in bills_v2]
    for b in bills_v3:
        if b["id"] == "b_feature_risk_1":
            b["status"] = BillStatus.SUPERSEDED
            b["note"] += "；EV3 被云厂商修订账单替代"
    bills_v3.append(_bill(
        "b_feature_risk_1_rev", "a_feature_risk_1", 660, BillStatus.BILLED, "EV3",
        "云厂商修订账单：补收跨区流量附加费 ¥0.40",
        supersedes="b_feature_risk_1"))

    attempts_v3 = [dict(a) for a in attempts_v1]
    attempts_v3.append(_attempt(
        "a_report_bi_2", "t_report_bi", AttemptType.RECOMPUTE, AttemptStatus.SUCCESS,
        50, 100, "经营口径修订，业务发起并行重算", caused_by_product="P_BI"))
    bills_v3.append(_bill(
        "b_report_bi_2", "a_report_bi_2", 155, BillStatus.BILLED, "EV3",
        "并行重算用量账单"))

    events_v3 = list(events_v2) + [
        {"id": "EVT-201", "version": "EV3", "time": TS_EV3,
         "type": "BILL_REVISION",
         "summary": "风控特征账单修订 ¥6.20→¥6.60，旧账单标记替代，不删除历史"},
        {"id": "EVT-202", "version": "EV3", "time": TS_EV3,
         "type": "PARALLEL_RECOMPUTE",
         "summary": "经营分析发起并行重算，新增用量 ¥1.55，独立归类为重算成本"},
    ]
    ev3 = _snapshot(
        "EV3", TS_EV3,
        "第三版证据：历史账单修订；业务并行重算追加用量",
        edges_v2, bills_v3, attempts_v3, events_v3, None)

    return [ev1, ev2, ev3]
