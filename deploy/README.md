# NBA Quant 夜间哨兵 · VPS 部署（Debian 13）

只跑**干净源**：ESPN（免费体育数据）+ The Odds API（商业赔率 API）。
333 / hao-tiyu 是博彩域名，**不上 VPS**（国内云合规审计会看 TLS SNI）。

## 0. 前置

- Debian 12/13，root 或 sudo
- 能出网访问 `site.api.espn.com` / `api.the-odds-api.com` / `dav.jianguoyun.com`
- 本机有仓库副本（`F:\量化\nba-quant-20261103`）

## 1. 系统依赖

```bash
apt update && apt install -y python3 python3-venv python3-pip
```

> Debian 13 自带 Python 3.13；`numpy>=1.26` 会解析到 2.x（支持 3.13）。

## 2. 代码

本机打包上传（GitHub 在国内 VPS 常连不上，别用 git clone）：

```powershell
# 本机（PowerShell）
cd F:\量化\nba-quant-20261103
tar -czf nbaq.tar.gz --exclude=.venv --exclude=.git --exclude=data --exclude=site --exclude=node_modules --exclude=.env .
scp nbaq.tar.gz root@<VPS_IP>:/tmp/
```

> **必须排除 `.env`**：VPS 的凭据只走 `/etc/nbaq/sentinel.env`。
> `app/config.py` 的 `load_dotenv(..., override=True)` 会让代码目录的 `.env`
> 覆盖 EnvironmentFile——把 `.env` 传上去等于把本机密钥全铺开。

```bash
# VPS
mkdir -p /opt/nba-quant && tar -xzf /tmp/nbaq.tar.gz -C /opt/nba-quant
cd /opt/nba-quant
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install httpx numpy python-dotenv tzdata
.venv/bin/python -m app.cli init          # 建库（空库）
```

## 3. 凭据（600，不进代码目录）

```bash
install -d -m 700 /etc/nbaq
cat > /etc/nbaq/sentinel.env <<'EOF'
# The Odds API
NBAQ_ODDS_API_KEY=<新 key>
# 推送（与手机同主题）
NBAQ_NTFY_TOPIC=<ntfy topic>
NBAQ_NTFY_SERVER=https://ntfy.sh
# 坚果云（哨兵导出 + 台账只读）
NBAQ_WEBDAV_URL=https://dav.jianguoyun.com/dav/
NBAQ_WEBDAV_USER=<坚果云账号>
NBAQ_WEBDAV_PASS=<坚果云应用密码>
# 时区口径
TZ=Asia/Shanghai
EOF
chmod 600 /etc/nbaq/sentinel.env
```

> 环境变量名以 `app/config.py` 实际读取的为准（部署时逐项核对，不要照抄猜名）。

## 4. systemd

```bash
cp /opt/nba-quant/deploy/nbaq-sentinel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nbaq-sentinel
journalctl -u nbaq-sentinel -f
```

自检：

```bash
cd /opt/nba-quant
.venv/bin/python -m scripts.sentinel --once --export-file /tmp/sentinel.json
head -c 300 /tmp/sentinel.json
```

## 5. 本机侧

watch 启动时自动 `pull_recent()`（最近 3 天，幂等合并）。也可手动：

```powershell
python -m app.cli sentinel-pull --days 3
```

## 6. 边界（重要）

| 项 | 规则 |
|---|---|
| 333 / hao-tiyu | **绝不**在 VPS 访问 |
| 下单 | 永远在本机（凭据、住宅 IP、优惠金流程不变） |
| CL 收盘会话 | 留本机（比赛日人醒着） |
| 数据方向 | VPS → 坚果云 → 本机（本机不反向推） |
| 导出频率 | 每小时 1 次（约 450KB，坚果云流量可控） |
| 内存 | 哨兵峰值 <200MB；systemd 设 MemoryMax=700M |
