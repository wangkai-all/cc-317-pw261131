# -*- coding: utf-8 -*-
"""分摊引擎。

核心原则：
  1. 共享成本只记一次——按任务的受益产品集合分摊，绝不在每个产品全额重复；
  2. 未完成/失败/投机/重算用量不抹除——逐尝试确定有效账单或预提，全额进入对账；
  3. 所有分摊走最大余数法，整数金额严格守恒；
  4. 每个分摊行附带可解释原因，支持网页端逐行追溯。
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Tuple

from .models import (
    ALL_SCHEMES,
    CATEGORY_LABEL,
    CostCategory,
    Scheme,
    largest_remainder,
)

PUBLIC_POOL = "PUBLIC_POOL"
PUBLIC_POOL_NAME = "平台公共成本池"


# ---------------------------------------------------------------- 账单匹配
def resolve_attempt_costs(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """逐尝试确定有效金额：优先有效真实账单，账单迟到则按标准单价预提。

    被 SUPERSEDED 替代的账单不参与计价，但保留在证据中可追溯。
    """
    bills = snapshot["bills"]
    superseded_ids = {b["supersedes"] for b in bills
                      if b.get("supersedes") and b["status"] != "SUPERSEDED"}
    active_bill: Dict[str, Dict[str, Any]] = {}
    for b in bills:
        if b["status"] == "BILLED" and b["id"] not in superseded_ids:
            # 同一尝试若有多张有效账单（修订场景只保留最新一张，旧的必被替代）
            active_bill[b["attempt_id"]] = b

    result = []
    for a in snapshot["attempts"]:
        bill = active_bill.get(a["id"])
        if bill is not None:
            result.append({
                "attempt_id": a["id"],
                "task_id": a["task_id"],
                "cost_cents": bill["amount_cents"],
                "source": "BILL",
                "source_id": bill["id"],
                "accrual_cents": a["accrual_cents"],
                "late_or_revised": bill["received_version"] != snapshot["version"]
                                   or bool(bill.get("supersedes")),
            })
        else:
            result.append({
                "attempt_id": a["id"],
                "task_id": a["task_id"],
                "cost_cents": a["accrual_cents"],
                "source": "ACCRUAL",
                "source_id": None,
                "accrual_cents": a["accrual_cents"],
                "late_or_revised": False,
            })
    return result


def categorize_attempts(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """每个尝试归入成本类别。任务首个成功尝试为承诺成本，其余按性质归类。"""
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for a in snapshot["attempts"]:
        by_task.setdefault(a["task_id"], []).append(a)

    cat: Dict[str, str] = {}
    from .models import AttemptStatus, AttemptType
    for task_id, attempts in by_task.items():
        first_success = None
        for a in attempts:  # 列表顺序即执行顺序（模拟器保证）
            if a["status"] == AttemptStatus.SUCCESS and a["type"] != AttemptType.SPEC:
                first_success = a["id"]
                break
        for a in attempts:
            if a["id"] == first_success:
                cat[a["id"]] = CostCategory.COMMITTED
            elif a["type"] == AttemptType.SPEC:
                cat[a["id"]] = CostCategory.SPEC_WASTE
            elif a["type"] == AttemptType.RECOMPUTE:
                cat[a["id"]] = CostCategory.RECOMPUTE_WASTE
            else:
                cat[a["id"]] = CostCategory.RETRY_WASTE
    return cat


# ---------------------------------------------------------------- 需求传播
def compute_demand_weights(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Fraction]]:
    """计算每个任务的受益产品占比（精确分数，已归一化）。

    反向拓扑（终端层→接入层）。任务 t 的受益结构为两类需求的加权混合：
      1. 产品对 t 的直接终端消费；
      2. t 的各出边通量，按下游任务已算好的产品结构展开。
    权重即各项需求量，混合后归一化。因此：
      * 源头任务通过出边继承全部下游受益产品，不会出现空集；
      * 补录一条出边会真实改变源头/相关任务的产品结构（EV2 更正可复现）。
    """
    edges = snapshot["edges"]
    outgoing: Dict[str, List[Tuple[str, str, int]]] = {}
    for e in edges:
        outgoing.setdefault(e["upstream"], []).append(
            (e["upstream"], e["downstream"], e["demand"]))

    # 终端层先处理，保证处理 t 时其所有下游结构已知
    tasks = sorted(snapshot["tasks"], key=lambda t: -t["layer"])
    weight: Dict[str, Dict[str, Fraction]] = {t["id"]: {} for t in snapshot["tasks"]}

    for t in tasks:
        u = t["id"]
        raw: Dict[str, Fraction] = {}
        # 1) 直接终端消费
        for td in snapshot["terminal_demand"]:
            if td["task_id"] == u:
                p = td["product_id"]
                raw[p] = raw.get(p, Fraction(0)) + Fraction(td["demand"])
        # 2) 出边通量 × 下游产品结构
        for (_, d, edge_demand) in outgoing.get(u, []):
            for p, share in weight[d].items():  # 下游结构已归一化
                raw[p] = raw.get(p, Fraction(0)) + Fraction(edge_demand) * share
        total = sum(raw.values())
        weight[u] = {p: v / total for p, v in raw.items()} if total else {}
    return weight


def _frac_allocate(total: int, frac_weights: Dict[str, Fraction]
                   ) -> Dict[str, int]:
    """分数权重的最大余数法整数分摊，分摊和恒等于 total。"""
    pos = {k: v for k, v in frac_weights.items() if v > 0}
    s = sum(pos.values())
    int_weights = {k: int(v * 10_000_000 / s) for k, v in pos.items()}
    # 防止全 0
    if sum(int_weights.values()) == 0:
        int_weights[next(iter(pos))] = 1
    return largest_remainder(total, int_weights)


# ---------------------------------------------------------------- 三方案
SCHEME_RATIONALE = {
    Scheme.OCCUPANCY: {
        "name": "按资源占用分摊",
        "fairness": "用量大的产品多承担，全部成本（含重试/投机）随受益结构走，"
                    "口径单一、可预测；但平台基础设施故障造成的浪费也会摊给无辜产品。",
        "traceability": "每行可由任务需求权重反推到终端消费量，链路完全可复算。",
        "fit": "适用于稳定运行期、产品间用量差异需要被精确反映的常规结算。",
    },
    Scheme.MARGINAL: {
        "name": "边际需求分摊",
        "fairness": "共享固定成本按 Shapley 在受益产品间均分：任一受益产品退出都不会"
                    "省下该固定成本，均分体现边际贡献相等；额外用量仍按占用结构走。"
                    "小产品受益的共享任务会承担更多，需业务接受该口径。",
        "traceability": "固定成本行记录受益集合与均分人数；Shapley 推导可在报告附录复算。",
        "fit": "适用于共享上游占比高、希望避免大产品替小产品交叉补贴的谈判场景。",
    },
    Scheme.CAUSAL: {
        "name": "因果归责分摊",
        "fairness": "谁引起谁承担：产品侧变更导致的重试/重算全额归责；无主的基础设施"
                    "故障进入平台公共成本池而非摊给产品，责任边界最清晰；公共池需要"
                    "另行决定吸收或二次分摊，是本方案的治理成本。",
        "traceability": "每条归责行附带事件原因；公共池独立列示，不与产品成本混淆。",
        "fit": "适用于强调责任归属、需要对失败重试做专项治理的复盘与问责场景。",
    },
}


def _reason_occupation(task_name: str, mix_desc: str, cat_label: str) -> str:
    return f"{task_name}的{cat_label}按受益产品资源占用结构（{mix_desc}）分摊"


def _allocate_attempt(scheme: str, cost: int, category: str,
                      task: Dict[str, Any], attempt: Dict[str, Any],
                      weights: Dict[str, Fraction]) -> Tuple[Dict[str, int], str]:
    """返回 (产品/公共池 → 金额, 解释原因)。"""
    tname = task["name"]
    cat_label = CATEGORY_LABEL[category]
    pos = {p: w for p, w in weights.items() if w > 0}
    mix = "、".join(f"{p} {float(w / sum(pos.values())) * 100:.1f}%"
                    for p, w in sorted(pos.items())) if pos else "无受益产品"

    if not pos:
        return {PUBLIC_POOL: cost}, f"{tname}无受益产品，{cat_label}全额计入平台公共成本池"

    if scheme == Scheme.OCCUPANCY:
        return _frac_allocate(cost, pos), _reason_occupation(tname, mix, cat_label)

    if scheme == Scheme.MARGINAL:
        if category == CostCategory.COMMITTED:
            members = sorted(pos)
            alloc = largest_remainder(cost, {m: 1 for m in members})
            return alloc, (f"{tname}为共享固定成本，{cat_label}按边际需求 Shapley "
                           f"在 {len(members)} 个受益产品间均分（{mix}）")
        return _frac_allocate(cost, pos), _reason_occupation(tname, mix, cat_label)

    # CAUSAL
    cause = attempt.get("caused_by_product")
    if category == CostCategory.RETRY_WASTE and cause:
        return {cause: cost}, (
            f"{tname}失败重试由 {cause} 侧变更引起（{attempt['note']}），"
            f"{cat_label}全额因果归责 {cause}")
    if category == CostCategory.RETRY_WASTE and not cause:
        return {PUBLIC_POOL: cost}, (
            f"{tname}失败源于平台基础设施故障且无产品责任方（{attempt['note']}），"
            f"{cat_label}计入平台公共成本池")
    if category == CostCategory.RECOMPUTE_WASTE and cause:
        return {cause: cost}, (
            f"{tname}并行重算由 {cause} 主动发起（{attempt['note']}），"
            f"{cat_label}全额归责发起方")
    if category == CostCategory.SPEC_WASTE:
        return _frac_allocate(cost, pos), (
            f"{tname}投机备份为全体受益产品提供长尾容错，{cat_label}按占用结构（{mix}）分摊")
    return _frac_allocate(cost, pos), _reason_occupation(tname, mix, cat_label)


def compute_report(snapshot: Dict[str, Any], scheme: str) -> Dict[str, Any]:
    """基于一份固定证据快照与指定方案，计算完整成本报告。"""
    assert scheme in ALL_SCHEMES
    products = snapshot["products"]
    product_ids = [p["id"] for p in products]
    task_by_id = {t["id"]: t for t in snapshot["tasks"]}
    attempt_by_id = {a["id"]: a for a in snapshot["attempts"]}

    costs = resolve_attempt_costs(snapshot)
    categories = categorize_attempts(snapshot)
    weights = compute_demand_weights(snapshot)

    product_totals = {p: 0 for p in product_ids}
    product_category = {p: {c: 0 for c in CATEGORY_LABEL} for p in product_ids}
    pool_total = 0
    pool_category = {c: 0 for c in CATEGORY_LABEL}
    lines: List[Dict[str, Any]] = []
    task_rollup: Dict[str, Dict[str, Any]] = {}

    for c in costs:
        aid = c["attempt_id"]
        tid = c["task_id"]
        attempt = attempt_by_id[aid]
        category = categories[aid]
        alloc, reason = _allocate_attempt(
            scheme, c["cost_cents"], category, task_by_id[tid], attempt,
            weights.get(tid, {}))
        for target, amount in alloc.items():
            if target == PUBLIC_POOL:
                pool_total += amount
                pool_category[category] += amount
            else:
                product_totals[target] += amount
                product_category[target][category] += amount

        lines.append({
            "attempt_id": aid,
            "task_id": tid,
            "task_name": task_by_id[tid]["name"],
            "attempt_type": attempt["type"],
            "status": attempt["status"],
            "category": category,
            "category_label": CATEGORY_LABEL[category],
            "cost_cents": c["cost_cents"],
            "source": c["source"],
            "source_id": c["source_id"],
            "accrual_cents": c["accrual_cents"],
            "note": attempt["note"],
            "caused_by_product": attempt.get("caused_by_product"),
            "allocation": alloc,
            "reason": reason,
        })

        tr = task_rollup.setdefault(tid, {
            "task_id": tid, "task_name": task_by_id[tid]["name"],
            "total_cents": 0,
            "categories": {k: 0 for k in CATEGORY_LABEL},
            "beneficiaries": sorted(p for p, w in weights.get(tid, {}).items()
                                    if w > 0),
        })
        tr["total_cents"] += c["cost_cents"]
        tr["categories"][category] += c["cost_cents"]

    total_cost = sum(c["cost_cents"] for c in costs)
    billed_total = sum(c["cost_cents"] for c in costs if c["source"] == "BILL")
    accrual_total = sum(c["cost_cents"] for c in costs if c["source"] == "ACCRUAL")
    allocated_sum = sum(product_totals.values()) + pool_total

    # 守恒断言：分摊总额必须与有效账单+预提总额分毫不差
    assert allocated_sum == total_cost, (
        f"守恒失败: 分摊 {allocated_sum} != 总成本 {total_cost}")

    conservation = {
        "total_cost_cents": total_cost,
        "billed_cents": billed_total,
        "accrued_cents": accrual_total,
        "allocated_products_cents": sum(product_totals.values()),
        "public_pool_cents": pool_total,
        "allocated_total_cents": allocated_sum,
        "balanced": allocated_sum == total_cost,
        "diff_cents": allocated_sum - total_cost,
    }

    return {
        "scheme": scheme,
        "scheme_meta": SCHEME_RATIONALE[scheme],
        "evidence_version": snapshot["version"],
        "products": [
            {"product_id": p["id"], "name": p["name"], "owner": p["owner"],
             "total_cents": product_totals[p["id"]],
             "by_category": product_category[p["id"]]}
            for p in products
        ],
        "public_pool": {
            "name": PUBLIC_POOL_NAME,
            "total_cents": pool_total,
            "by_category": pool_category,
        },
        "tasks": sorted(task_rollup.values(), key=lambda x: x["task_id"]),
        "lines": lines,
        "conservation": conservation,
    }


def compare_schemes(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """同一证据版下三方案并排比较。"""
    reports = {s: compute_report(snapshot, s) for s in ALL_SCHEMES}
    rows = []
    for p in snapshot["products"]:
        row = {"product_id": p["id"], "name": p["name"], "by_scheme": {}}
        for s in ALL_SCHEMES:
            row["by_scheme"][s] = reports[s]["products"][
                [x["product_id"] for x in reports[s]["products"]].index(p["id"])
            ]["total_cents"]
        rows.append(row)
    pool_row = {"product_id": PUBLIC_POOL, "name": PUBLIC_POOL_NAME,
                "by_scheme": {s: reports[s]["public_pool"]["total_cents"]
                              for s in ALL_SCHEMES}}
    return {"rows": rows + [pool_row], "reports": reports}
