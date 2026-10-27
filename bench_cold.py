# -*- coding: utf-8 -*-
"""冷启动测速计时：清空 provider 缓存与测速缓存，从零开始，
统计 5 个小源多久测完 100%，以及日常组多久选出第一个可用节点。
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "_fx2")
CTL = "http://127.0.0.1:29190"
SMALL = ["peasoft", "mahdibland", "free18", "ripaojiedian", "ermaozi"]


def prep():
    shutil.rmtree(FX, ignore_errors=True)
    os.makedirs(FX)
    src = open(os.path.join(HERE, "fixed.yaml"), encoding="utf-8").read()
    src = src.replace("mixed-port: 7890", "mixed-port: 17891")
    open(os.path.join(FX, "config.yaml"), "w", encoding="utf-8").write(src)
    for f in ("GEOSITE.dat", "GEOIP.metadb"):
        p = os.path.join(r"C:\Users\yjj\AppData\Roaming\com.follow\clash", f)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(FX, f))


def main():
    prep()
    exe = os.path.join(HERE, "bin", "mihomo.exe")
    log = open(os.path.join(FX, "run.log"), "wb")
    proc = subprocess.Popen([exe, "-d", FX, "-ext-ctl", "127.0.0.1:29190"],
                            stdout=log, stderr=subprocess.STDOUT)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    try:
        for _ in range(120):
            try:
                op.open(CTL + "/version", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        print(f"内核就绪 +{time.time()-t0:.1f}s")

        first_node = None
        done_at = None
        for _ in range(240):          # 最长 4 分钟
            time.sleep(2)
            try:
                d = json.load(op.open(CTL + "/providers/proxies", timeout=10))
            except Exception:
                continue
            el = time.time() - t0
            stat = {}
            for k in SMALL:
                ps = (d["providers"].get(k) or {}).get("proxies") or []
                if not ps:
                    stat[k] = -1
                    continue
                done = sum(1 for p in ps if p.get("history"))
                stat[k] = done / len(ps)
            if first_node is None:
                try:
                    pr = json.load(op.open(CTL + "/proxies", timeout=8))
                    gw = (pr["proxies"].get("🚀 境外代理") or {}).get("now")
                    if gw and gw not in ("COMPATIBLE", "DIRECT", "PASS", "REJECT"):
                        first_node = el
                        print(f"日常组首次选出节点 +{el:.1f}s -> {gw[:30]}")
                except Exception:
                    pass
            if all(v >= 1.0 for v in stat.values()):
                done_at = el
                break
            if int(el) % 10 < 2:
                print(f"  +{el:5.0f}s  " + "  ".join(
                    f"{k}={v*100:3.0f}%" if v >= 0 else f"{k}=下载中" for k, v in stat.items()))
        print()
        if done_at:
            print(f"✅ 5 个小源全部测完：{done_at:.1f}s")
        else:
            print(f"⚠️ 4 分钟内未测完，最后状态：" + str(
                {k: f"{v*100:.0f}%" for k, v in stat.items()}))
        # 汇总
        d = json.load(op.open(CTL + "/providers/proxies", timeout=10))
        total = ok = 0
        for k in SMALL:
            ps = (d["providers"].get(k) or {}).get("proxies") or []
            total += len(ps)
            ok += sum(1 for p in ps if any(h.get("delay", 0) > 0 for h in (p.get("history") or [])))
        print(f"5 源合计 {total} 节点，测出可用 {ok} 个（{ok/max(1,total)*100:.1f}%）")
        print("\n按源明细：")
        for k in SMALL:
            ps = (d["providers"].get(k) or {}).get("proxies") or []
            good = [p for p in ps if any(h.get("delay", 0) > 0 for h in (p.get("history") or []))]
            print(f"  {k:14s} {len(good):3d}/{len(ps):4d} 可用")
            for p in good[:4]:
                dl=[h["delay"] for h in p.get("history") or [] if h.get("delay")]
                print(f"      {p['name'][:40]:42s} {min(dl)}ms")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        log.close()


if __name__ == "__main__":
    main()
