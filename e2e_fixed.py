# -*- coding: utf-8 -*-
"""走本地 mihomo(17890) 实测各分组能否真出海，并打印出口 IP 归属。
切换分组用 external-controller REST API。
"""
import json
import time
import urllib.parse
import urllib.request

CTL = "http://127.0.0.1:29090"
MIXED = "http://127.0.0.1:17890"
DIRECT_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXY_OP = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": MIXED, "https": MIXED})
)
UA = {"User-Agent": "Mozilla/5.0"}


def switch(group, name):
    data = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{CTL}/proxies/{urllib.parse.quote(group)}",
        data=data, method="PUT",
        headers={"Content-Type": "application/json"},
    )
    DIRECT_OP.open(req, timeout=8).read()


def now_of(group):
    d = json.load(DIRECT_OP.open(f"{CTL}/proxies", timeout=8))
    return (d["proxies"].get(group) or {}).get("now")


def egress(timeout=25):
    t = time.time()
    try:
        r = PROXY_OP.open(
            urllib.request.Request("https://api.ipify.org?format=json", headers=UA),
            timeout=timeout,
        )
        ip = json.load(r)["ip"]
        dt = time.time() - t
    except Exception as e:
        return None, time.time() - t, type(e).__name__
    # 查归属（这个请求也走代理，验证的是同一条链路）
    loc = ""
    try:
        q = PROXY_OP.open(
            urllib.request.Request(f"http://ip-api.com/json/{ip}?fields=country,regionName,city,isp,as",
                                   headers=UA), timeout=12)
        j = json.load(q)
        loc = f'{j.get("country","?")}/{j.get("regionName","?")}/{j.get("isp","?")[:26]}'
    except Exception:
        loc = "(归属查询失败)"
    return ip, dt, loc


def main():
    print(f"{'分组':14s} {'选中节点':34s} {'出口 IP':16s} {'耗时':>7s}  归属")
    print("-" * 108)
    for grp in ("🚀 境外代理", "♻️ anaer", "♻️ peasoft", "♻️ mahdibland",
                "♻️ free18", "♻️ ripaojiedian", "♻️ ermaozi"):
        # 先看组自己选中的节点
        nm = now_of(grp)
        if grp != "🚀 境外代理":
            switch("🎯 手动选择", grp)
            time.sleep(1.0)
            nm = now_of(grp)
        # 若该组选中的节点不通，让 URLTest 原地重测一次
        ip, dt, loc = egress()
        if not ip:
            try:
                DIRECT_OP.open(f"{CTL}/group/{urllib.parse.quote(grp)}/delay?url=http://cp.cloudflare.com/generate_204&timeout=5000",
                               timeout=60).read()
                time.sleep(1.0)
                nm = now_of(grp)
                ip, dt, loc = egress()
            except Exception:
                pass
        print(f"{grp:14s} {str(nm)[:33]:34s} {str(ip)[:15]:16s} {dt:6.2f}s  {loc}")
    # 收尾：切回默认
    switch("🎯 手动选择", "🚀 境外代理")


if __name__ == "__main__":
    main()
