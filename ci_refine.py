#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci_refine.py —— 在 GitHub Actions 里完成"中国视角"精筛

链路：
    Actions（美国）update.py 抓取+测速 -> dist/config.yaml（候选池）
        |
        | SSH
        v
    腾讯云广州（China/Shanghai）remote_refine.py TCP_ONLY=1 -> 只回传能连的下标
        |
        v
    本脚本生成 dist/config-cn.yaml（FlClash 订阅这个）

为什么代理层测速不放在云上：
    2026-10-27 实测，免费节点普遍封云厂商 IP —— 同一批节点本机宽带 6/7 可代理，
    腾讯云上 0/7 全超时；但 TCP 层两边完全一致（7/100 = 7/100）。
    所以云机器只判"端口通不通"，代理层交给客户端 url-test。

环境变量：
    SSH_KEY_PATH   私钥文件路径（Actions 从 secret 写入）
    SSH_HOST       服务器地址
    SSH_USER       默认 root
    SSH_PORT       默认 22
    REMOTE_DIR     默认 /opt/subhub
"""
import io
import json
import os
import subprocess
import sys
import time

import yaml

import update as U

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
CAND = os.path.join(DIST, "config.yaml")
OUT = os.path.join(DIST, "config-cn.yaml")
OUT_STATS = os.path.join(DIST, "stats-cn.json")

KEY = os.environ.get("SSH_KEY_PATH", "").strip()
HOST = os.environ.get("SSH_HOST", "").strip()
USER = os.environ.get("SSH_USER", "root").strip()
PORT = os.environ.get("SSH_PORT", "22").strip()
RDIR = os.environ.get("REMOTE_DIR", "/opt/subhub").strip()


def ssh_base():
    return ["ssh", "-i", KEY, "-p", PORT,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=20",
            "-o", "BatchMode=yes"]


def main():
    t0 = time.time()
    if not (KEY and HOST):
        U.log("!! 缺 SSH_KEY_PATH / SSH_HOST，无法做中国视角筛选")
        print("SKIP")
        return 1
    if not os.path.exists(CAND):
        U.log("!! 没有候选池 dist/config.yaml")
        print("SKIP")
        return 1

    cand = yaml.safe_load(io.open(CAND, encoding="utf-8"))
    pub = [p for p in (cand.get("proxies") or [])
           if str(p.get("name", "")).startswith("PUB")]
    U.log(f"候选池: {len(pub)} 个公共节点")
    if not pub:
        print("SKIP")
        return 1

    clean = []
    for p in pub:
        q = {k: v for k, v in p.items() if k not in ("name", "_src", "_name")}
        clean.append(q)
    payload = json.dumps({"proxies": clean}, ensure_ascii=False).encode()

    # 1) 传脚本（幂等覆盖，保证与仓库同步）
    U.log("上传 remote_refine.py 到中国节点…")
    r = subprocess.run(["scp", "-i", KEY, "-P", PORT,
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "LogLevel=ERROR",
                        os.path.join(HERE, "remote_refine.py"),
                        f"{USER}@{HOST}:{RDIR}/"], capture_output=True, timeout=120)
    if r.returncode != 0:
        U.log(f"!! scp 失败: {r.stderr.decode(errors='replace')[:300]}")
        print("SKIP")
        return 1

    # 2) 中国视角 TCP 预筛
    U.log("在腾讯云上做中国视角 TCP 预筛…")
    cmd = ssh_base() + [f"{USER}@{HOST}",
                        f"cd {RDIR} && TCP_ONLY=1 python3 remote_refine.py"]
    r = subprocess.run(cmd, input=payload, capture_output=True, timeout=900)
    if r.returncode != 0:
        U.log(f"!! 远程执行失败: {r.stderr.decode(errors='replace')[:400]}")
        print("SKIP")
        return 1
    stderr_txt = r.stderr.decode(errors="replace")
    for line in stderr_txt.strip().splitlines()[-6:]:
        U.log("  " + line.strip())
    try:
        res = json.loads(r.stdout.decode())
    except Exception as e:
        U.log(f"!! 远程输出非 JSON: {e} :: {r.stdout[:200]}")
        print("SKIP")
        return 1

    alive = res.get("alive") or []
    cstats = res.get("stats") or {}
    U.log(f"中国视角 TCP 可连: {len(alive)}/{len(pub)}")

    if not alive:
        U.log("!! 中国视角零可用，保留上一版 config-cn.yaml")
        print("SKIP")
        return 0

    # 3) 按下标回取节点
    kept = []
    for item in alive:
        i = item.get("idx")
        if isinstance(i, int) and 0 <= i < len(clean):
            q = dict(clean[i])
            q["_name"] = f"C{i:04d}"     # 仅作 key，delay=0 表示未做代理层测速
            kept.append(q)

    stats = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "view": "CN-TCP (腾讯云广州 TCP 预筛) + 客户端 url-test 兜底",
        "counts": {"candidates": len(pub), "tcp_ok": len(alive), "alive": len(kept),
                   "kept": len(kept)},
        "remote": cstats,
        "delay": {q["_name"]: 0 for q in kept},
    }

    cfg = U.build_config(kept, stats)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("# 由 ci_refine.py 生成（中国视角 TCP 预筛），请勿手改\n")
        f.write(f"# 更新时间: {stats['updated']}\n")
        f.write(f"# 候选池 {len(pub)} -> 中国 TCP 可连 {len(alive)} -> 写入 {len(kept)}\n")
        f.write("# 说明：延迟未标注，因代理层测速由客户端 url-test 完成\n")
        f.write("#       （云服务器 IP 被免费节点封禁，云上测代理层恒为 0）\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    with io.open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    zi = [p["name"] for p in cfg["proxies"] if p["name"].startswith("🏠")]
    U.log(f"完成 {time.time()-t0:.0f}s -> {OUT} "
          f"({len(cfg['proxies'])} 节点 = 自建 {len(zi)} + 公共 {len(kept)})")
    print("WROTE_CN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
