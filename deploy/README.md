# 云主机常驻部署说明（nba-quant）

本目录是 Linux 云主机上的常驻部署脚本，跑的是**完整 API 模式**：可以记账、结算、看盘口快照，写操作全部可用。

云主机同时是**唯一数据源**：算完的静态站会推到 GitHub Pages（`gem08166.github.io/nba-quant`），GitHub Actions 里原有的两套计算流水线已停用，不再重复计算、不再抢数据。

## 架构

三个常驻进程，由 `deploy/nbaq` 统一管：

```
guard（每 60s 巡检，自带调度）
  ├─ serve 挂了？watch 挂了？──> deploy/nbaq start 拉起
  ├─ 每 30 分钟 ──> deploy/publish.sh（gh-pages 单 commit force push）
  └─ 每 12 小时 ──> deploy/refresh.sh full（全量 ingest + 第二书源 + 训练 + 预测 + 结算）
                                                └─> publish.sh --with-db（nba.db 快照推 data 分支）

watch（常驻刷新循环）
  └─ 默认 300s 一轮；检测到有比赛进行中自动切 120s
     每轮：ingest（比分/状态/盘口/伤停）+ 收盘快照 + 结算 + 重算预测 + 推送 bettable 信号

serve（uvicorn 0.0.0.0:8000，SQLite data/nba.db）

GitHub Pages（legacy build）← gh-pages 分支 ← 云主机推送
```

cron 只留两条兜底（`@reboot` 启动、每 2 分钟看门狗），真正的调度在 `guard` 里——因为这台容器上 cron 守护进程本身会被回收。

- 进程守护：`nbaq status` 同时校验 serve（pid + `/api/health`）和 watch/guard（pid）。
- 刷新互斥：`flock` 保证发布、刷新不会叠加跑，避免 SQLite 写冲突。
- 日志：`data/log/serve.log`、`guard.log`、`refresh.log`、`publish.log`、`watchdog.log`，以及 `data/watch.log`（watch 每轮明细，超 10MB 自动滚动）。

## 常用命令

```bash
deploy/nbaq start            # 启动 guard + watch + serve
deploy/nbaq stop             # 停三个（先停 guard，避免它把别人拉起来）
deploy/nbaq restart          # 全重启
deploy/nbaq status           # 三个进程 + 健康检查 + 最近一轮 watch 日志
deploy/nbaq restart watch    # 只重启 watch（改了预测逻辑后用这个）
deploy/nbaq logs watch 200   # 看 watch 最近 200 行
deploy/nbaq logs guard       # 看调度器日志

deploy/refresh.sh full       # 手动全量刷新（含训练）
deploy/refresh.sh live       # 手动跑一轮 watch --once
deploy/publish.sh            # 手动发布静态站
```

## 安装 / 重装

```bash
bash deploy/install.sh
```

幂等：依赖、数据库、定时任务、服务启动都会被检查后补齐。数据库缺失时会自动从 `data` 分支恢复快照。

> 机器重启后 cron 守护进程不一定自启（容器里没有 systemd），重跑一次 `sudo service cron start` 或 `bash deploy/install.sh` 即可。

## 机器被回收后的恢复（重要）

这台机器是容器环境，没有 systemd，实测过：**VM 被回收重启后，cron 守护进程不启动、`/var/spool/cron/crontabs/` 会被清空、服务进程也没了**。三条一起消失，看门狗自然也不会干活。

一条命令恢复：

```bash
sudo -n service cron start && bash deploy/install.sh
```

- `install.sh` 幂等，会重写 crontab（含 `@reboot`），cron 下次启动时 `@reboot` 条目会把服务拉起来
- 恢复后建议 `deploy/nbaq status` 确认，和 `kill -9 $(cat data/run/serve.pid)` 实测自愈
- 如果长期 24/7 跑，这种容器不合适：换一台有 systemd 的 VPS，用下一节的 `deploy/systemd/install-units.sh` 交给 init 托管

## 访问方式

1. **本机/内网**：`http://<云主机IP>:8000`
2. **公网安全访问**：不要直接把 8000 端口暴露出去，用隧道：
   ```bash
   # Cloudflare Tunnel
   cloudflared tunnel --url http://127.0.0.1:8000
   # 或 Tailscale
   tailscale serve --bg 8000
   ```
   暴露到公网前，务必在 `.env` 里设置 `NBAQ_TOKEN`，然后以 `http://地址/?token=<值>` 访问。API 支持 `Authorization: Bearer <token>` 或 `?token=`。
3. 手机装 PWA 需要 HTTPS（Tailscale / Cloudflare 提供的地址满足）。

## 数据源说明

`.env` 里的两个第二书源 key 已配置：

- `NBAQ_ODDS_API_KEY`（The Odds API）：实测有效，一次拉 13 场 / 478 行，额度剩 409。共识书源由此恢复到 **9 家**（DraftKings、FanDuel、BetMGM、BetRivers、BetOnline、BetUS、Bovada、LowVig、MyBookie）。
- `HOOPS_API_KEY`：已写入 `.env`，但当前处于 429 退避（`data/hoops_state.json` 的 `rate_limited_until` = 2026-11-07 08:00 北京时间），到点后 cron 的下一轮刷新会自动接管，无需手动干预。

注意额度：The Odds API 免费层约 500 次/月，代码默认 20 小时才拉一次（`NBAQ_ODDSAPI_MIN_HOURS`），正常一天 1-2 次，够用。临时验证时可用 `NBAQ_ODDSAPI_MIN_HOURS=0` 覆盖，但别长期设为 0。

