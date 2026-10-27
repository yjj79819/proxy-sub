#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge.py —— 把「付费机场 + 自建 + 中国视角筛过的免费节点」合成一份配置

产出 `dist/config-all.yaml`，**不进 git**（里面有付费机场的 uuid/密码，等于账号凭据）。
这份文件通过 SCP 送到腾讯云 `/opt/subhub/www/`，由 `serve.py` 在 18080 端口用
带 token 的路径对外提供，专供自己的 FlClash 订阅。

为什么不用 CDN 托管：
    jsDelivr/gh-proxy 都是公开的，付费节点上去=谁都能白嫖，机场多 IP 并发会封号。

数据来源：
    AIRPORT_SUBS 环境变量，每行 `标签|订阅地址|User-Agent`
    或本地文件 `airports.local.txt`（同样格式，# 开头为注释，已 gitignore）

    实测（2026-10-27）：机场按 UA 分流，浏览器 UA 拿到的是诱饵节点
    （3 条广告名 + 5 条假 IP），必须用 `clash.meta` 这类客户端 UA 才给真节点。

用法：
    python merge.py                 # 只生成 dist/config-all.yaml
    python merge.py --push          # 生成后 SCP 到服务器（读 SSH_KEY_PATH/SSH_HOST/SSH_USER）
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
CN = os.path.join(DIST, "config-cn.yaml")
OUT = os.path.join(DIST, "config-all.yaml")
OUT_STATS = os.path.join(DIST, "stats-all.json")
LOCAL_SUBS = os.path.join(HERE, "airports.local.txt")

DEFAULT_UA = "clash.meta"

# 机场订阅里混的"非节点"条目：广告位、到期提示、客户端引导。
# 它们的 server 常是 110.110.110.110 这种占位 IP，或被标成"客户端不支持"。
JUNK_WORDS = ("官网", "更新", "到期", "剩余", "流量", "请点击", "不支持",
              "下载", "订阅", "客户端", "购买", "续费", "过期", "公告",
              "expire", "traffic", "website", "http")
JUNK_SERVERS = ("", "127.0.0.1", "localhost", "0.0.0.0", "110.110.110.110")

# 协议鲁棒性分级（0=优先）。
# 2026-10-27 本机实测（同一批 32 个机场节点）：tuic 4/15=27%，vless 12/17=71%。
# 原因：tuic/hysteria2 走 QUIC/UDP，家宽与部分运营商对 UDP 有 QoS 甚至直接丢；
# 云服务器 UDP 干净，所以在云上测起来 tuic 反而正常——这就是"云上测通、家里不通"的根因。
# 所以 fallback 顺序必须"TCP 类优先"，同级别内再按中国实测延迟排。
PROTO_RANK = {"vless": 0, "trojan": 0, "vmess": 0, "ss": 0,
              "hysteria2": 1, "tuic": 1}


def load_airports():
    """返回 [(标签, url, ua)]。环境变量优先，其次本地文件。"""
    raw = os.environ.get("AIRPORT_SUBS", "").strip()
    src = "env"
    if not raw and os.path.exists(LOCAL_SUBS):
        raw = io.open(LOCAL_SUBS, encoding="utf-8").read()
        src = "airports.local.txt"
    out = []
    for line in raw.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("|")]
        label = parts[0] or "机场"
        url = parts[1] if len(parts) > 1 else ""
        ua = parts[2] if len(parts) > 2 and parts[2] else DEFAULT_UA
        if url.startswith("http"):
            out.append((label, url, ua))
    U.log(f"机场源: {len(out)} 条（来自 {src}）")
    return out


def fetch(url, ua, retries=3):
    for i in range(retries):
        try:
            op = U.make_opener()
            req = U.urllib.request.Request(url, headers={
                "User-Agent": ua, "Accept": "*/*", "Cache-Control": "no-cache"})
            with op.open(req, timeout=45) as r:
                data = r.read()
            if len(data) < 100:
                raise ValueError(f"too small ({len(data)}B)")
            return data
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    U.log(f"  !! 抓取失败 {url[:50]}… {type(last).__name__}: {last}")
    return None


def is_junk(p):
    nm = str(p.get("name", ""))
    srv = str(p.get("server", "")).strip()
    if srv in JUNK_SERVERS:
        return True
    low = nm.lower()
    if any(w.lower() in low for w in JUNK_WORDS):
        return True
    if p.get("type") not in ("vmess", "vless", "trojan", "ss", "hysteria2", "tuic"):
        return True
    return False


