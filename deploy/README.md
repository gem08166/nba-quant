# 云主机常驻部署说明（nba-quant）

本目录是 Linux 云主机上的常驻部署脚本，跑的是**完整 API 模式**：可以记账、结算、看盘口快照，写操作全部可用。

云主机同时是**唯一数据源**：算完的静态站会推到 GitHub Pages（`gem08166.github.io/nba-quant`），GitHub Actions 里原有的两套计算流水线已停用，不再重复计算、不再抢数据。

## 架构

```
cron（每 2 分钟） ──> deploy/watchdog.sh ──> 服务挂了就 deploy/nbaq start
cron（每 30 分钟）──> deploy/refresh.sh live ── watch --once：抓价 + 收盘快照 + 结算 + 预测
                                                  └─> deploy/publish.sh（gh-pages 单 commit force push）
cron（每天 10:30/18:30）──> deploy/refresh.sh full ── 全量 ingest + 第二书源 + 训练 + 预测 + 结算
                                                  └─> deploy/publish.sh --with-db（同时把 nba.db 快照推 data 分支）
uvicorn 常驻 0.0.0.0:8000（SQLite: data/nba.db）
GitHub Pages（legacy build）← gh-pages 分支 ← 云主机推送
```

- 进程守护：`serve.pid` + 端口健康检查（`/api/health`），连续失败自动重拉。
- 刷新互斥：`flock` 保证定时任务不会叠加跑，避免 SQLite 写冲突。
- 日志：`data/log/serve.log`、`refresh.log`、`publish.log`、`watchdog.log`。

## 常用命令

```bash
deploy/nbaq start        # 启动
deploy/nbaq stop         # 停止
deploy/nbaq restart      # 重启
deploy/nbaq status       # 看进程 + 健康检查
deploy/nbaq logs 100     # 看最近 100 行日志

deploy/refresh.sh full   # 手动全量刷新
deploy/refresh.sh live   # 手动轻量刷新
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
- 如果长期 24/7 跑，这种容器不合适：换一台有 systemd 的 VPS，把 `deploy/nbaq start` 写成 systemd unit，开机自启 + `Restart=always` 由 init 保证

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

## 发布到 GitHub Pages

线上 `https://gem08166.github.io/nba-quant/` 的内容现在由云主机推送，不再是 Actions 算出来的。

| 项 | 状态 |
|---|---|
| Pages 发布源 | `gh-pages` 分支（legacy build，根目录） |
| `gh-pages` 内容 | `site/` 导出产物，每次 force-push 单 commit，历史不增长 |
| 发布频率 | 每轮刷新后自动发布（每 30 分钟一次；全量轮附带 DB 快照） |
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
| 数据不更新 | `tail data/log/refresh.log`；多半是数据源 403/429（好体育、Hoops 限流），ESPN 仍可用 |
| 进程反复重启 | 看 `data/log/watchdog.log` 与 `serve.log`，通常是端口占用或数据库被锁 |
| 端口冲突 | 改 `.env` 的 `NBAQ_PORT`，再 `deploy/nbaq restart` |
| Pages 没更新 | `tail data/log/publish.log`；403 = token 权限不足，`gh-pages` 有没有新 commit 也可用 API 查 |
| Pages 更新了但手机还是旧数据 | CDN `max-age=600`，等 10 分钟或强制刷新 |
