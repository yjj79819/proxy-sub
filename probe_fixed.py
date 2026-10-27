# -*- coding: utf-8 -*-
"""探测固定订阅地址：国内直连可达性 + 内容格式 + 节点数 + 新鲜度。
不做任何筛选、不生成配置 —— 只为回答"哪几条地址值得直接填进 FlClash"。
"""
import base64
import re
import time
import urllib.request

RAW = {
    "anaer/Sub": "https://raw.githubusercontent.com/anaer/Sub/main/clash.yaml",
    "free18/v2ray": "https://raw.githubusercontent.com/free18/v2ray/main/c.yaml",
    "mahdibland/ShadowsocksAggregator": "https://raw.githubusercontent.com/mahdibland/ShadowsocksAggregator/master/Eternity.yml",
    "peasoft/NoMoreWalls": "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.yml",
    "ripaojiedian/freenode": "https://raw.githubusercontent.com/ripaojiedian/freenode/main/clash",
    "ermaozi01/free_clash_vpn": "https://raw.githubusercontent.com/ermaozi01/free_clash_vpn/main/subscribe/clash.yml",
}

PROXIES = [
    ("raw", ""),
    ("gh-proxy", "https://gh-proxy.com/"),
    ("ghfast", "https://ghfast.top/"),
]

OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, timeout=25):
    t = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": "clash.meta",
        "Cache-Control": "no-cache",
    })
    r = OP.open(req, timeout=timeout)
    data = r.read()
    return data, time.time() - t


def analyze(data):
    """返回 (格式, 节点数, 更新时间)"""
    txt = data.decode("utf-8", "replace")
    head = txt.strip()[:400]
    upd = ""
    m = re.search(r"(?:updated|更新时间|Update)[^\n]{0,40}", head, re.I)
    if m:
        upd = m.group(0).strip()[:44]

    if "proxies:" in txt and ("proxy-groups:" in txt or "rules:" in txt):
        n = len(re.findall(r"^\s{0,4}-\s*\{?\s*name:", txt, re.M))
        if n < 5:
            n = len(re.findall(r"^\s*-\s+name:", txt, re.M))
        return "clash-yaml", n, upd

    # 可能是 base64
    compact = re.sub(r"\s+", "", txt)
    if compact and re.fullmatch(r"[A-Za-z0-9+/=_\-]+", compact[:200] or "x"):
        try:
            dec = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
            n = len([l for l in dec.splitlines() if "://" in l])
            if n:
                return "base64-v2ray", n, upd
        except Exception:
            pass
    return "unknown", 0, upd


def main():
    rows = []
    for name, raw in RAW.items():
        line = {"源": name, "raw": None}
        for label, prefix in PROXIES:
            url = prefix + raw
            try:
                data, dt = get(url)
                if len(data) < 300:
                    line[label] = ("小", len(data), dt, "", 0, "")
                    continue
                fmt, n, upd = analyze(data)
                line[label] = ("OK", len(data), dt, fmt, n, upd)
                line["_body_" + label] = data
            except Exception as e:
                line[label] = ("FAIL", 0, 0, type(e).__name__, 0, "")
        rows.append(line)

    print("=" * 108)
    print(f"{'源':34s} {'入口':9s} {'状态':6s} {'大小':>8s} {'耗时':>7s} {'格式':13s} {'节点':>5s}  更新")
    print("=" * 108)
    for r in rows:
        for label, _ in PROXIES:
            st, size, dt, fmt, n, upd = r[label]
            print(f"{r['源'][:33]:34s} {label:9s} {st:6s} {size:8d} {dt:6.2f}s {fmt:13s} {n:5d}  {upd}")
        print("-" * 108)

    best = []
    for r in rows:
        cand = [(r[l][2], l) for l, _ in PROXIES if r[l][0] == "OK"]
        if cand:
            dt, l = min(cand)
            best.append((r["源"], l, dt, r[l][4], r[l][3]))
    print("\n【国内直连最快入口】")
    for name, l, dt, n, fmt in sorted(best, key=lambda x: x[2]):
        print(f"  {name:34s} via {l:9s} {dt:5.2f}s  {n:4d} 节点  {fmt}")
    return best


if __name__ == "__main__":
    main()