def parse_any(raw):
    """优先当 Clash YAML 读（机场用客户端 UA 时基本都返回 YAML），失败再试 base64 链接。

    update.parse_subscription 只在**前 4000 字符**里找 `proxies:`，机场的 YAML 常有
    几十 KB 的规则在节点之前，判断会漏；所以这里先整篇 yaml.safe_load。
    """
    text = raw.decode("utf-8", "replace").strip()
    head = text.split("\n", 1)[0]
    if ":" in head and not head.startswith(("dm1lc3M", "dmxlc3M", "dHJvamFu", "c3M6")):
        try:
            o = yaml.safe_load(text)
            if isinstance(o, dict) and isinstance(o.get("proxies"), list):
                return ([p for p in o["proxies"] if isinstance(p, dict)],
                        f"clash-yaml {len(o['proxies'])}")
        except Exception:
            pass
    return U.parse_subscription(raw)


def collect_airports(subs):
    """抓所有机场订阅 -> 统一命名的 proxy 列表。全部保留，不做连通性筛选。"""
    out = []
    for label, url, ua in subs:
        raw = fetch(url, ua)
        if not raw:
            continue
        ps, note = parse_any(raw)
        U.log(f"  [{label}] {note} (ua={ua})")
        kept = 0
        for p in ps:
            q = U.normalize(p, label)
            if not q or is_junk(q):
                continue
            orig = str(q.pop("name", "") or "node").strip()
            q["name"] = f"✈️{label} {orig}"
            out.append(q)
            kept += 1
        U.log(f"  [{label}] 有效 {kept}/{len(ps)}")
    before = len(out)
    out = U.dedupe(out)
    if before != len(out):
        U.log(f"  跨源去重: {before} -> {len(out)}")
    # YAML 里重名会被 mihomo 当成同一个节点，二次去重名字
    seen, uniq = set(), []
    for p in out:
        n, i = p["name"], 2
        while n in seen:
            n = f"{p['name']}#{i}"
            i += 1
        p["name"] = n
        seen.add(n)
        uniq.append(p)
    return uniq


def load_cn_public():
    """中国视角筛过的公共节点（config-cn.yaml）。拿不到就退到美国候选池。"""
    for path, tag in ((CN, "CN"), (os.path.join(DIST, "config.yaml"), "US")):
        if not os.path.exists(path):
            continue
        o = yaml.safe_load(io.open(path, encoding="utf-8"))
        pub = []
        for p in (o.get("proxies") or []):
            if not str(p.get("name", "")).startswith("PUB"):
                continue
            q = {k: v for k, v in p.items() if k != "name"}
            q["_name"] = p["name"]          # 只用于 build_config 取延迟，不写进产物
            pub.append(q)
        if pub:
            U.log(f"公共节点来源: {tag} 视角 {len(pub)} 个")
            return pub
    U.log("!! 没有可用的公共节点池")
    return []


def ssh_conf():
    return (os.environ.get("SSH_KEY_PATH", "").strip(),
            os.environ.get("SSH_HOST", "").strip(),
            os.environ.get("SSH_USER", "root").strip(),
            os.environ.get("SSH_PORT", "22").strip(),
            os.environ.get("REMOTE_DIR", "/opt/subhub").strip())


def ssh_base(key, user, host, port):
    return ["ssh", "-i", key, "-p", port, "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", f"{user}@{host}"]


def cn_measure(air):
    """把机场节点交给腾讯云做**中国视角代理层**实测，返回 {下标: 延迟ms}。

    2026-10-27 实测：付费机场节点在云上能正常代理（33/53 通，336~553ms），
    和免费公共节点完全不同（那些是 0/7，应用层被封云 IP）。
    所以机场节点可以做中国视角的真测速，不需要靠客户端赌。
    """
    key, host, user, port, rdir = ssh_conf()
    if not (key and host):
        U.log("  跳过中国视角测速（缺 SSH_KEY_PATH / SSH_HOST）")
        return {}
    blank = [{k: v for k, v in p.items() if k not in ("name", "_src", "_name")} for p in air]
    payload = json.dumps({"proxies": blank}, ensure_ascii=False).encode()
    r = subprocess.run(["scp", "-i", key, "-P", port,
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null",
                        "-o", "LogLevel=ERROR",
                        os.path.join(HERE, "remote_refine.py"),
                        f"{user}@{host}:{rdir}/"], capture_output=True, timeout=120)
    if r.returncode != 0:
        U.log(f"  !! 上传 remote_refine.py 失败: {r.stderr.decode(errors='replace')[:200]}")
        return {}
    r = subprocess.run(ssh_base(key, user, host, port) +
                       [f"cd {rdir} && python3 remote_refine.py"],
                       input=payload, capture_output=True, timeout=900)
    for line in r.stderr.decode(errors="replace").strip().splitlines()[-3:]:
        U.log("  " + line.strip())
    if r.returncode != 0:
        U.log(f"  !! 远程测速失败: {r.stderr.decode(errors='replace')[:300]}")
        return {}
    try:
        res = json.loads(r.stdout.decode())
    except Exception as e:
        U.log(f"  !! 远程输出非 JSON: {e}")
        return {}
    out = {int(x["idx"]): int(x["delay"]) for x in (res.get("alive") or [])}
    st = res.get("stats") or {}
    U.log(f"  中国视角代理层可用: {len(out)}/{len(air)}  "
          f"(tcp_ok={st.get('tcp_ok')} 耗时 {st.get('elapsed')}s)")
    return out


