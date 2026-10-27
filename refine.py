#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refine.py —— 中国视角精筛（Actions 测不了的那一步）

为什么必须有这一步：
  GitHub Actions 的 runner 在**美国 Azure 机房**，它测的是"节点自身是否工作"。
  同一份配置，两端读数差 16 倍（2026-10-27 实测）：
      Actions 美国视角  100/2885 可用，延迟 34~60ms
      本机中国视角       6/100   可用，延迟 239~1254ms
  所以"能不能出海"这个判断，只有在中国大陆的机器上做才算数。

分工：
  Actions（境外）  → 抓取 + 去重 + 粗筛，产出 dist/config.yaml（候选池）
  本脚本（中国）   → 对候选池逐个真测速，只留国内真能连的，产出 dist/config-cn.yaml
  FlClash 订阅     → dist/config-cn.yaml

用法：
  python refine.py            # 只生成不推送
  python refine.py --push     # 生成后 git commit + push
"""
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import yaml

import update as U   # 复用抓取/测速/生成逻辑，避免两套实现漂移

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
OUT = os.path.join(DIST, "config-cn.yaml")
OUT_STATS = os.path.join(DIST, "stats-cn.json")

REMOTE = ("https://raw.githubusercontent.com/yjj79819/proxy-sub/"
          "main/dist/config.yaml")

# 中国视角比美国严，超时放宽一点；但延迟上限卡死，避免选出 3s+ 的废节点
CN_DELAY_LIMIT = int(os.environ.get("CN_DELAY_LIMIT", "2500"))
CN_MAX_KEEP = int(os.environ.get("CN_MAX_KEEP", "60"))


def http_get(url, timeout=45):
    """优先走本机代理（FlClash 7890），拿不到就直连。"""
    for proxy in (os.environ.get("SUBHUB_PROXY", "http://127.0.0.1:7890"), None):
        try:
            if proxy:
                h = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            else:
                h = urllib.request.ProxyHandler({})
            op = urllib.request.build_opener(h)
            with op.open(urllib.request.Request(url, headers={
                    "User-Agent": U.UA, "Cache-Control": "no-cache"}), timeout=timeout) as r:
                return r.read()
        except Exception:
            continue
    return None


def load_candidates():
    """取候选池：先远程（Actions 刚生成），失败退回本地 dist/config.yaml。"""
    for src, getter in (("远程", lambda: http_get(REMOTE)),
                        ("本地", lambda: io.open(os.path.join(DIST, "config.yaml"), "rb").read()
                         if os.path.exists(os.path.join(DIST, "config.yaml")) else None)):
        raw = getter()
        if not raw:
            continue
        try:
            o = yaml.safe_load(raw.decode("utf-8", "replace"))
        except Exception:
            continue
        ps = [p for p in (o.get("proxies") or []) if isinstance(p, dict)]
        pub = [p for p in ps if str(p.get("name", "")).startswith("PUB")]
        if pub:
            U.log(f"候选池来源: {src}，公共节点 {len(pub)} 个")
            return pub
    U.log("!! 拿不到候选池")
    return []


def strip_name(p):
    q = {k: v for k, v in p.items() if k not in ("name", "_src", "_name")}
    return q


def main():
    do_push = "--push" in sys.argv
    t0 = time.time()

    pub = load_candidates()
    if not pub:
        print("SKIP")
        return 1

    cand = []
    for p in pub:
        q = strip_name(p)
        if q.get("server") and q.get("port"):
            cand.append(q)
    U.log(f"去名后候选: {len(cand)}")

    # 中国视角：先 TCP 预筛（砍掉大部分，快），再真测速
    alive_tcp = U.tcp_filter(cand)
    U.log(f"TCP 可连（中国）: {len(alive_tcp)}/{len(cand)} "
          f"({len(alive_tcp)/max(1,len(cand))*100:.1f}%)")

    mihomo = U.ensure_mihomo()
    delays = U.speedtest(alive_tcp, mihomo)
    U.log(f"中国视角真可用: {len(delays)}/{len(alive_tcp)} "
          f"({len(delays)/max(1,len(alive_tcp))*100:.1f}%)")

    by_name = {f"P{i:04d}": p for i, p in enumerate(alive_tcp)}
    scored = []
    for n, d in delays.items():
        if d <= CN_DELAY_LIMIT:
            q = dict(by_name[n])
            q["_name"] = n
            scored.append((d, q))
    scored.sort(key=lambda x: x[0])
    keep = scored[:CN_MAX_KEEP]
    U.log(f"保留（≤{CN_DELAY_LIMIT}ms）: {len(keep)} 个")

    stats = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "view": "CN (本机中国视角)",
        "counts": {"candidates": len(cand), "tcp_ok": len(alive_tcp),
                   "alive": len(delays), "kept": len(keep)},
        "top": [{"delay": d, "type": p["type"], "server": p["server"]}
                for d, p in keep[:15]],
        "delay": {p["_name"]: d for d, p in keep},
    }

    # 公共节点一个都没通时，不写：保留上一版，避免把配置清空
    if not keep:
        U.log("!! 中国视角零可用，保留上一版 config-cn.yaml")
        print("SKIP")
        return 0

    cfg = U.build_config([p for d, p in keep], stats)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("# 由 refine.py 生成（中国视角精筛），请勿手改\n")
        f.write(f"# 更新时间: {stats['updated']}\n")
        f.write(f"# 候选 {len(cand)} -> TCP 通 {len(alive_tcp)} -> 真可用 {len(delays)} -> 保留 {len(keep)}\n")
        f.write("# 与 dist/config.yaml 的区别：那是美国 runner 测的，本文件是国内实测能连的\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    with io.open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    zi = [p["name"] for p in cfg["proxies"] if p["name"].startswith("🏠")]
    U.log(f"完成 {time.time()-t0:.0f}s -> {OUT} "
          f"({len(cfg['proxies'])} 节点 = 自建 {len(zi)} + 公共 {len(keep)})")

    if do_push:
        env = dict(os.environ)
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                  "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
        env["GH_CONFIG_DIR"] = "C:/Users/yjj/.workbuddy/ghconfig"
        cmds = [
            ["git", "fetch", "-q", "origin"],
            ["git", "reset", "--hard", "origin/main"],
            ["git", "add", "dist/config-cn.yaml", "dist/stats-cn.json"],
        ]
        for c in cmds:
            subprocess.run(c, cwd=HERE, env=env, capture_output=True)
        n = stats["counts"]
        msg = (f"chore: 中国视角精筛 {stats['updated']} | "
               f"可用 {n['kept']}/{n['candidates']}")
        r = subprocess.run(["git", "-c", "http.proxy=", "-c", "https.proxy=",
                            "commit", "-q", "-m", msg],
                           cwd=HERE, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            U.log("无变更，跳过推送")
        else:
            r2 = subprocess.run(["git", "-c", "http.proxy=", "-c", "https.proxy=",
                                 "push", "-q", "origin", "main"],
                                cwd=HERE, env=env, capture_output=True, text=True)
            U.log("推送成功" if r2.returncode == 0 else f"推送失败: {r2.stderr[:200]}")
    print("WROTE_CN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
