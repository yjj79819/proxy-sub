#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subhub —— 公开节点订阅抓取 / 去重 / 真测速 / 生成 mihomo 配置

流程：
  1. 并发抓取 sources.txt 里的公开 Clash 格式订阅
  2. 解析 proxies，规范化，跨源去重
  3. TCP 预筛（廉价，砍掉大部分死节点）
  4. 启动 mihomo 内核，用 delay API 做真实 HTTP 出口测试（唯一可信判据）
  5. 只保留真可用节点，按延迟排序截断
  6. 与自建节点合并，套用「国内直连 / 境外代理」分流规则，产出 dist/config.yaml

设计原则：
  - 可用节点数不足 MIN_OK 时**不覆盖**上一版配置（防止一次抖动把订阅清空）
  - 所有判断基于实测读数，不做"TCP 通就算能用"的假设
  - 纯标准库 + pyyaml，无第三方依赖
"""
import io
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

try:
    import yaml
except ImportError:
    sys.exit("缺少 pyyaml：pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
SOURCES_FILE = os.path.join(HERE, "sources.txt")
BUILTIN_FILE = os.path.join(HERE, "builtin.yaml")   # 自建节点（可选）

# ---------------- 可调参数 ----------------
MIN_OK = 5             # 可用节点少于此值则不覆盖旧配置
MAX_KEEP = 100         # 最终保留的公共节点上限
DELAY_LIMIT = 3000     # 延迟超过此值(ms)视为不可用
TCP_TIMEOUT = 4.0
DELAY_TIMEOUT = 8000
TEST_WORKERS = 40
FETCH_TIMEOUT = 45
TEST_URL = "http://cp.cloudflare.com/generate_204"

UA = "clash-verge/v2.0.0"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def make_opener():
    """显式决定走哪个出口。

    - 设了 SUBHUB_PROXY（如 http://127.0.0.1:7890）-> 用它
    - 否则 -> 直连，且**忽略系统代理**
      这一条很关键：本机 ProxyEnable=1 时 urllib.getproxies() 会读到别的软件
      留下的代理端口（实测读到过 60570/12334），那些端口掐 raw.githubusercontent
      会直接 RemoteDisconnected。GitHub Actions 的 runner 在境外，直连即通。
    """
    proxy = os.environ.get("SUBHUB_PROXY", "").strip()
    if proxy:
        h = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        log(f"出口: 代理 {proxy}")
    else:
        h = urllib.request.ProxyHandler({})
        log("出口: 直连（忽略系统代理）")
    return urllib.request.build_opener(h)


OPENER = None


# ---------------- 1. 抓取 ----------------
def http_get(url, timeout=FETCH_TIMEOUT):
    global OPENER
    if OPENER is None:
        OPENER = make_opener()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Cache-Control": "no-cache",
    })
    with OPENER.open(req, timeout=timeout) as r:
        return r.read()


def load_sources():
    out = []
    if not os.path.exists(SOURCES_FILE):
        return out
    for line in io.open(SOURCES_FILE, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def fetch_one(url, retries=3):
    """单源抓取，失败重试。公开源所在链路抖动很大，一次超时不能当结论。"""
    last = None
    for i in range(retries):
        try:
            data = http_get(url)
            if len(data) < 200:
                raise ValueError(f"too small ({len(data)}B)")
            return url, data, None
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    return url, None, f"{type(last).__name__}: {last}"


def fetch_all(urls):
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for url, data, err in ex.map(fetch_one, urls):
            results.append((url, data, err))
    return results


# ---------------- 2. 解析 ----------------
def parse_subscription(raw):
    """返回 (proxies, 说明)。支持 Clash YAML；base64 的 v2ray 链接走另一条路。"""
    text = raw.decode("utf-8", "replace")
    stripped = text.strip()
    if "proxy-groups" in stripped[:4000] or "\nproxies:" in stripped[:4000] \
            or stripped.startswith("proxies:"):
        try:
            obj = yaml.safe_load(stripped)
        except Exception as e:
            return [], f"yaml 解析失败: {str(e)[:60]}"
        if not isinstance(obj, dict):
            return [], "非 YAML 字典"
        ps = obj.get("proxies") or []
        return [p for p in ps if isinstance(p, dict)], f"clash-yaml {len(ps)}"
    # 尝试 base64 -> v2ray 链接
    try:
        import base64
        compact = "".join(stripped.split())
        dec = base64.b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8", "replace")
        links = [l for l in dec.splitlines() if "://" in l]
        if links:
            conv = [c for c in (link_to_proxy(l) for l in links) if c]
            return conv, f"v2ray-links {len(links)}->{len(conv)}"
    except Exception:
        pass
    return [], "未识别格式"


def link_to_proxy(link):
    """把 vless/trojan/ss 链接转成 mihomo proxy dict。vmess 暂不支持。"""
    try:
        if link.startswith("vless://"):
            return _vless(link)
        if link.startswith("trojan://"):
            return _trojan(link)
        if link.startswith("ss://"):
            return _ss(link)
    except Exception:
        return None
    return None


def _q(u):
    return dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))


def _vless(link):
    u = urllib.parse.urlparse(link)
    q = _q(u)
    name = urllib.parse.unquote(u.fragment) or "vless"
    p = {
        "name": name, "type": "vless", "server": u.hostname,
        "port": u.port or 443, "uuid": u.username, "udp": True,
    }
    net = q.get("type", "tcp")
    p["network"] = net
    if q.get("security") in ("tls", "reality"):
        p["tls"] = True
        if q.get("sni"):
            p["servername"] = q["sni"]
        if q.get("fp"):
            p["client-fingerprint"] = q["fp"]
    if q.get("flow"):
        p["flow"] = q["flow"]
    if net == "ws":
        p["ws-opts"] = {"path": q.get("path", "/")}
        host = q.get("host") or q.get("sni")
        if host:
            p["ws-opts"]["headers"] = {"Host": host}
    elif net == "grpc":
        p["grpc-opts"] = {"grpc-service-name": q.get("serviceName", "")}
    return p


def _trojan(link):
    u = urllib.parse.urlparse(link)
    q = _q(u)
    p = {
        "name": urllib.parse.unquote(u.fragment) or "trojan", "type": "trojan",
        "server": u.hostname, "port": u.port or 443,
        "password": urllib.parse.unquote(u.username or ""), "udp": True,
    }
    if q.get("sni"):
        p["sni"] = q["sni"]
    if q.get("type") == "ws":
        p["network"] = "ws"
        p["ws-opts"] = {"path": q.get("path", "/")}
    return p


def _ss(link):
    import base64
    body = link[5:].split("#", 1)[0]
    if "@" not in body:
        body = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
    userinfo, hostport = body.rsplit("@", 1)
    if ":" not in userinfo:
        userinfo = base64.b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
    method, pwd = userinfo.split(":", 1)
    host, _, port = hostport.partition(":")
    return {"name": "ss", "type": "ss", "server": host, "port": int(port),
            "cipher": method, "password": pwd, "udp": True}


BAD_KEYS = ("_src",)


def normalize(p, src):
    q = {k: v for k, v in p.items() if k not in BAD_KEYS}
    q["_src"] = src
    t = q.get("type")
    if t not in ("vmess", "vless", "trojan", "ss", "hysteria2", "tuic"):
        return None
    srv = str(q.get("server") or "").strip()
    if not srv or srv in ("127.0.0.1", "localhost", "0.0.0.0"):
        return None
    try:
        port = int(q.get("port"))
    except Exception:
        return None
    if not (1 <= port <= 65535):
        return None
    q["server"] = srv
    q["port"] = port
    return q


def dedupe(proxies):
    seen, out = set(), []
    for p in proxies:
        cred = p.get("uuid") or p.get("password") or p.get("cipher", "")
        k = (p.get("type"), p["server"].lower(), p["port"], str(cred))
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


# ---------------- 3. TCP 预筛 ----------------
def tcp_ok(p):
    try:
        with socket.create_connection((p["server"], p["port"]), timeout=TCP_TIMEOUT):
            return True
    except Exception:
        return False


def tcp_filter(proxies, workers=400):
    if not proxies:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        flags = list(ex.map(tcp_ok, proxies))
    return [p for p, ok in zip(proxies, flags) if ok]


# ---------------- 4. mihomo 测速 ----------------
def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def ensure_mihomo():
    """优先用同目录/环境变量的 mihomo，否则下载。返回可执行文件路径。"""
    env = os.environ.get("MIHOMO_BIN")
    if env and os.path.exists(env):
        return env
    for cand in ("mihomo", "mihomo.exe",
                 os.path.join("bin", "mihomo"), os.path.join("bin", "mihomo.exe")):
        p = os.path.join(HERE, cand)
        if os.path.exists(p):
            return p
    cache = os.path.join(tempfile.gettempdir(), "subhub-mihomo")
    os.makedirs(cache, exist_ok=True)
    exe = os.path.join(cache, "mihomo.exe" if sys.platform == "win32" else "mihomo")
    if os.path.exists(exe):
        return exe

    log("下载 mihomo 内核…")
    rel = json.loads(http_get(
        "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest", timeout=40))
    tag = rel["tag_name"]
    want = None
    for a in rel.get("assets", []):
        n = a["name"]
        if "compatible" in n:
            continue
        if sys.platform == "win32" and "windows-amd64" in n and n.endswith(".zip"):
            want = a
        elif sys.platform != "win32" and "linux-amd64" in n and n.endswith(".gz"):
            want = a
        if want:
            break
    if not want:
        raise RuntimeError("找不到合适的 mihomo 资产")
    log(f"  {tag} -> {want['name']} ({want['size']//1048576}MB)")
    blob = http_get(want["browser_download_url"], timeout=180)
    tmp = exe + ".dl"
    with open(tmp, "wb") as f:
        f.write(blob)
    if want["name"].endswith(".zip"):
        with zipfile.ZipFile(tmp) as z:
            for n in z.namelist():
                if n.endswith(".exe"):
                    with z.open(n) as src, open(exe, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    break
    else:
        import gzip
        with gzip.open(tmp, "rb") as src, open(exe, "wb") as dst:
            shutil.copyfileobj(src, dst)
    os.remove(tmp)
    os.chmod(exe, 0o755)
    log(f"  已就绪: {exe}")
    return exe


def wait_api(port, timeout=25):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with op.open(f"http://127.0.0.1:{port}/version", timeout=2):
                return True
        except Exception:
            time.sleep(0.4)
    return False


def speedtest(proxies, mihomo):
    """用 mihomo delay API 做真实出口测试。返回 {name: delay_ms}"""
    if not proxies:
        return {}
    tagged = []
    for i, p in enumerate(proxies):
        q = {k: v for k, v in p.items() if k != "_src"}
        q["name"] = f"P{i:04d}"
        tagged.append(q)
    names = [q["name"] for q in tagged]

    ctl, mixed = free_port(), free_port()
    workdir = tempfile.mkdtemp(prefix="subhub-")
    cfg = {
        "mixed-port": mixed, "allow-lan": False, "mode": "rule",
        "log-level": "silent", "ipv6": False,
        "external-controller": f"127.0.0.1:{ctl}",
        "proxies": tagged,
        "proxy-groups": [{"name": "G", "type": "select", "proxies": names}],
        "rules": ["MATCH,G"],
    }
    cfg_path = os.path.join(workdir, "test.yaml")
    with io.open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    proc = subprocess.Popen(
        [mihomo, "-f", cfg_path, "-d", workdir],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_api(ctl):
            log("  !! 内核未就绪，测速跳过")
            return {}
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        target = urllib.parse.quote(TEST_URL, safe="")

        def one(n):
            u = f"http://127.0.0.1:{ctl}/proxies/{n}/delay?url={target}&timeout={DELAY_TIMEOUT}"
            try:
                with op.open(u, timeout=DELAY_TIMEOUT / 1000 + 8) as r:
                    d = json.loads(r.read().decode()).get("delay")
                    return n, d if isinstance(d, int) else None
            except Exception:
                return n, None

        out, done = {}, 0
        with ThreadPoolExecutor(max_workers=TEST_WORKERS) as ex:
            for n, d in ex.map(one, names):
                done += 1
                if d:
                    out[n] = d
                if done % 250 == 0:
                    log(f"  测速 {done}/{len(names)}  已存活 {len(out)}")
        return out
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------- 5. 生成配置 ----------------
def load_builtin():
    if not os.path.exists(BUILTIN_FILE):
        return [], []
    try:
        o = yaml.safe_load(io.open(BUILTIN_FILE, encoding="utf-8"))
        return (o.get("proxies") or []), (o.get("proxy-groups") or [])
    except Exception as e:
        log(f"builtin.yaml 解析失败: {e}")
        return [], []


def load_prev():
    """读上一版配置里的公共节点，作为候选池的一部分。

    公开源链路抖动大，一次跑批可能 4/6 源超时。把上一版还活着的节点带进来重新测速，
    可以避免"源挂了 -> 配置退化 -> 越来越少"的棘轮效应。失效的会被测速环节筛掉。
    """
    p = os.path.join(DIST, "config.yaml")
    if not os.path.exists(p):
        return []
    try:
        o = yaml.safe_load(io.open(p, encoding="utf-8"))
    except Exception:
        return []
    out = []
    for q in (o.get("proxies") or []):
        if not isinstance(q, dict):
            continue
        nm = str(q.get("name", ""))
        if nm.startswith("🏠"):        # 自建节点由 builtin.yaml 负责
            continue
        q = dict(q)
        q.pop("name", None)
        out.append(q)
    return out


def build_config(public, stats, airports=None, airport_label="✈️ 机场"):
    """生成 mihomo 配置。

    airports: 付费机场节点（已由调用方起好名字），**不参与筛选、全部保留**——
              付费节点不该被"延迟阈值"砍掉，死掉的交给客户端 url-test 跳过。
    airport_label: 机场分组名，同时也是配置里区分"付费/自建/免费"三档的标识。
    """
    builtin, _ = load_builtin()
    proxies = []
    bin_names, pub_names, air_names = [], [], []

    for p in builtin:
        q = dict(p)
        proxies.append(q)
        bin_names.append(q["name"])

    for p in (airports or []):
        q = {k: v for k, v in p.items() if k not in ("_src", "_name")}
        proxies.append(q)
        air_names.append(q["name"])

    for p in public:
        d = stats["delay"].get(p["_name"])
        # delay 为 0/None 表示这一轮没做代理层测速（TCP_ONLY 模式），名字不带 ms
        nm = (f"PUB-{len(pub_names)+1:03d} {d}ms" if d else f"PUB-{len(pub_names)+1:03d}")
        q = {k: v for k, v in p.items() if k not in ("_src", "_name", "name")}
        q["name"] = nm
        proxies.append(q)
        pub_names.append(nm)

    allp = air_names + bin_names + pub_names

    # 分组：机场（付费，最稳）> 自建 > 公共免费兜底
    # 注意：fallback 组里**不放 DIRECT**。境外站直连本来就不通，放 DIRECT 只会把
    # "节点挂了" 变成 "静默直连、看起来正常但实际被墙"，排查时极具误导性
    # （2026-10-27 实测踩过：fallback 尾端挂 DIRECT，出口显示本地 IP，误判成节点可用）。
    groups = []
    if bin_names:
        groups.append({"name": "🏠 自建", "type": "url-test",
                       "url": TEST_URL, "interval": 300, "tolerance": 50,
                       "lazy": True, "proxies": bin_names})
    if air_names:
        # 机场组用 fallback 不用 url-test：付费节点也会挂，fallback 在当前节点失败时
        # 立刻换下一个，比 url-test 等下一轮测速（90s 黑洞）可靠。
        # 顺序由调用方按"中国视角实测延迟"排好，所以 fallback 的第一个就是最快的。
        groups.append({"name": airport_label, "type": "fallback",
                       "url": TEST_URL, "interval": 180, "lazy": True,
                       "proxies": air_names})
    if pub_names:
        groups.append({"name": "♻️ 公共自动", "type": "url-test",
                       "url": TEST_URL, "interval": 600, "tolerance": 80,
                       "lazy": True, "proxies": pub_names})
    pick = ([airport_label] if air_names else []) + \
           (["🏠 自建"] if bin_names else []) + \
           (["♻️ 公共自动"] if pub_names else [])
    groups.append({"name": "🎯 手动选择", "type": "select",
                   "proxies": pick + allp + ["DIRECT"]})
    # 顺序即优先级：机场（付费，最稳）> 自建 > 公共免费。
    # 千万别把聚合组塞到自建前面去占位——fallback 是按列表顺序试的，
    # 放错位置会让"公共自动"抢在自建前面被选中。
    groups.append({"name": "🚀 境外代理", "type": "fallback",
                   "url": TEST_URL, "interval": 300,
                   "proxies": ([airport_label] if air_names else []) +
                              bin_names + pub_names +
                              (["♻️ 公共自动"] if pub_names else [])})

    cfg = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "external-controller": "127.0.0.1:9090",
        "unified-delay": True,
        "tcp-concurrent": True,
        "profile": {"store-selected": True, "store-fake-ip": True},
        "dns": {
            "enable": True, "ipv6": False, "listen": "0.0.0.0:1053",
            "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
            "fake-ip-filter": [
                "*.lan", "*.local",
                "+.sina.com.cn", "+.eastmoney.com", "+.qt.gtimg.cn",
                "+.10jqka.com.cn", "+.tdx.com.cn",
            ],
            "default-nameserver": ["223.5.5.5", "119.29.29.29"],
            "nameserver": ["223.5.5.5", "119.29.29.29", "114.114.114.114"],
            "fallback": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
            "fallback-filter": {"geoip": True, "geoip-code": "CN",
                                "ipcidr": ["240.0.0.0/4"]},
        },
        "proxies": proxies,
        "proxy-groups": groups,
        "rules": [
            "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
            "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
            "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
            "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
            "IP-CIDR,100.64.0.0/10,DIRECT,no-resolve",
            "IP-CIDR,169.254.0.0/16,DIRECT,no-resolve",
            "DOMAIN-SUFFIX,local,DIRECT",
            "DOMAIN-SUFFIX,lan,DIRECT",
            "DOMAIN-SUFFIX,legaga.top,DIRECT",
            "GEOSITE,GOOGLE,🚀 境外代理",
            "GEOSITE,GITHUB,🚀 境外代理",
            "GEOSITE,OPENAI,🚀 境外代理",
            "GEOSITE,TELEGRAM,🚀 境外代理",
            "GEOSITE,YOUTUBE,🚀 境外代理",
            "GEOSITE,TWITTER,🚀 境外代理",
            "GEOSITE,GEOLOCATION-CN,DIRECT",
            "DOMAIN-SUFFIX,cn,DIRECT",
            "DOMAIN-SUFFIX,sina.com.cn,DIRECT",
            "DOMAIN-SUFFIX,sinajs.cn,DIRECT",
            "DOMAIN-SUFFIX,eastmoney.com,DIRECT",
            "DOMAIN-SUFFIX,gtimg.cn,DIRECT",
            "DOMAIN-SUFFIX,qq.com,DIRECT",
            "DOMAIN-SUFFIX,10jqka.com.cn,DIRECT",
            "DOMAIN-SUFFIX,hexun.com,DIRECT",
            "DOMAIN-SUFFIX,cninfo.com.cn,DIRECT",
            "DOMAIN-SUFFIX,sse.com.cn,DIRECT",
            "DOMAIN-SUFFIX,szse.cn,DIRECT",
            "DOMAIN-SUFFIX,csindex.com.cn,DIRECT",
            "DOMAIN-SUFFIX,tdx.com.cn,DIRECT",
            "DOMAIN-SUFFIX,tdxdata.cn,DIRECT",
            "GEOSITE,GEOLOCATION-!CN,🚀 境外代理",
            "GEOIP,CN,DIRECT",
            "MATCH,🚀 境外代理",
        ],
    }
    return cfg


# ---------------- main ----------------
def main():
    t_start = time.time()
    urls = load_sources()
    log(f"源数量: {len(urls)}")

    raw_results = fetch_all(urls)
    all_proxies = []
    per_source = {}
    for url, data, err in raw_results:
        short = url.split("//")[-1][:46]
        if err:
            log(f"  [FAIL] {short}  {err}")
            per_source[short] = {"ok": False, "err": err}
            continue
        ps, note = parse_subscription(data)
        log(f"  [OK]   {short}  {len(data)}B  {note}")
        per_source[short] = {"ok": True, "note": note, "count": len(ps)}
        for p in ps:
            q = normalize(p, short)
            if q:
                all_proxies.append(q)

    prev = load_prev()
    if prev:
        n0 = len(all_proxies)
        for p in prev:
            q = normalize(p, "prev")
            if q:
                all_proxies.append(q)
        log(f"沿用上一版候选: +{len(all_proxies)-n0} 个")

    log(f"解析总计: {len(all_proxies)}")
    uniq = dedupe(all_proxies)
    log(f"去重后: {len(uniq)}")

    alive_tcp = tcp_filter(uniq)
    log(f"TCP 可连: {len(alive_tcp)}/{len(uniq)} ({len(alive_tcp)/max(1,len(uniq))*100:.1f}%)")

    mihomo = ensure_mihomo()
    delays = speedtest(alive_tcp, mihomo)
    log(f"真实可用: {len(delays)}/{len(alive_tcp)} "
        f"({len(delays)/max(1,len(alive_tcp))*100:.1f}%)")

    # 把 name 映射回 proxy
    by_name = {}
    for i, p in enumerate(alive_tcp):
        by_name[f"P{i:04d}"] = p
    scored = []
    for n, d in delays.items():
        if d <= DELAY_LIMIT:
            p = dict(by_name[n])
            p["_name"] = n
            scored.append((d, p))
    scored.sort(key=lambda x: x[0])
    keep = scored[:MAX_KEEP]
    log(f"保留(≤{DELAY_LIMIT}ms): {len(keep)} 个")

    stats = {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "sources": per_source,
        "counts": {
            "parsed": len(all_proxies), "unique": len(uniq),
            "tcp_ok": len(alive_tcp), "alive": len(delays),
            "kept": len(keep),
        },
        "top": [{"delay": d, "type": p["type"], "server": p["server"],
                 "port": p["port"]} for d, p in keep[:20]],
        "delay": {p["_name"]: d for d, p in keep},
    }

    os.makedirs(DIST, exist_ok=True)
    out_cfg = os.path.join(DIST, "config.yaml")
    out_stats = os.path.join(DIST, "stats.json")

    if len(keep) < MIN_OK:
        log(f"!! 可用节点仅 {len(keep)} < MIN_OK({MIN_OK})，保留上一版配置不覆盖")
        old = {}
        if os.path.exists(out_stats):
            try:
                old = json.load(io.open(out_stats, encoding="utf-8"))
            except Exception:
                pass
        old["last_failed_run"] = stats["updated"]
        old["last_failed_counts"] = stats["counts"]
        with io.open(out_stats, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=1)
        print("SKIP_WRITE")
        return 0

    cfg = build_config([p for d, p in keep], stats)
    with io.open(out_cfg, "w", encoding="utf-8") as f:
        f.write("# 由 subhub 自动生成，请勿手改（会被覆盖）\n")
        f.write(f"# 更新时间: {stats['updated']}\n")
        f.write(f"# 公共节点: {len(keep)} 个（源 {len(urls)}，去重 {len(uniq)}，TCP 通 {len(alive_tcp)}，实测可用 {len(delays)}）\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    with io.open(out_stats, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    log(f"完成，耗时 {time.time()-t_start:.0f}s")
    log(f"  配置: {out_cfg} ({os.path.getsize(out_cfg)}B, 共 {len(cfg['proxies'])} 节点)")
    print("WROTE_CONFIG")
    return 0


if __name__ == "__main__":
    sys.exit(main())
