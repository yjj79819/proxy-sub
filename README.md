# subhub —— 自更新代理订阅（中国视角）

每天由 **GitHub Actions 自动触发**：境外抓取 + 真测速 → SSH 到腾讯云广州做**中国视角**复核
→ 生成一份带「国内直连 / 境外代理」分流的 mihomo 配置 → 自动 commit 回仓库。
FlClash 订阅一个 jsDelivr 地址即可，不需要本机跑任何任务。

## 一句话结论

公开免费节点几乎全是废的；而且**"在美国能连"和"在中国能连"是两件事**。
本项目把两件事拆成两层做，最终成品 `dist/config-cn.yaml` 的公共节点命中率从 6% 提到 **78%**。

## 实测数据链（2026-10-27，同一批源）

| 环节 | 数量 | 说明 |
|---|---|---|
| 6 个源解析出节点 | 3143 | clash-yaml + v2ray 链接 |
| 跨源去重 | 2885 | |
| TCP 可连（美国 runner） | 1605 / 2885 = 55.6% | 端口开着而已 |
| 真能代理出海（mihomo 实测） | 100 / 1605 = 6.2% | 唯一可信判据 |
| 其中**中国视角** TCP 可达 | 9 / 100 = 9% | 腾讯云广州实测 |
| 这 9 个在中国宽带代理层真能通 | **7 / 9 = 78%** | 本机复测，692~1512ms |

**关键对比**：不做中国视角这一层，直接把美国筛出的 100 个丢给国内用 → 只有 6 个真能用（6%）；
加了这一层，9 个里 7 个能用（78%）。**精度提升 13 倍**，而代价只是一次 SSH。

## 两端视角差异（必须知道）

| 视角 | 可用数 | 延迟 |
|---|---|---|
| Actions runner（美国 Azure） | 100 / 2885（3.5%） | 34~60ms |
| 中国宽带复测同一份 | 6 / 100（6%） | 239~1254ms |

同一个节点，美国测 40ms、国内测 900ms 是常态（跨境链路 + QoS），
**节点名里的延迟数字要看是哪一端测的**——本项目 `config-cn.yaml` 里公共节点不带延迟数字，
就是因为云服务器测不了代理层（见下）。

## 为什么中国视角要用云服务器，而不是在本机跑

用户明确要求：**不要本地计划任务，要 GitHub 上自动触发**。所以中国视角这一步由
Actions 通过 SSH 驱动腾讯云轻量（`111.229.4.201`，出口 China/Shanghai）完成。

代价是一个已实测的限制：**免费节点普遍封云厂商 IP**。
同一批节点，TCP 层两边完全一致（7/100 = 7/100），但代理层
云上 **0/7**、家庭宽带 **6/7** —— 云 IP 在应用层被拒。
所以云机器只判 **TCP 可达性**（这一层已经能砍掉 91% 的中国不可达节点），
**代理层的最终裁决交给客户端的 `url-test`**（FlClash 在真实网络里自己测，失败自动跳下一个）。

> 想在本机做代理层复核：`python refine.py`（手动工具，不是定时任务），会把 `dist/config-cn.yaml`
> 换成中国宽带实测过的版本。日常不需要跑。

## 架构

```
GitHub Actions（美国）   update.py
   抓取 6 源 → 解析 → 去重 → TCP 预筛 → mihomo 真测速 → dist/config.yaml（候选池 100）
        │
        │  SSH（secrets: SSH_KEY / SSH_HOST / SSH_USER）
        ▼
腾讯云广州 111.229.4.201（China/Shanghai）   remote_refine.py  TCP_ONLY=1
   对 100 个候选做中国 IP 的 TCP 可达性判定 → 回传存活下标（9 个）
        │
        ▼
   ci_refine.py  →  dist/config-cn.yaml（13 节点 = 自建 4 + 公共 9）
                →  dist/stats-cn.json
        │
        ▼
   自动 commit + push → jsDelivr 刷新缓存 → FlClash 订阅
```

