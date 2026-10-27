# subhub —— 自更新代理订阅

每天自动从公开源抓取节点、去重、**真测速**，生成一份带「国内直连 / 境外代理」分流的 mihomo 配置，
推到 GitHub 仓库，FlClash 订阅 raw 地址即可。

## 为什么需要它

公开免费节点看着多（本次抓 3043 个 / 去重 2885 个），但：

| 环节 | 数量 | 比例 |
|---|---|---|
| 解析出的节点 | 3043 | — |
| 去重后 | 2885 | 94.8% |
| TCP 能连通 | 1205 | 41.8% |
| **真实能代理出海** | **17** | **1.4%**（对 TCP 通的比例） |
| 延迟 ≤3000ms 保留 | 9 | 0.3%（对去重总数） |

**TCP 能连 ≠ 能用。** 43% 的节点端口是开的，但其中 98.6% 建立 TLS/WS 后无法完成一次 HTTP 请求
（多为已废弃、限速、被墙、或蜜罐）。所以本项目的核心不是"抓得多"，而是**筛得准**——用真内核
逐个发 HTTP 请求，只留实测能出海的。

## 架构

```
抓取(6 源) → 解析(clash yaml / v2ray 链接) → 规范化 → 跨源去重
   → TCP 预筛(廉价, 砍 58%) → mihomo 真测速(唯一可信判据)
   → 与 builtin.yaml 自建节点合并 → 生成 dist/config.yaml → commit
```

- `update.py` 主脚本（纯标准库 + pyyaml）
- `sources.txt` 源清单
- `builtin.yaml` 自建节点，永远排分组最前
- `dist/config.yaml` 产出，FlClash 订阅这个
- `dist/stats.json` 每轮统计，便于回溯

**保护机制**：可用节点 < `MIN_OK`(5) 时不覆盖旧配置，只往 stats 里记 `last_failed_run`。
避免某次网络抖动把订阅清空。

## 部署

```bash
# 1. 建仓库（public，FlClash 才能直接拉 raw）
gh repo create proxy-sub --public --source=. --push

# 2. 确认 Actions 有写权限
#    Settings → Actions → General → Workflow permissions → Read and write

# 3. 手动触发一次验证
gh workflow run update-subscription
```

定时：北京时间每天 **04:17**（`17 20 * * *` UTC）。GitHub 的 cron 在整点容易排队，
所以错开 17 分；实际触发常延迟 5~30 分钟，属正常。

## FlClash 接入

```
https://raw.githubusercontent.com/<用户名>/proxy-sub/main/dist/config.yaml
```

国内直连 raw.githubusercontent 会被墙，两个办法：

1. **jsDelivr CDN**（推荐，国内可达）：
   `https://cdn.jsdelivr.net/gh/<用户名>/proxy-sub@main/dist/config.yaml`
   注意 jsDelivr 有缓存，新鲜度不如 raw。
2. FlClash 已有可用配置时，用 raw 地址更新（走自身代理）。

默认分组：

| 分组 | 类型 | 作用 |
|---|---|---|
| `🏠 自建 *` | — | 排最前，优先使用 |
| `♻️ 公共自动` | url-test 600s | 公共节点自动选最快 |
| `🎯 手动选择` | select | 手动指定，末位有 DIRECT |
| `🚀 境外代理` | fallback 300s | 规则命中后走这里 |

节点名形如 `PUB-001 703ms`，数字是当日实测延迟。**每天更新后名称会变**，
所以别长期锁定单个节点，用 `♻️ 公共自动` 或 `🚀 境外代理`。

## 分流规则

顺序即优先级，改之前先想清楚会盖住谁：

1. 私有网段 / `local` / `lan` → DIRECT
2. `legaga.top` → DIRECT（防节点流量绕回代理）
3. `GEOSITE,GOOGLE/GITHUB/OPENAI/TELEGRAM/YOUTUBE/TWITTER` → 代理（前置，防被国内库误判）
4. `GEOSITE,GEOLOCATION-CN` + `DOMAIN-SUFFIX,cn` → DIRECT
5. 行情源 13 个域名（sina/eastmoney/gtimg/10jqka/tdx…）→ **强制 DIRECT**
6. `GEOSITE,GEOLOCATION-!CN` → 代理
7. `GEOIP,CN` → DIRECT
8. `MATCH` → 代理

第 5 条是这台机器的硬约束：2026-10-26 盘中曾因系统代理残留导致 `intraday_quotes.json` 停更，
行情源必须永不进代理。

## 踩过的坑（都是实测，别再踩）

1. **`curl --noproxy '*'` 会让 `-x` 指定的代理失效**。两者同时用，curl 直接连目标，
   看起来返回了数据，其实是直连。本机曾有 4 轮"出口 IP"全是假读数，最后靠
   `curl -v` 看到 `Trying 127.0.0.1:17891` 才发现。**测代理时二选一**。
2. **fallback 组尾端别放 DIRECT**。节点全挂时会静默降级直连，境外站表现成"超时"而不是
   "代理失败"，极易误判成节点可用。
3. **本机 `urllib.getproxies()` 读到 60570 / 12334 这类别人的代理端口**（不是 FlClash 的 7890），
   那些端口会掐 raw.githubusercontent 返回 `RemoteDisconnected`。脚本必须显式指定出口
   （`SUBHUB_PROXY`），或直接用 `ProxyHandler({})` 忽略系统代理。
4. **Git Bash 的 `/tmp` ≠ Windows Python 的 `/tmp`**。前者是 `%LOCALAPPDATA%\Temp`，
   后者解析成 `C:\tmp`。跨工具传路径一律用 Windows 绝对路径。
5. **`mode: global` 会绕过所有 rules**，测分流时配 `global` 等于没测。
6. **GEOSITE 分类名大小写**：本机 GeoSite.dat 全大写，写小写 `geolocation-!cn` 规则静默失效。
7. **FlClash 占着 1053(DNS) / 7890(mixed-port)**，验证配置要换端口，否则绑定失败但不报错。
8. **`mihomo -t` 校验配置需要 GeoSite.dat / geoip.metadb**，缺失时会尝试下载，国内直连必超时，
   误报 "test failed"。把这两个文件放到 `-d` 目录即可。

## 安全警告

公开免费节点的流量**不保证私密**。运营者能看到你的访问目标，部分节点会注入广告或劫持 DNS。

- **不要**用公共节点登录网银、券商、公司邮箱
- 本配置已把所有行情/交易域名强制直连，交易链路不经过公共节点
- 敏感操作请切到 `🏠 自建` 分组
