# subhub —— 代理订阅

## 先用这个（2026-10-27 定稿主线）

```
https://gh-proxy.com/https://raw.githubusercontent.com/yjj79819/proxy-sub/main/fixed.yaml
```

FlClash → 配置 → 添加 → 从 URL 导入 → 粘贴上面这行 → 完成。**填一次，之后不用再管。**

这一份是 `fixed.yaml`，它自己不含节点，而是用 mihomo 原生的 `proxy-providers`
把 6 个公开源**合并**进来，让客户端按各自节奏去拉。那 6 个源本来就是每天自动更新的，
所以这份配置**内容几乎永远不用改** —— 没有脚本、没有定时任务、没有 CI 参与。

| 组成 | 内容 |
|---|---|
| 自建 | 4 条 `🏠 自建` VLESS+WS+TLS，固定写死，永远排最前 |
| 合并源 | anaer / free18 / mahdibland / peasoft / ripaojiedian / ermaozi，共 2987 节点 |
| 日常路径 | `🚀 境外代理` = 自建 4 + 5 个小源组（**不含 anaer**，见下） |
| 分流 | `GEOIP,CN` 与 `.cn` 域名直连，其余走代理 |

## 为什么放弃"每天精筛重生成"

原方案是 Actions 每天抓全量 → 真测速 → SSH 到腾讯云做中国视角复核 → 生成精简配置。
技术上成立，**但价值不成立**：免费节点 10 分钟就失效，早上 4 点筛出的"可用"节点，
用户上午真用的时候基本已经死了。为一份当天中午就过期的东西维护整条流水线，不划算。

改成固定合并后，**筛选这件事交给客户端自己做** —— 它在你真实网络里测，比任何离线路数都准；
死了就按 fallback 顺序跳下一个。

## 性能实测（本机 500 节点冷启动，2026-10-27 21:5x）

| 指标 | 实测值 |
|---|---|
| 内核就绪 | 0.5s |
| **日常组选中第一个可用节点** | **2.5s**（自建节点立刻接管，此时已能上网） |
| 5 个小源 500 节点全部测完 | **103.6s** |
| 500 节点真可用 | 22 个（4.4%） |

**关键**：能上网的时刻是 2.5 秒，不是 104 秒 —— 后台测速在跑，不挡网页。
慢的只是"打开分组界面看全量延迟数字"这一步。

### 2487 个节点的组怎么处理

`anaer` 这一个源就有 2487 个节点，占全部节点的 83%。实测它冷启动跑几分钟才测完 4%，
而已测的 139 个里**可用数 = 0**；同期其余 5 源 500 个节点已 100% 测完。
按 500 节点/104s 外推，它单独要 **≈9 分钟**。

所以分层处理，不删：

| 位置 | 是否含 anaer | 效果 |
|---|---|---|
| `🚀 境外代理`（日常路径） | ❌ 不含 | 冷启动不被拖慢 |
| `🎯 手动选择` → `♻️ anaer` | ✅ 含，但排末位、`lazy`、interval 3600s | 不点就不测 |

想翻那 2487 个节点就去点它；不点它就一直不消耗测速资源。

### 各源真实产出率（同一个 500 节点池）

| 源 | 可用 / 总数 | 产出率 | 占测速时间 |
|---|---|---|---|
| ermaozi | 6 / 19 | **31.6%** | 4% |
| peasoft | 4 / 46 | 8.7% | 9% |
| mahdibland | 10 / 201 | 5.0% | 40% |
| free18 | 2 / 221 | **0.9%** | 44% |
| ripaojiedian | 0 / 13 | **0%** | 3% |

**节点数跟可用数基本无关**：free18 拿 221 个换 2 个，ermaozi 拿 19 个换 6 个。
这就是"不值得精筛"的量化理由。

另外实测两件事，都推翻了原设想：
- **缩小 `timeout` 不能提速**。5s→3s 只从 105.7s 变成 103.6s（噪声内）。瓶颈是内核测速并发数，不是超时等待。
- **6 个源全部能出海**，各自出口 IP 不同，说明合并逻辑正确、没有静默回落到直连。

## 自测命令（改完配置后跑一遍）

```bash
python bench_cold.py     # 冷启动测速计时 + 各源产出率
python e2e_fixed.py      # 逐组实测出口 IP，确认真的出海
python probe_fixed.py    # 6 个源在国内的可达性（raw / gh-proxy / ghfast 三入口对比）
```

---
---

# 以下为历史方案（可选，非主线）

原"每天精筛 + 中国视角复核"流水线，代码都还在，随时可手动跑。日常不需要。

每天由 **GitHub Actions 触发**：境外抓取 + 真测速 → SSH 到腾讯云广州做**中国视角**复核
→ 生成配置 → 自动 commit / 推送到私有端点。
定时触发已停用（`update.yml` 里注释掉了 `schedule`），需要时手动 `gh workflow run update-subscription`。

