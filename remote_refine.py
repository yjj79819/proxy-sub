#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""remote_refine.py —— 在【中国大陆】机器上做的节点真测速

为什么要在服务器上跑：
    GitHub Actions 的 runner 在美国，测的是"节点自身是否工作"；
    本脚本跑在腾讯云广州（出口 China/Shanghai），测的才是"从国内能不能连"。
    2026-10-27 实测同一份候选池：美国视角 100 个可用，中国视角只剩 6 个。

用法（由 GitHub Actions 通过 SSH 调用）：
    ssh root@host 'python3 /opt/subhub/remote_refine.py' < candidates.json > result.json

输入 stdin：{"proxies": [ {name,type,server,port,...}, ... ]}
输出 stdout：{"stats": {...}, "alive": [{"idx": 3, "delay": 412}, ...]}
    idx = 输入数组下标，让调用方能把结果映射回原节点

无第三方依赖（只 python3-yaml，Ubuntu 自带）。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import yaml

MIHOMO = os.environ.get("MIHOMO_BIN", "/opt/subhub/mihomo")
TCP_TIMEOUT = float(os.environ.get("TCP_TIMEOUT", "4"))
DELAY_TIMEOUT = int(os.environ.get("DELAY_TIMEOUT", "8000"))
WORKERS = int(os.environ.get("WORKERS", "40"))
TEST_URL = "http://cp.cloudflare.com/generate_204"
CTL_PORT = int(os.environ.get("CTL_PORT", "27901"))
MIXED_PORT = int(os.environ.get("MIXED_PORT", "27900"))


def log(m):
    print(f"[remote {time.strftime('%H:%M:%S')}] {m}", file=sys.stderr, flush=True)


def tcp_ok(p):
    try:
        with socket.create_connection((p["server"], int(p["port"])), timeout=TCP_TIMEOUT):
            return True
    except Exception:
        return False


def wait_api(timeout=25):
    # 服务器上可能有 http_proxy 环境变量，访问本地 API 必须绕开
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with op.open(f"http://127.0.0.1:{CTL_PORT}/version", timeout=2):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    t0 = time.time()
    tcp_only = os.environ.get("TCP_ONLY", "").strip() in ("1", "true", "yes")
    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"bad stdin: {e}"}))
        return 1

    proxies = [p for p in (payload.get("proxies") or [])
               if isinstance(p, dict) and p.get("server") and p.get("port")]
    log(f"收到候选 {len(proxies)}" + (" [TCP_ONLY]" if tcp_only else ""))
    if not proxies:
        print(json.dumps({"stats": {"candidates": 0, "tcp_ok": 0, "alive": 0}, "alive": []}))
        return 0

    # 1) TCP 预筛（廉价）
    with ThreadPoolExecutor(max_workers=min(400, len(proxies))) as ex:
        flags = list(ex.map(tcp_ok, proxies))
    tcp_idx = [i for i, ok in enumerate(flags) if ok]
    log(f"TCP 可连（中国）: {len(tcp_idx)}/{len(proxies)}")

    if not tcp_idx:
        print(json.dumps({"stats": {"candidates": len(proxies), "tcp_ok": 0, "alive": 0},
                          "alive": []}))
        return 0

    # TCP_ONLY：只回传通过 TCP 的下标，不做代理层测速。
    # 原因（2026-10-27 实测）：免费节点普遍封云厂商 IP —— 本机宽带 6/7 可代理，
    # 同批节点在腾讯云上 0/7 全超时。云机器能判"端口通不通"（7/100 两边一致），
    # 判不了"能否代理"。代理层留给客户端 url-test。
    if tcp_only:
        out = {
            "stats": {"candidates": len(proxies), "tcp_ok": len(tcp_idx),
                      "alive": len(tcp_idx), "elapsed": round(time.time() - t0, 1),
                      "view": "CN-TCP", "host": socket.gethostname()},
            "alive": [{"idx": i, "delay": 0} for i in tcp_idx],
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0

    # 2) 生成测试配置：节点重命名为 Q0000 形式，便于回映射
    tagged, name2idx = [], {}
    for n, i in enumerate(tcp_idx):
        q = {k: v for k, v in proxies[i].items() if k not in ("name", "_src", "_name")}
        nm = f"Q{n:04d}"
        q["name"] = nm
        name2idx[nm] = i
        tagged.append(q)
    names = [q["name"] for q in tagged]

    workdir = tempfile.mkdtemp(prefix="subhub-remote-")
    cfg = {
        "mixed-port": MIXED_PORT, "allow-lan": False, "mode": "rule",
        "log-level": "silent", "ipv6": False,
        "external-controller": f"127.0.0.1:{CTL_PORT}",
        # 必须自带 DNS 并禁用 IPv6：
        # 腾讯云系统的 resolv.conf 会把 cp.cloudflare.com 解析成 2606:4700:: 的 AAAA，
        # 而绝大多数 vless/ss 节点不支持 IPv6 转发 ⇒ delay 全失败（实测 0/7，
        # 开 DNS 关 IPv6 后恢复正常）。系统级 resolver 不可控，只能让内核自己解。
        "dns": {
            "enable": True,
            "ipv6": False,
            "nameserver": ["223.5.5.5", "119.29.29.29"],
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
        },
        "proxies": tagged,
        "proxy-groups": [{"name": "G", "type": "select", "proxies": names}],
        "rules": ["MATCH,G"],
    }
    cfg_path = os.path.join(workdir, "test.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    proc = subprocess.Popen([MIHOMO, "-f", cfg_path, "-d", workdir],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_api():
            log("!! 内核未就绪")
            print(json.dumps({"error": "mihomo api not ready"}))
            return 1

        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        target = urllib.parse.quote(TEST_URL, safe="")

        def one(nm):
            u = (f"http://127.0.0.1:{CTL_PORT}/proxies/{nm}/delay"
                 f"?url={target}&timeout={DELAY_TIMEOUT}")
            try:
                with op.open(u, timeout=DELAY_TIMEOUT / 1000 + 8) as r:
                    d = json.loads(r.read().decode()).get("delay")
                    return nm, (d if isinstance(d, int) else None)
            except Exception:
                return nm, None

        alive = []
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for nm, d in ex.map(one, names):
                if d:
                    alive.append({"idx": name2idx[nm], "delay": d})
        log(f"中国视角真可用: {len(alive)}/{len(tagged)}")

        alive.sort(key=lambda x: x["delay"])
        out = {
            "stats": {
                "candidates": len(proxies),
                "tcp_ok": len(tcp_idx),
                "alive": len(alive),
                "elapsed": round(time.time() - t0, 1),
                "view": "CN",
                "host": socket.gethostname(),
            },
            "alive": alive,
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        subprocess.run(["rm", "-rf", workdir], check=False)


if __name__ == "__main__":
    sys.exit(main())