def apply_cn(air, delays):
    """按中国实测延迟排序；大部分都测不通时保守处理（全留、只排序不剔除）。"""
    if not delays:
        return air
    for i, p in enumerate(air):
        p["_d"] = delays.get(i)
    alive = [p for p in air if p.get("_d")]
    dead = [p for p in air if not p.get("_d")]
    ratio = len(alive) / max(1, len(air))
    if ratio >= 0.3 and len(alive) >= 3:
        seen, keep = set(), []
        # 先按协议分级（TCP 优先），同级别再按中国实测延迟
        for p in sorted(alive, key=lambda x: (PROTO_RANK.get(x["type"], 0), x["_d"])):
            nm = f"{p['name']} {p['_d']}ms"
            while nm in seen:
                nm += "'"
            seen.add(nm)
            p["name"] = nm
            keep.append(p)
        tcp = sum(1 for p in keep if PROTO_RANK.get(p["type"], 0) == 0)
        U.log(f"  按中国延迟排序：保留 {len(keep)} 个"
              f"（TCP 类 {tcp} + UDP 类 {len(keep)-tcp}，剔除 {len(dead)} 个测不通的）")
        return keep
    U.log(f"  可用率偏低({ratio:.0%})，保守处理：全部保留、只排序不剔除")
    ranked = sorted(air, key=lambda x: (PROTO_RANK.get(x.get("type"), 0),
                                        x.get("_d") is None, x.get("_d") or 99999))
    for p in ranked:
        p.pop("_d", None)
    return ranked


def push(path):
    key, host, user, _port, rdir = ssh_conf()
    if not (key and host):
        U.log("!! 缺 SSH_KEY_PATH / SSH_HOST，跳过上传")
        return False
    cmd = ["scp", "-i", key, "-P", os.environ.get("SSH_PORT", "22"),
           "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR", path, f"{user}@{host}:{rdir}/www/config-all.yaml"]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if r.returncode != 0:
        U.log(f"!! 上传失败: {r.stderr.decode(errors='replace')[:300]}")
        return False
    return True


def main():
    t0 = time.time()
    subs = load_airports()
    air = collect_airports(subs) if subs else []
    if not air:
        U.log("!! 没有任何机场节点（AIRPORT_SUBS 为空或全部抓取失败）")
        # 不直接退出：退化成"自建+公共"的合并版，仍然可用
    elif "--no-cn" not in sys.argv:
        U.log("机场节点交腾讯云做中国视角代理层测速…")
        air = apply_cn(air, cn_measure(air))
    pub = load_cn_public()
    if not air and not pub:
        print("SKIP")
        return 1

    stats = {"updated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
             "delay": {}}
    cfg = U.build_config(pub, stats, airports=air, airport_label="✈️ 机场")

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("# 由 merge.py 生成：付费机场 + 自建 + 中国视角筛过的公共节点，请勿手改\n")
        f.write(f"# 更新时间: {stats['updated']}\n")
        f.write(f"# 机场 {len(air)} + 自建 {sum(1 for p in cfg['proxies'] if p['name'].startswith('🏠'))}"
                f" + 公共 {sum(1 for p in cfg['proxies'] if p['name'].startswith('PUB'))}\n")
        f.write("# 本文件含付费节点凭据，仅经私有端点提供，禁止公开\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    meta = {
        "updated": stats["updated"],
        "airports": {lb: sum(1 for p in air if p["name"].startswith(f"✈️{lb}"))
                     for lb, _, _ in subs},
        "airport_total": len(air),
        "nodes": len(cfg["proxies"]),
        "groups": [(g["name"], g["type"], len(g["proxies"])) for g in cfg["proxy-groups"]],
    }
    with io.open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    U.log(f"完成 {time.time()-t0:.0f}s -> {OUT} "
          f"({len(cfg['proxies'])} 节点：机场 {len(air)} + 自建 "
          f"{sum(1 for p in cfg['proxies'] if p['name'].startswith('🏠'))} + 公共 "
          f"{sum(1 for p in cfg['proxies'] if p['name'].startswith('PUB'))})")

    if "--push" in sys.argv:
        U.log("上传到腾讯云私有端点…")
        if push(OUT):
            U.log("上传成功")
    print("WROTE_ALL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