| 文件 | 作用 |
|---|---|
| `update.py` | 主流水线：抓取/解析/去重/测速/生成配置 |
| `ci_refine.py` | Actions 内运行：传脚本到云机、SSH 执行、回填生成 `config-cn.yaml` |
| `remote_refine.py` | 在云机上运行：`TCP_ONLY=1` 时只做 TCP 预筛并输出 JSON |
| `refine.py` | 手动工具：本机（中国宽带）做代理层复核 |
| `counts.py` | 给 CI 拼提交信息（`US 100 / CN 9`） |
| `sources.txt` | 源清单（当前 6 个活源） |
| `builtin.yaml` | 自建 4 个 VLESS+WS+TLS 节点，永远排分组最前 |
| `dist/config.yaml` | 美国视角候选池（100） |
| `dist/config-cn.yaml` | **中国视角成品，FlClash 订阅这个** |
| `dist/stats*.json` | 每轮统计，便于回溯 |

**保护机制**：中国视角零可用时**不覆盖** `config-cn.yaml`（保留上一版），避免把订阅清空；
美国侧可用 < `MIN_OK`(5) 时不覆盖 `config.yaml`。

## 部署（已完成的步骤，重做时照抄）

```bash
gh repo create proxy-sub --public --source=. --push
gh api -X PUT repos/<用户>/proxy-sub/actions/permissions/workflow \
       -f default_workflow_permissions=write      # 否则 git push 403
gh secret set SSH_KEY  < ~/.ssh/zt_cloud_ed25519
gh secret set SSH_HOST --body 111.229.4.201
gh secret set SSH_USER --body root
gh workflow run update-subscription
```

云机一次性准备（已就绪）：
```bash
mkdir -p /opt/subhub && cd /opt/subhub
# mihomo linux-amd64 v1.19.33 放到 /opt/subhub/mihomo
apt-get install -y python3-yaml
```

定时：北京时间每天 **04:17**（`17 20 * * *` UTC）。整点 GitHub 容易排队，所以错开 17 分；
实际触发常延迟 5~30 分钟，属正常。改脚本/源清单也会自动触发一次。

## FlClash 接入

**主地址（每次都新鲜，实测 1.0s / 5845B）**：

```
https://gh-proxy.com/https://raw.githubusercontent.com/yjj79819/proxy-sub/main/dist/config-cn.yaml
```

**备用地址（企业 CDN 稳定，但 `@main` 有 ~12h 缓存，可能拿到昨晚的版本）**：

```
https://cdn.jsdelivr.net/gh/yjj79819/proxy-sub@main/dist/config-cn.yaml
```

为什么主地址不是 jsDelivr：实测 jsDelivr 的 `@main` 分支别名存在约 12 小时的分支解析缓存，
**调 `purge.jsdelivr.net` 返回 `CF:true FY:true` 也刷不掉**（等 10 分钟仍是旧版本）。
对比同一时刻 `@<commit-sha>` 路径立刻返回新内容 —— 说明不是内容缓存，而是分支别名解析缓存。
`raw.githubusercontent.com` 本身在国内 20s 后 `ConnectionReset`，所以走 gh-proxy 直通。

实测三个国内可达入口（2026-10-27）：

| 地址 | 延迟 | 新鲜度 |
|---|---|---|
| `gh-proxy.com` + raw | 1.0s | 最新 ✓ |
| `ghfast.top` + raw | 4.7s | 最新 ✓ |
| jsDelivr `@main` | 0.9s | 滞后 ≤12h |

镜像都是社区服务，随时可能失效；失效就换成上表另一行，或直接切 jsDelivr。
CI 最后一步会用 gh-proxy 回读一次，确认订阅真能取到、且含自建节点。

分组：

| 分组 | 类型 | 作用 |
|---|---|---|
| `🏠 自建 *` | — | 排最前，优先使用（4 个） |
| `♻️ 公共自动` | url-test | 公共节点自动选最快，**这就是代理层最终裁决** |
| `🎯 手动选择` | select | 手动指定，末位有 DIRECT |
| `🚀 境外代理` | fallback 300s | 规则命中后走这里 |

节点名形如 `PUB-001`（中国视角模式不带延迟）；`refine.py` 生成的版本形如 `PUB-001 703ms`。
**每天更新后名称会变**，别长期锁定单个节点。

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