## 搬到有 systemd 的 VPS（推荐长期跑）

本容器的 cron 和进程都会被平台回收，`guard` 只是补救。真正常驻建议换一台有 systemd 的机器，由 init 保证进程：

```bash
git clone https://github.com/gem08166/nba-quant.git
cd nba-quant
scp 旧机器:/path/nba-quant/.env .   # 或者手写一份（含 4 个密钥，见下文数据源说明）
bash deploy/install.sh          # 依赖 + 数据库 + 首次训练
sudo bash deploy/systemd/install-units.sh
```

`install-units.sh` 做的事情：

- 把 `deploy/systemd/*.service` 里的 `__ROOT__` / `__USER__` 替换成实际路径和运行用户，写入 `/etc/systemd/system/`
- 清掉旧的 nba-quant crontab 条目（避免 cron 和 systemd 抢进程）
- `daemon-reload` + `enable --now` 三个 unit

三个 unit：

| unit | 作用 | 重启策略 |
|---|---|---|
| `nba-quant-serve.service` | 面板 + API，0.0.0.0:8000 | `Restart=always`，5 秒 |
| `nba-quant-watch.service` | 常驻刷新循环，300s / 赛中 120s | `Restart=always`，10 秒 |
| `nba-quant-guard.service` | 调度发布与全量训练（`NBAQ_GUARD_HEALTH=0`，健康交给 systemd） | `Restart=always`，10 秒 |

验证与排障：

```bash
systemctl status nba-quant-serve nba-quant-watch nba-quant-guard
journalctl -u nba-quant-watch -f      # 实时看刷新
journalctl -u nba-quant-guard -n 50   # 看发布/训练记录
```

> unit 文件已在本地用 `systemd-analyze verify` 校验通过（无告警）。

## 告警与日报（推送到手机）

`guard` 自带两条推送，走 `.env` 里的 `NBAQ_NTFY_TOPIC`，不依赖任何外部定时服务：

| 触发 | 内容 | 节流 |
|---|---|---|
| 三进程拉不起来 | 「nba-quant 服务拉不起来」+ 指向 guard.log / serve.log | 1 小时一次 |
| 发布失败（rc≠0） | 常见原因是 `GH_TOKEN` 失效（403） | 1 小时一次 |
| 全量刷新失败（rc≠0） | 指向 refresh.log | 1 小时一次 |
| 每日简报 | 三进程状态、线上 `generated_at`、Odds API 剩余额度 | 每天 1 条 |

开关：`NBAQ_DIGEST=0` 关掉日报；`NBAQ_DIGEST_HOUR=20` 改发送时间（默认 20 点后第一次巡检时发）。

> 为什么不用平台定时任务：当前套餐只允许 1 个定时任务，名额已被「每日股市新闻早报」占用。告警做成机器内置，免费且不受额度限制。

## 发布到 GitHub Pages

线上 `https://gem08166.github.io/nba-quant/` 的内容现在由云主机推送，不再是 Actions 算出来的。

| 项 | 状态 |
|---|---|
| Pages 发布源 | `gh-pages` 分支（legacy build，根目录） |
| `gh-pages` 内容 | `site/` 导出产物，每次 force-push 单 commit，历史不增长 |
| 发布频率 | guard 每 30 分钟一次（全量刷新后额外带 DB 快照）；Pages 构建上限 10 次/小时，2 次/小时安全 |
| `data` 分支 | 云主机推送的 `nba.db` 快照，供灾备/回滚 |
| 已停用的 Actions | `refresh-and-deploy`、`odds-refresh-light`（state=disabled_manually） |

凭据：`.env` 里的 `GH_TOKEN`（classic PAT，需 `repo` + `workflow` scope；fine-grained token 必须显式给 Contents: write，否则 push 会 403）。

```bash
deploy/publish.sh              # 只发静态站
deploy/publish.sh --with-db    # 静态站 + 数据库快照
```

行为：push `gh-pages` 后约 30 秒～2 分钟 Pages 自动重建；客户端受 `cache-control: max-age=600` 影响，最长 10 分钟才看到新数据。

**回滚到 Actions 方案**（不建议，会和云主机抢数据）：

```bash
# 1. 重新启用工作流
TOKEN=$(grep '^GH_TOKEN=' .env | cut -d= -f2)
curl -X PUT -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/gem08166/nba-quant/actions/workflows/406057446/enable
# 2. 把 Pages 源切回 workflow
curl -X PUT -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/gem08166/nba-quant/pages -d '{"build_type":"workflow"}'
```

## 排查

| 现象 | 处理 |
|---|---|
| 页面打不开 | `deploy/nbaq status`；再看 `data/log/serve.log` 尾部 |
| 数据不更新 | `deploy/nbaq logs watch 60`；再看 `tail data/log/refresh.log`，多半是数据源 403/429（好体育、Hoops 限流），ESPN 仍可用 |
| 三个进程都掉了 | `tail data/log/guard.log`；guard 自己挂了就 `deploy/nbaq restart guard` |
| 发布停了 | `tail data/log/guard.log`、`data/log/publish.log`；403 = token 权限/过期 |
| 进程反复重启 | 看 `data/log/watchdog.log` 与 `serve.log`，通常是端口占用或数据库被锁 |
| 端口冲突 | 改 `.env` 的 `NBAQ_PORT`，再 `deploy/nbaq restart` |
| Pages 没更新 | `tail data/log/publish.log`；403 = token 权限不足，`gh-pages` 有没有新 commit 也可用 API 查 |
| Pages 更新了但手机还是旧数据 | CDN `max-age=600`，等 10 分钟或强制刷新 |