**产物有两份，别订错：**

| 产物 | 内容 | 位置 | 用途 |
|---|---|---|---|
| `dist/config-cn.yaml` | 自建 4 + 公共免费 9~12 | 公开仓库 → gh-proxy | 分享/应急，节点少且会挂 |
| `config-all.yaml` | **付费机场 + 自建 + 公共** | 腾讯云私有端点（带 token） | **日常主用，订这个** |

合并那份里有付费机场的 uuid/密码，等于账号凭据，所以**不进公开仓库**（公开=被白嫖+机场封号）。

## 一句话结论

公开免费节点几乎全是废的；而且**"在美国能连"和"在中国能连"是两件事**。
本项目把两件事拆成两层做，最终成品 `dist/config-cn.yaml` 的公共节点命中率从 6% 提到 **58~78%**。

## 实测数据链（2026-10-27，同一批源）

| 环节 | 数量 | 说明 |
|---|---|---|
| 6 个源解析出节点 | 3143 | clash-yaml + v2ray 链接 |
| 跨源去重 | 2885 | |
| TCP 可连（美国 runner） | 1605 / 2885 = 55.6% | 端口开着而已 |
| 真能代理出海（mihomo 实测） | 100 / 1605 = 6.2% | 唯一可信判据 |
| 其中**中国视角** TCP 可达 | 9~12 / 100 | 腾讯云广州实测 |
| 这 9 个在中国宽带代理层真能通 | **7 / 9 = 78%** | 本机复测，692~1512ms |

**关键对比**：不做中国视角这一层，直接把美国筛出的 100 个丢给国内用 → 只有 6 个真能用（6%）；
加了这一层，9 个里 7 个能用（78%）。**精度提升 13 倍**，而代价只是一次 SSH。

## 付费机场这一层（2026-10-27 新增）

机场订阅**按 User-Agent 分流**，这是最容易踩的坑：

| UA | 返回内容 |
|---|---|
| `Mozilla/5.0` / `okhttp` | 8 条：3 条广告（名字就是"官网: xxx.com"）+ 5 条假 IP `110.110.110.110` |
| `mihomo/1.19.33`、`v2rayN` | base64，54 条真节点 |
| `clash.meta` | Clash YAML，54 条（vless 25 / tuic 24 / hysteria2 4 / ss 1） |

所以 `AIRPORT_SUBS` 里必须写客户端 UA，用浏览器 UA 会拿到诱饵节点。

**云服务器能测付费节点的代理层**（和免费节点相反）：
同一台腾讯云，付费节点 **33/53 通（336~553ms）**，免费节点 **0/7**。
差别不在网络层，在应用层——免费节点普遍封云厂商 IP，付费机场不会。

**协议差异是家宽的关键变量**（同一批 32 个机场节点，本机实测）：

| 协议 | 家宽可用率 | 原因 |
|---|---|---|
| vless（TCP） | 12/17 = **71%** | 稳定 |
| tuic / hysteria2（QUIC/UDP） | 4/15 = **27%** | 家宽/运营商对 UDP 有 QoS 甚至丢弃；**云上 UDP 干净，所以云测反而正常** |

所以机场组的排序规则是 **TCP 类优先 → 同级别内按中国实测延迟**，
再配 `fallback`（节点失败立刻换下一个），而不是 `url-test`（等下一轮测速，最多 90s 黑洞）。

## 两端视角差异（必须知道）

| 视角 | 可用数 | 延迟 |
|---|---|---|
| Actions runner（美国 Azure） | 100 / 2885（3.5%） | 34~60ms |
| 中国宽带复测同一份 | 6 / 100（6%） | 239~1254ms |

同一个节点，美国测 40ms、国内测 900ms 是常态（跨境链路 + QoS），
**节点名里的延迟数字要看是哪一端测的**——本项目 `config-cn.yaml` 里公共节点不带延迟数字，
就是因为云服务器测不了免费节点的代理层（见下）。

## 为什么中国视角要用云服务器，而不是在本机跑

用户明确要求：**不要本地计划任务，要 GitHub 上自动触发**。所以中国视角这一步由
Actions 通过 SSH 驱动腾讯云轻量（`111.229.4.201`，出口 China/Shanghai）完成。

代价是一个已实测的限制：**免费节点普遍封云厂商 IP**。
同一批节点，TCP 层两边完全一致（7/100 = 7/100），但代理层
云上 **0/7**、家庭宽带 **6/7** —— 云 IP 在应用层被拒。
所以云机器对**免费节点**只判 TCP 可达性（这一层已经能砍掉 91% 的中国不可达节点），
代理层的最终裁决交给客户端的 `url-test`（FlClash 在真实网络里自己测，失败自动跳下一个）。
**付费机场节点则可以直接在云上测代理层**，所以走的是真测速+排序。

