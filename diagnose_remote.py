#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性诊断：单节点 delay 失败的原始原因（不参与生产流程）"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import yaml

MIHOMO = "/opt/subhub/mihomo"
CTL, MIX = 27911, 27910


def main():
    payload = json.load(open("/opt/subhub/candidates.json", encoding="utf-8"))
    cands = payload["proxies"]
    print(f"候选 {len(cands)}")

    # 找第一个 TCP 通的
    pick = None
    for p in cands:
        try:
            with socket.create_connection((p["server"], int(p["port"])), timeout=4):
                pick = p
                break
        except Exception:
            continue
    if not pick:
        print("没有 TCP 通的节点")
        return
    print(f"选中: {pick['type']} {pick['server']}:{pick['port']} net={pick.get('network')} tls={pick.get('tls')}")
    print(f"完整配置: {json.dumps({k: v for k, v in pick.items()}, ensure_ascii=False)[:500]}")

    q = {k: v for k, v in pick.items() if k not in ("name",)}
    q["name"] = "T0"
    cfg = {
        "mixed-port": MIX, "allow-lan": False, "mode": "rule",
        "log-level": "debug", "ipv6": False,
        "external-controller": f"127.0.0.1:{CTL}",
        "dns": {"enable": True, "ipv6": False,
                "nameserver": ["223.5.5.5", "119.29.29.29"],
                "default-nameserver": ["223.5.5.5", "119.29.29.29"]},
        "proxies": [q],
        "proxy-groups": [{"name": "G", "type": "select", "proxies": ["T0"]}],
        "rules": ["MATCH,G"],
    }
    wd = tempfile.mkdtemp(prefix="diag-")
    cp = os.path.join(wd, "c.yaml")
    # 必须 with 关闭句柄再 flush：否则 Popen 起来时文件还是空的，
    # mihomo 会把空文件当合法配置（test is successful 但 proxies 为 0）
    with open(cp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    print(f"配置已写入 {cp} ({os.path.getsize(cp)} 字节)")

    logf = os.path.join(wd, "m.log")
    with open(logf, "wb") as lf:
        proc = subprocess.Popen([MIHOMO, "-f", cp, "-d", wd],
                                stdout=lf, stderr=lf)
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ok = False
        for _ in range(40):
            try:
                op.open(f"http://127.0.0.1:{CTL}/version", timeout=2)
                ok = True
                break
            except Exception:
                time.sleep(0.4)
        print(f"API 就绪: {ok}")
        if not ok:
            print(open(logf, encoding="utf-8", errors="replace").read()[-2000:])
            return

        tgt = urllib.parse.quote("http://cp.cloudflare.com/generate_204", safe="")
        # 先看 API 认不认这个节点
        try:
            with op.open(f"http://127.0.0.1:{CTL}/proxies", timeout=8) as r:
                allp = json.loads(r.read().decode()).get("proxies", {})
            print("API 里的 proxies:", list(allp.keys()))
        except Exception as e:
            print("列 proxies 失败:", type(e).__name__, e)
        for probe in ("/proxies/T0", f"/proxies/T0/delay?url={tgt}&timeout=8000"):
            try:
                with op.open(f"http://127.0.0.1:{CTL}{probe}", timeout=15) as r:
                    print(f"  {probe[:40]} -> {r.read().decode()[:200]}")
            except Exception as e:
                body = ""
                if hasattr(e, "read"):
                    try:
                        body = e.read().decode()[:200]
                    except Exception:
                        pass
                print(f"  {probe[:40]} -> {type(e).__name__} {e} body={body}")
        u = f"http://127.0.0.1:{CTL}/proxies/T0/delay?url={tgt}&timeout=10000"
        try:
            with op.open(u, timeout=20) as r:
                print("delay 结果:", r.read().decode())
        except Exception as e:
            body = ""
            if hasattr(e, "read"):
                try:
                    body = e.read().decode()
                except Exception:
                    pass
            print(f"delay 失败: {type(e).__name__} {e} body={body}")

        # 再用代理口直连试一次
        try:
            rq = urllib.request.Request("http://cp.cloudflare.com/generate_204")
            op2 = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{MIX}"}))
            with op2.open(rq, timeout=20) as r:
                print("经代理口访问:", r.status)
        except Exception as e:
            print(f"经代理口失败: {type(e).__name__} {e}")

        time.sleep(1)
        txt = open(logf, encoding="utf-8", errors="replace").read()
        print("\n=== 内核日志（尾部 2500 字） ===")
        print(txt[-2500:])
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