第 5 条是硬约束：2026-10-26 盘中曾因系统代理残留导致 `intraday_quotes.json` 停更，
行情源必须永不进代理。

## 踩过的坑（都是实测，别再踩）

1. **`curl --noproxy '*'` 会让 `-x` 指定的代理失效**。两者同时用，curl 直接连目标，
   看起来返回了数据，其实是直连。本机曾有 4 轮"出口 IP"全是假读数，最后靠
   `curl -v` 看到 `Trying 127.0.0.1:17891` 才发现。**测代理时二选一**。
2. **fallback 组尾端别放 DIRECT**。节点全挂时会静默降级直连，境外站表现成"超时"而不是
   "代理失败"，极易误判成节点可用。
3. **`urllib.getproxies()` 会读到别人留下的代理端口**（本机曾有 60570/12334，不是 FlClash 的 7890），
   那些端口掐 `raw.githubusercontent.com` 返回 `RemoteDisconnected`。脚本必须显式指定出口
   （`SUBHUB_PROXY`），或 `ProxyHandler({})` 忽略系统代理。
4. **Git Bash 的 `/tmp` ≠ Windows Python 的 `/tmp`**。前者是 `%LOCALAPPDATA%\Temp`，
   后者解析成 `C:\tmp`。跨工具传路径一律用 Windows 绝对路径。
5. **`mode: global` 会绕过所有 rules**，测分流时配 `global` 等于没测。
6. **GEOSITE 分类名大小写**：本机 GeoSite.dat 全大写，写小写 `geolocation-!cn` 规则静默失效。
7. **FlClash 占着 1053(DNS) / 7890(mixed-port)**，验证配置要换端口，否则绑定失败但不报错。
8. **`mihomo -t` 校验配置需要 GeoSite.dat / geoip.metadb**，缺失时会尝试下载，国内直连必超时，
   误报 "test failed"。把这两个文件放到 `-d` 目录即可。
9. **GitHub Actions 的 YAML 块标量里不能出现顶格行**。在 `run: |` 里嵌 `python -c "` 多行代码，
   一旦某行顶格 → `while scanning a simple key ... could not find expected ':'`，
   工作流直接 0 秒 `failure` 且 `log not found`。**多行逻辑一律单独写 .py 文件**（本项目的 `counts.py`）。
10. **`.gitattributes` 必须 `eol=lf`**。Windows 提交的 CRLF 会让 Actions 的 bash 报
    `$'\r': command not found`。
11. **云服务器 IP 被免费节点应用层封禁**。腾讯云上 TCP 层与家庭宽带完全一致（7/100），
    但代理层 0/7 vs 6/7。别在云上做代理层判据，会得出"全死"的错误结论。
12. **云机 `resolv.conf` 可能优先返回 IPv6**（`2606:4700::`），导致测速全超时。
    内嵌 DNS 必须 `ipv6: false`。
13. **`mihomo -d <dir>` 才是加载配置的正确姿势**。用 `-f <任意文件名>` 起进程，
    `/proxies` 里可能拿到空的 proxies 列表，表现为"0 个节点"。
14. **jsDelivr 的 `@main` 别名会给你 12 小时前的文件，且 purge 刷不掉**。见上文"FlClash 接入"。
    订阅要每天变化的内容，别用 jsDelivr 分支别名，用直通 raw 的镜像。
15. **`ensure_mihomo()` 找内核要连 `bin/` 一起找**。本机把 `mihomo.exe` 放在 `bin/` 下时，
    旧逻辑只在根目录找，找不到就去 GitHub 下载 → 国内直连超时（`WinError 10060`），
    表现为"测速跑不起来"。已修：候选路径含 `bin/mihomo(.exe)`。

## 安全警告

公开免费节点的流量**不保证私密**。运营者能看到你的访问目标，部分节点会注入广告或劫持 DNS。

- **不要**用公共节点登录网银、券商、公司邮箱
- 本配置已把所有行情/交易域名强制直连，交易链路不经过公共节点
- 敏感操作请切到 `🏠 自建` 分组
- 公共节点随时会死，本项目的价值在于"每天帮你重新挑一批还活着的"，不是"给你稳定节点"
