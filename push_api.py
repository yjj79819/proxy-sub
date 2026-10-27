#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""push_api.py —— 走 GitHub REST API 提交，绕过 git-over-HTTPS

为什么需要它：
    2026-10-27 实测，FlClash 选中的免费节点随时会死（端口还开着，但 TLS 直接 EOF），
    这时 `git push` 报 `schannel: failed to receive handshake`，而 `api.github.com`
    在国内可以直连（gh CLI 一直好用）。所以留一条"不依赖代理"的提交通道。

用法：
    python push_api.py <文件1> [文件2 ...] -m "提交信息"

只做「把本地这几个文件的内容提交到 main」，不做删除、不改其它文件
（删除仍需 git push；本脚本是应急通道，不是替代品）。
"""
import base64
import json
import os
import subprocess
import sys

GH = (r"C:\Users\yjj\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_"
      r"Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe")
REPO = "yjj79819/proxy-sub"
HERE = os.path.dirname(os.path.abspath(__file__))


def gh(method, path, body=None):
    env = dict(os.environ, GH_CONFIG_DIR="C:/Users/yjj/.workbuddy/ghconfig")
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    cmd = [GH, "api", "--method", method, path]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(body).encode() if body is not None else None,
                       capture_output=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"{method} {path} -> "
                           f"{r.stderr.decode(errors='replace')[:400]}")
    return json.loads(r.stdout or b"{}")


def main():
    argv = sys.argv[1:]
    if "-m" not in argv or len(argv) < 3:
        print(__doc__)
        return 2
    i = argv.index("-m")
    files, msg = argv[:i], argv[i + 1]

    head = gh("GET", f"/repos/{REPO}/git/ref/heads/main")["object"]["sha"]
    base_tree = gh("GET", f"/repos/{REPO}/git/commits/{head}")["tree"]["sha"]

    tree = []
    for f in files:
        p = os.path.join(HERE, f)
        with open(p, "rb") as fh:
            blob = gh("POST", f"/repos/{REPO}/git/blobs", {
                "content": base64.b64encode(fh.read()).decode(),
                "encoding": "base64"})["sha"]
        tree.append({"path": f, "mode": "100644", "type": "blob", "sha": blob})
        print(f"  blob {f} -> {blob[:8]}")

    nt = gh("POST", f"/repos/{REPO}/git/trees",
            {"base_tree": base_tree, "tree": tree})["sha"]
    nc = gh("POST", f"/repos/{REPO}/git/commits",
            {"message": msg, "tree": nt, "parents": [head]})["sha"]
    gh("PATCH", f"/repos/{REPO}/git/refs/heads/main",
       {"sha": nc, "force": False})
    print(f"OK {nc[:8]} (parent {head[:8]}) {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
