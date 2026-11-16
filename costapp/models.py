# -*- coding: utf-8 -*-
"""领域常量与通用工具。

金额单位：分（整数），避免浮点误差；展示层再换算为元。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------- 定价（虚构）
# CPU: 3 分 / CPU-秒；内存: 1 分 / 20 GB-秒
CPU_RATE_CENT_PER_SEC = 3
MEM_RATE_CENT_PER_20GBS = 1


def tariff_amount(cpu_seconds: int, mem_gb_seconds: int) -> int:
    """按标准单价计算应付金额（分），整除保证整数。"""
    assert cpu_seconds % 10 == 0 and mem_gb_seconds % 20 == 0, (
        "用量取值需保证整数金额", cpu_seconds, mem_gb_seconds)
    return (cpu_seconds * CPU_RATE_CENT_PER_SEC
            + mem_gb_seconds // 20 * MEM_RATE_CENT_PER_20GBS)


# ---------------------------------------------------------------- 枚举
class AttemptType:
    INITIAL = "INITIAL"        # 初次执行
    RETRY = "RETRY"            # 失败重试
    SPEC = "SPEC"              # 投机备份执行
    RECOMPUTE = "RECOMPUTE"    # 业务发起的并行重算


class AttemptStatus:
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SPEC_KILLED = "SPEC_KILLED"


class CostCategory:
    # 任务首个成功尝试 = 承诺成本；其余尝试按性质进入三类“额外用量”
    COMMITTED = "COMMITTED"        # 承诺成本
    RETRY_WASTE = "RETRY_WASTE"    # 失败/重试额外用量
    SPEC_WASTE = "SPEC_WASTE"      # 投机执行额外用量
    RECOMPUTE_WASTE = "RECOMPUTE_WASTE"  # 并行重算额外用量


CATEGORY_LABEL = {
    CostCategory.COMMITTED: "承诺成本",
    CostCategory.RETRY_WASTE: "重试额外用量",
    CostCategory.SPEC_WASTE: "投机额外用量",
    CostCategory.RECOMPUTE_WASTE: "并行重算用量",
}


class BillStatus:
    BILLED = "BILLED"          # 已收到真实账单
    ACCRUED = "ACCRUED"        # 账单未到，按标准单价预提
    SUPERSEDED = "SUPERSEDED"  # 被后续账单/修订替代


class Scheme:
    OCCUPANCY = "OCCUPANCY"    # 按资源占用（终端需求权重）
    MARGINAL = "MARGINAL"      # 边际需求（固定成本 Shapley 均分）
    CAUSAL = "CAUSAL"          # 因果归责 + 公共池


SCHEME_LABEL = {
    Scheme.OCCUPANCY: "按资源占用分摊",
    Scheme.MARGINAL: "边际需求分摊",
    Scheme.CAUSAL: "因果归责分摊",
}

ALL_SCHEMES = [Scheme.OCCUPANCY, Scheme.MARGINAL, Scheme.CAUSAL]


# ---------------------------------------------------------------- 哈希 / JSON
def canonical_json(obj: Any) -> str:
    """规范化 JSON：键排序、无空白、确保哈希稳定。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def short(h: str, n: int = 12) -> str:
    return h[:n]


def money_yuan(cents: int) -> str:
    return f"¥{cents / 100:,.2f}"


# ---------------------------------------------------------------- 整数分摊
def largest_remainder(total: int, weights: Dict[str, int]) -> Dict[str, int]:
    """按权重整数分摊 total，最大余数法保证分毫不差、总额严格守恒。

    weights 为 {参与方: 权重}，权重必须为正整数；返回 {参与方: 分摊额}，
    分摊额之和恒等于 total。
    """
    items = [(k, w) for k, w in weights.items() if w > 0]
    if not items:
        raise ValueError("largest_remainder 需要至少一个正权重")
    wsum = sum(w for _, w in items)
    quotas: List[Tuple[str, int, float]] = []
    for k, w in items:
        exact = total * w / wsum
        quotas.append((k, int(exact), exact))
    allocated = sum(q[1] for q in quotas)
    remainder = total - allocated
    # 小数部分从大到小补 1 分
    order = sorted(range(len(quotas)),
                   key=lambda i: (quotas[i][2] - quotas[i][1]), reverse=True)
    result = {k: base for k, base, _ in quotas}
    for i in order[:remainder]:
        result[quotas[i][0]] += 1
    assert sum(result.values()) == total
    return result


def equal_split(total: int, members: List[str]) -> Dict[str, int]:
    """在成员间均分（最大余数法），成员顺序固定保证可复现。"""
    return largest_remainder(total, {m: 1 for m in sorted(members)})
