# -*- coding: utf-8 -*-
"""核心引擎测试：守恒、三方案、账单生命周期与需求传播。"""
import unittest

from costapp.allocator import (
    PUBLIC_POOL,
    categorize_attempts,
    compute_demand_weights,
    compute_report,
    resolve_attempt_costs,
)
from costapp.models import CostCategory, Scheme
from costapp.simulator import build_all
from fractions import Fraction


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snaps = {s["version"]: s for s in build_all()}

    def test_01_accrual_keeps_unfinished_usage(self):
        """EV1 迟到账单不能抹掉用量：无账单尝试按预提全额保留。"""
        costs = {c["attempt_id"]: c for c
                 in resolve_attempt_costs(self.snaps["EV1"])}
        agg = costs["a_aggregate_1"]
        self.assertEqual(agg["source"], "ACCRUAL")
        self.assertEqual(agg["cost_cents"], 217)  # 70*3 + 140/20
        # EV2 迟到账单替代预提
        costs2 = {c["attempt_id"]: c for c
                  in resolve_attempt_costs(self.snaps["EV2"])}
        self.assertEqual(costs2["a_aggregate_1"]["source"], "BILL")
        self.assertEqual(costs2["a_aggregate_1"]["cost_cents"], 235)

    def test_02_superseded_bill_not_counted_twice(self):
        """修订账单：旧账单被替代，只计新账单一次，不重复计价。"""
        costs = {c["attempt_id"]: c for c
                 in resolve_attempt_costs(self.snaps["EV3"])}
        risk = costs["a_feature_risk_1"]
        self.assertEqual(risk["source"], "BILL")
        self.assertEqual(risk["cost_cents"], 660)  # 不是 620，也不是 620+660

    def test_03_categories(self):
        cats = categorize_attempts(self.snaps["EV3"])
        self.assertEqual(cats["a_join_wide_1"], CostCategory.RETRY_WASTE)
        self.assertEqual(cats["a_join_wide_2"], CostCategory.COMMITTED)
        self.assertEqual(cats["a_clean_order_2"], CostCategory.SPEC_WASTE)
        self.assertEqual(cats["a_report_bi_2"], CostCategory.RECOMPUTE_WASTE)
        self.assertEqual(cats["a_report_bi_1"], CostCategory.COMMITTED)

    def test_04_conservation_all_versions_schemes(self):
        """每个版本 × 每个方案：分摊总额严格等于有效成本总额。"""
        for ver, snap in self.snaps.items():
            for scheme in (Scheme.OCCUPANCY, Scheme.MARGINAL, Scheme.CAUSAL):
                rep = compute_report(snap, scheme)
                c = rep["conservation"]
                self.assertTrue(c["balanced"], f"{ver}/{scheme} 不守恒")
                self.assertEqual(c["diff_cents"], 0)
                # 逐尝试行金额之和 = 总额（无重复、无遗漏）
                self.assertEqual(sum(l["cost_cents"] for l in rep["lines"]),
                                 c["total_cost_cents"])
                # 每行分摊之和 = 行成本（共享成本不重复全额）
                for line in rep["lines"]:
                    self.assertEqual(sum(line["allocation"].values()),
                                     line["cost_cents"], line["attempt_id"])

    def test_05_no_double_full_charge(self):
        """共享任务的每个产品分摊额必须小于其成本（多受益方时）。"""
        rep = compute_report(self.snaps["EV1"], Scheme.OCCUPANCY)
        join_line = next(l for l in rep["lines"]
                         if l["task_id"] == "t_join_wide"
                         and l["category"] == CostCategory.COMMITTED)
        for amount in join_line["allocation"].values():
            self.assertLess(amount, join_line["cost_cents"])

    def test_06_causal_routing(self):
        """因果方案：产品责任归责、基础设施故障入公共池、重算归发起方。"""
        rep = compute_report(self.snaps["EV3"], Scheme.CAUSAL)
        lines = {l["attempt_id"]: l for l in rep["lines"]}
        # 风控引起的清洗失败 → 全额 P_RISK
        self.assertEqual(lines["a_clean_log_1"]["allocation"], {"P_RISK": 124})
        # 推荐侧 Schema 失败 → 全额 P_REC
        self.assertEqual(lines["a_feature_rec_1"]["allocation"], {"P_REC": 93})
        # 基础设施故障（无责任产品）→ 公共池
        self.assertEqual(lines["a_join_wide_1"]["allocation"],
                         {PUBLIC_POOL: 372})
        # 投机备份为容错支出，按受益结构分摊，不进公共池
        self.assertNotIn(PUBLIC_POOL, lines["a_feature_risk_2"]["allocation"])
        # 并行重算 → 发起方 P_BI
        self.assertEqual(lines["a_report_bi_2"]["allocation"], {"P_BI": 155})
        # 公共池非负且计入守恒
        self.assertGreater(rep["public_pool"]["total_cents"], 0)

    def test_07_marginal_equal_share_fixed(self):
        """边际方案：多受益方的共享固定成本在受益产品间均分。"""
        rep = compute_report(self.snaps["EV1"], Scheme.MARGINAL)
        line = next(l for l in rep["lines"]
                    if l["attempt_id"] == "a_ingest_log_1")
        vals = list(line["allocation"].values())
        self.assertEqual(len(vals), 3)
        self.assertEqual(max(vals) - min(vals) <= 1, True)  # 最大余数差至多 1 分
        self.assertEqual(sum(vals), line["cost_cents"])

    def test_08_graph_correction_changes_weights(self):
        """EV2 补录 feature_rec→report_bi 边：受益占比沿图重排，可被量化复现。"""
        w1 = compute_demand_weights(self.snaps["EV1"])
        w2 = compute_demand_weights(self.snaps["EV2"])
        # 汇点 report_bi 自身只承接 P_BI 的终端消费，两版不变
        self.assertEqual(set(w1["t_report_bi"]), {"P_BI"})
        # feature_rec 早已经 aggregate→report 路径服务 P_BI，补边后其 P_BI 占比上升
        self.assertIn("P_BI", w1["t_feature_rec"])
        self.assertGreater(w2["t_feature_rec"]["P_BI"],
                           w1["t_feature_rec"]["P_BI"])
        # 权重恒归一化
        for w in (w1, w2):
            for tid, mix in w.items():
                if mix:
                    self.assertAlmostEqual(float(sum(mix.values())), 1.0)
        # 至少一个任务的产品结构确实发生变化（更正有实际影响）
        changed = [tid for tid in w1 if w1[tid] != w2[tid]]
        self.assertTrue(changed)
        # 源头任务在两版都继承全部三个受益产品
        self.assertEqual(set(w1["t_ingest_log"]), {"P_RISK", "P_REC", "P_BI"})

    def test_09_version_totals(self):
        """版本总额：迟到账单、修订、重算均只产生应有差额。"""
        def total(v):
            return compute_report(self.snaps[v], Scheme.OCCUPANCY
                                  )["conservation"]["total_cost_cents"]
        t1, t2, t3 = total("EV1"), total("EV2"), total("EV3")
        self.assertEqual(t2 - t1, 235 - 217)          # 迟到账单差额 18 分
        self.assertEqual(t3 - t2, (660 - 620) + 155)  # 修订 +40，重算 +155

    def test_10_deterministic(self):
        """同输入同输出：报告可复算。"""
        r1 = compute_report(self.snaps["EV2"], Scheme.CAUSAL)
        r2 = compute_report(build_all()[1], Scheme.CAUSAL)
        self.assertEqual(r1["products"], r2["products"])
        self.assertEqual(r1["lines"], r2["lines"])


if __name__ == "__main__":
    unittest.main()
