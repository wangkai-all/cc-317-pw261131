# -*- coding: utf-8 -*-
"""命令行入口：

  python3 run.py seed [--data-dir data]      生成虚构证据与报告（幂等）
  python3 run.py verify [--data-dir data]    完整性与守恒校验
  python3 run.py list [--data-dir data]      列出证据与报告
  python3 run.py serve [--data-dir data] [--port 8000]  启动网页服务
"""
from __future__ import annotations

import argparse
import sys

from costapp.models import money_yuan
from costapp.reports import evidence_overview, seed_demo
from costapp.server import serve
from costapp.store import Store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="任务成本分摊与修订追溯演示")
    p.add_argument("command", choices=["seed", "verify", "list", "serve"])
    p.add_argument("--data-dir", default="data")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args(argv)

    store = Store(args.data_dir)

    if args.command == "seed":
        result = seed_demo(store)
        print(f"已播种 {len(result['reports'])} 份报告（幂等）")
        return 0

    if args.command == "verify":
        v = store.verify_integrity()
        if v["ok"]:
            print(f"完整性校验通过：证据 {v['evidence_count']} 版，"
                  f"台账 {v['ledger_entries']} 条")
        else:
            print("完整性校验发现问题：")
            for prob in v["problems"]:
                print(" -", prob)
            return 1
        # 额外打印各报告守恒结果
        for r in store.list_reports():
            c = r["body"]["conservation"]
            flag = "OK" if c["balanced"] else "FAIL"
            print(f"  [{flag}] {r['report_id']} 总额 {money_yuan(c['total_cost_cents'])}"
                  f" = 产品 {money_yuan(c['allocated_products_cents'])}"
                  f" + 公共池 {money_yuan(c['public_pool_cents'])}")
        return 0

    if args.command == "list":
        for ev in evidence_overview(store):
            print(f"\n== {ev['version']} {ev['description']}")
            c = ev["conservation"]
            print(f"   总额 {money_yuan(c['total_cost_cents'])}（账单 "
                  f"{money_yuan(c['billed_cents'])} / 预提 {money_yuan(c['accrued_cents'])}）"
                  f"  证据哈希 {ev['evidence_hash'][:12]}")
            for r in ev["reports"]:
                print(f"   - {r['report_id']} [{r['status']}] hash={r['short_hash']}")
        return 0

    if args.command == "serve":
        serve(store, host=args.host, port=args.port)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
