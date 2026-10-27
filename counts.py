#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""counts.py —— 给 CI 的提交信息拼一行可用节点数

    US 100 / CN 4

只读 dist/stats.json 与 dist/stats-cn.json，任何异常都退化成 "-"，绝不阻断提交。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def kept(fn):
    p = os.path.join(HERE, "dist", fn)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)["counts"]["kept"]
    except Exception:
        return "-"


def main():
    print(f"US {kept('stats.json')} / CN {kept('stats-cn.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