> 想在本机做代理层复核：`python refine.py`（手动工具，不是定时任务），会把 `dist/config-cn.yaml`
> 换成中国宽带实测过的版本。日常不需要跑。
> 端到端验收：`python e2e_check.py`（起内核 → 真发出海请求 → 打印出口 IP 与分组选择）。

## 架构

```
GitHub Actions（美国）   update.py
   抓取 6 源 → 解析 → 去重 → TCP 预筛 → mihomo 真测速 → dist/config.yaml（候选池 100）
        │
        │  SSH（secrets: SSH_KEY / SSH_HOST / SSH_USER）
        ▼
腾讯云广州 111.229.4.201（China/Shanghai）   remote_refine.py
   ├─ 免费节点 TCP_ONLY=1 → 中国 IP 可达性（9~12/100）
   └─ 付费机场节点 全量代理层测速 → 延迟（27~33/53）
        │
        ▼
   ci_refine.py → dist/config-cn.yaml（自建 + 公共，公开）
   merge.py     → dist/config-all.yaml（机场 + 自建 + 公共，私有）
                    │
                    │ SCP
                    ▼
       腾讯云 /opt/subhub/www/config-all.yaml
       serve.py  →  http://111.229.4.201:18080/s/<token>.yaml
        │
        ▼
   自动 commit（公开部分）→ jsDelivr/gh-proxy 刷新 → FlClash 订阅
```

| 文件 | 作用 |
|---|---|
| `update.py` | 主流水线：抓取/解析/去重/测速/生成配置（`build_config` 支持机场层） |
| `ci_refine.py` | Actions 内运行：传脚本到云机、SSH 执行免费节点的 TCP 预筛、生成 `config-cn.yaml` |
| `merge.py` | 合成合并版：机场（中国视角测速+排序）+ 自建 + 公共 → `config-all.yaml` → SCP |
| `remote_refine.py` | 在云机上运行：`TCP_ONLY=1` 只做 TCP 预筛；否则做完整代理层测速 |
| `serve.py` | 云机上的私有订阅端点（18080），token 路径，404 兜底，不记日志 |
| `refine.py` | 手动工具：本机（中国宽带）做代理层复核 |
| `e2e_check.py` | 手动验收：起内核发真流量，打印出口 IP 和分组选择 |
| `counts.py` | 给 CI 拼提交信息（`US 100 / CN 9`） |
| `push_api.py` | 应急提交通道：走 REST API，不依赖代理（FlClash 节点死掉时 `git push` 会握手失败） |
| `sources.txt` | 免费源清单（当前 6 个活源） |
| `airports.txt` | ~~机场源~~ → 改用 secret `AIRPORT_SUBS` / 本地 `airports.local.txt`（已 gitignore） |
| `builtin.yaml` | 自建 4 个 VLESS+WS+TLS 节点，永远排分组最前 |

**保护机制**：中国视角零可用时**不覆盖** `config-cn.yaml`（保留上一版），避免把订阅清空；
美国侧可用 < `MIN_OK`(5) 时不覆盖 `config.yaml`；机场节点可用率过低时**不剔除**、只排序。

## 部署（已完成的步骤，重做时照抄）

```bash
gh repo create proxy-sub --public --source=. --push
gh api -X PUT repos/<用户>/proxy-sub/actions/permissions/workflow \
       -f default_workflow_permissions=write      # 否则 git push 403
gh secret set SSH_KEY  < ~/.ssh/zt_cloud_ed25519
gh secret set SSH_HOST --body 111.229.4.201
gh secret set SSH_USER --body root
gh secret set AIRPORT_SUBS < airports.local.txt   # 格式: 标签|订阅地址|clash.meta
gh workflow run update-subscription
```

云机一次性准备：
```bash
mkdir -p /opt/subhub/www && cd /opt/subhub
# mihomo linux-amd64 v1.19.33 放到 /opt/subhub/mihomo
apt-get install -y python3-yaml
# 私有端点
scp serve.py root@host:/opt/subhub/
python3 -c "import secrets;print(secrets.token_hex(16))" > /opt/subhub/serve.token
chmod 600 /opt/subhub/serve.token
cat > /etc/systemd/system/subhub-serve.service <<'EOF'
[Unit]
Description=subhub private subscription endpoint
After=network-online.target
[Service]
Type=simple
WorkingDirectory=/opt/subhub
ExecStart=/usr/bin/python3 /opt/subhub/serve.py
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now subhub-serve
```

