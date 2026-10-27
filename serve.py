#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serve.py —— 私有订阅端点（跑在腾讯云 111.229.4.201:18080）

为什么不用 jsDelivr / gh-proxy：
    合并配置里有付费机场节点（uuid/密码就是凭据）。放公开仓库 = 谁都能白嫖，
    机场检测到多 IP 并发可能封号。所以这一份**只走自己的服务器**。

为什么用国内服务器：
    国内直连 30~60ms、无 CDN 缓存（jsDelivr 的 @main 分支别名有 12h 缓存且刷不掉），
    宿主就在国内，比任何 CDN 都快、都新。

安全模型：
    路径里带 32 位随机 token（存在 /opt/subhub/serve.token，600 权限），
    不匹配一律 404，不列目录、不做重定向、不暴露文件名。
    这是"不可猜"而非"鉴权"——够用，但别把 URL 发到公开场合。

用法：
    python3 serve.py            # 前台跑（systemd 用这个）
    curl -s http://127.0.0.1:18080/s/<token>.yaml | head
"""
import http.server
import os
import re
import socketserver
import sys

PORT = int(os.environ.get("PORT", "18080"))
ROOT = os.environ.get("ROOT", "/opt/subhub/www")
TOKEN_FILE = os.environ.get("TOKEN_FILE", "/opt/subhub/serve.token")
FILE = os.environ.get("FILE", "config-all.yaml")

if not os.path.exists(TOKEN_FILE):
    sys.exit(f"缺少 token 文件: {TOKEN_FILE}")
with open(TOKEN_FILE, encoding="utf-8") as _f:
    TOKEN = _f.read().strip()
if len(TOKEN) < 16:
    sys.exit("token 太短，拒绝启动")

PAT = re.compile(r"^/s/([0-9A-Za-z_-]{16,64})\.ya?ml$")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "subhub"
    sys_version = ""

    def _body(self):
        """token 不对或文件不存在都返回 None，不区分（不给探测者任何信息）。"""
        m = PAT.match(self.path.split("?", 1)[0])
        if not m or m.group(1) != TOKEN:
            return None
        p = os.path.join(ROOT, FILE)
        try:
            with open(p, "rb") as f:
                return f.read()
        except OSError:
            return None

    def _send(self, with_body):
        body = self._body()
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/yaml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        # 部分客户端靠这个头显示流量；本文件不含订阅信息，给 0 更诚实
        self.send_header("Subscription-Userinfo",
                         "upload=0; download=0; total=0; expire=0")
        self.send_header("Profile-Update-Interval", "12")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self):
        self._send(True)

    def do_HEAD(self):
        self._send(False)

    def log_message(self, fmt, *args):
        # 不记访问日志：URL 里有 token，落盘等于二次泄露
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    print(f"serving {ROOT}/{FILE} on 0.0.0.0:{PORT} (token {TOKEN[:4]}…)", flush=True)
    with Server(("0.0.0.0", PORT), Handler) as srv:
        srv.serve_forever()
