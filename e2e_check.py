#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证：拿合并配置起 mihomo，真发出海流量，看出口 IP / 走的是哪个节点。

只改端口（避开 FlClash 占的 7890/1053），其余用合并配置原样。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "dist", "config-all.yaml")
TMP = os.path.join(HERE, "_e2e")
MIHOMO = os.path.join(HERE, "bin", "mihomo.exe")
MIXED = 17890
CTL = 19090
DNS = 15353

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, method="GET", body=None):
    url = f"http://127.0.0.1:{CTL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with OP.open(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def via_proxy(url, timeout=30):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({
        "http": f"http://127.0.0.1:{MIXED}", "https": f"http://127.0.0.1:{MIXED}"}))
    t = time.time()
    with op.open(urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace").strip(), round(time.time() - t, 2)


def main():
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)
    cfg = yaml.safe_load(io.open(SRC, encoding="utf-8"))
    cfg["mixed-port"] = MIXED
    cfg["external-controller"] = f"127.0.0.1:{CTL}"
    cfg["dns"]["listen"] = f"127.0.0.1:{DNS}"
    cfg["log-level"] = "warning"
    yaml.safe_dump(cfg, io.open(os.path.join(TMP, "config.yaml"), "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)
    for f in ("GEOSITE.dat", "GEOIP.metadb"):
        src = os.path.join(os.path.dirname(os.path.abspath(r"C:\Users\yjj\AppData\Roaming\com.follow\clash\x")), f)
        src = rf"C:\Users\yjj\AppData\Roaming\com.follow\clash\{f}"
        if os.path.exists(src):
            shutil.copy(src, TMP)

    p = subprocess.Popen([MIHOMO, "-d", TMP], stdout=subprocess.DEVNULL,
                         stderr=subprocess.STDOUT)
    try:
        ok = False
        for _ in range(40):
            try:
                api("/version")
                ok = True
                break
            except Exception:
                time.sleep(0.4)
        if not ok:
            print("!! mihomo 没起来")
            return 1
        ver = api("/version").get("version")
        print(f"mihomo {ver} 已启动  mixed={MIXED}")

        # 1) 分组当前选中
        for g in ("🚀 境外代理", "✈️ 机场", "🏠 自建", "♻️ 公共自动"):
            try:
                d = api("/proxies/" + urllib.parse.quote(g))
                print(f"  [{g}] now={d.get('now')} type={d.get('type')} n={len(d.get('all') or [])}")
            except Exception as e:
                print(f"  [{g}] 读不到: {e}")

        # 2) 真发出海请求（走规则 -> 🚀 境外代理）
        print("\n-- 真流量 --")
        for url in ("https://www.cloudflare.com/cdn-cgi/trace",
                    "https://api.ipify.org?format=json"):
            try:
                body, dt = via_proxy(url)
                if "cdn-cgi/trace" in url:
                    kv = dict(l.split("=", 1) for l in body.splitlines() if "=" in l)
                    print(f"  {dt:6.2f}s  ip={kv.get('ip')} loc={kv.get('loc')} "
                          f"colo={kv.get('colo')} warp={kv.get('warp')}")
                else:
                    print(f"  {dt:6.2f}s  {body[:60]}")
            except Exception as e:
                print(f"  FAIL {type(e).__name__}: {e}")

        # 3) 单独测机场组内最快几个，确认付费节点确实能出海
        print("\n-- 机场组内前 5 个节点实测 --")
        d = api("/proxies/" + urllib.parse.quote("✈️ 机场"))
        cand = (d.get("all") or [])[:5]
        for nm in cand:
            q = urllib.parse.quote(nm, safe="")
            try:
                r = api(f"/proxies/{q}/delay?url=http://cp.cloudflare.com/generate_204&timeout=6000")
                print(f"  {nm[:34]:34s} {r.get('delay')}ms")
            except Exception as e:
                print(f"  {nm[:34]:34s} 超时/失败")
    finally:
        p.terminate()
        try:
            p.wait(timeout=8)
        except Exception:
            p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