> 端口 18080 在腾讯云安全组里本来就可通（实测从国内直连 30~60ms）。
> 不用 80：大陆机房未备案的 80/443 HTTP 有被拦的风险，非标端口更省事。

定时：北京时间每天 **04:17**（`17 20 * * *` UTC）。整点 GitHub 容易排队，所以错开 17 分；
实际触发常延迟 5~30 分钟，属正常。改脚本/源清单也会自动触发一次。

## FlClash 接入

**日常主用（合并版，含付费机场）**：

```
http://111.229.4.201:18080/s/<32位token>.yaml
```
国内直连 0.17s、无 CDN 缓存、每次 CI 跑完自动更新。
token 在云机 `/opt/subhub/serve.token`，路径不对一律 404。

**备用/分享（免费版）**：

```
https://gh-proxy.com/https://raw.githubusercontent.com/yjj79819/proxy-sub/main/dist/config-cn.yaml
```

企业 CDN 版本稳定但会滞后（jsDelivr 的 `@main` 分支别名有约 12h 缓存，
**调 `purge.jsdelivr.net` 返回 `CF:true FY:true` 也刷不掉**，等 10 分钟仍是旧版本；
同一时刻 `@<commit-sha>` 立刻返回新内容 —— 是分支别名解析缓存，不是内容缓存）：

```
https://cdn.jsdelivr.net/gh/yjj79819/proxy-sub@main/dist/config-cn.yaml
```

`raw.githubusercontent.com` 本身在国内 20s 后 `ConnectionReset`，所以免费版走 gh-proxy 直通。
实测三个国内可达入口（2026-10-27）：

| 地址 | 延迟 | 新鲜度 |
|---|---|---|
| `111.229.4.201:18080`（合并版，私有） | **0.17s** | 最新 ✓ |
| `gh-proxy.com` + raw（免费版） | 1.0s | 最新 ✓ |
| `ghfast.top` + raw | 4.7s | 最新 ✓ |
| jsDelivr `@main` | 0.9s | 滞后 ≤12h |

镜像都是社区服务，随时可能失效；失效就换成上表另一行。

分组（合并版）：

| 分组 | 类型 | 作用 |
|---|---|---|
| `✈️ 机场` | fallback | 付费节点，按中国实测延迟 + TCP 优先排序；主用 |
| `🏠 自建` | url-test | 4 个 legaga 自建，出口美国 CF |
| `♻️ 公共自动` | url-test | 免费节点，兜底 |
| `🎯 手动选择` | select | 手动指定，末位有 DIRECT |
| `🚀 境外代理` | fallback | 规则命中后走这里（顺序即优先级） |

节点命名：机场 `✈️星链 LAX-洛杉矶3 640ms`（延迟是中国云侧实测量，不是美国 runner 测的）、
自建 `🏠 自建 fast3`、公共 `PUB-001`。
**每天更新后名称会变**，别长期锁定单个节点，用分组（`✈️ 机场` / `🚀 境外代理`）。

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
16. **机场订阅按 User-Agent 分流，浏览器 UA 拿到的是诱饵**。同一 URL：`Mozilla/5.0` 返回
    8 条（3 条名字是"官网: xxx.com"的广告 + 假 IP `110.110.110.110`），`clash.meta` 返回 54 条真节点。
    我第一轮就是因为用浏览器 UA 抓，误判成"这订阅是钓鱼"。
17. **家宽对 UDP 类协议（tuic / hysteria2）不友好，云上却测不出这个问题**。同一批 32 个机场节点：
    vless 12/17=71%，tuic 4/15=27%。云服务器 UDP 干净，所以在云上测 tuic 一切正常 ——
    "云上测通、家里不通"多半是协议而不是节点的问题。排序必须 TCP 类优先。
18. **临时校验目录别用会被 git 忽略不掉的路径**。`_vt/config.yaml` 不在 `_*.yaml` 的匹配范围里
    （那只匹配当前目录），结果把含付费节点凭据的配置提交进了公开仓库，只能改写历史强推补救。
    现在 `.gitignore` 有 `_vt/` 和 `_*/`，双保险。
19. **`pkill -f "http.server 18080"` 会把自己那条 SSH 命令一起杀掉** —— 因为远端 `bash -c` 的
    命令行里也含这个字符串，`-f` 是全命令行匹配。按端口杀用 `fuser -k 18080/tcp`。

## 安全警告

公开免费节点的流量**不保证私密**。运营者能看到你的访问目标，部分节点会注入广告或劫持 DNS。

- **不要**用公共节点登录网银、券商、公司邮箱
- 本配置已把所有行情/交易域名强制直连，交易链路不经过公共节点
- 敏感操作请切到 `🏠 自建` 分组
- 公共节点随时会死，本项目的价值在于"每天帮你重新挑一批还活着的"，不是"给你稳定节点"
