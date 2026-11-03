# NBA Quant（个人赛前研究版）

一个**个人研究用**的 NBA 赛前量化工具：

- 免费数据源：ESPN 记分牌 / 摘要（比分、盘口、伤停）
- 模型：Elo + 调整后分差模型 + 球队得分环境模型
- 输出：预测分差、预测总分、主胜概率、让分/大小分/胜负的模型概率、公平概率、Edge、EV、凯利仓位
- 前端：移动端优先的 PWA（安卓浏览器可“添加到主屏幕”，后续可打包 APK）
- 存储：SQLite（单机、零运维）
- 回测：按月 walk-forward，输出 MAE / Brier / 方向命中率

> 本项目只做数据分析与研究，不提供下注执行、资金通道或收益承诺。请遵守所在地法律与数据源条款。

---

## 1. 快速开始（Windows PowerShell）

```powershell
cd D:\nba-quant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m app.cli init
python -m app.cli backfill --start 2025-10-21 --end 2026-04-12
python -m app.cli backfill --start 2026-10-01 --end 2026-11-03
python -m app.cli refresh --date 2026-11-04
python -m app.cli serve
```

浏览器打开 `http://127.0.0.1:8000`。

> `--date` 使用**美国比赛日**（ESPN 的 `dates` 参数），不是北京时间。

### 常用命令

| 命令 | 作用 |
|---|---|
| `python -m app.cli init` | 初始化 SQLite |
| `python -m app.cli ingest --date 2026-11-04` | 拉取某天比赛、盘口、伤停 |
| `python -m app.cli backfill --start ... --end ...` | 回填历史赛果 |
| `python -m app.cli train` | 用已存赛果训练模型 |
| `python -m app.cli predict --date 2026-11-04` | 生成预测与 EV |
| `python -m app.cli refresh --date 2026-11-04` | ingest + train + predict |
| `python -m app.cli serve` | 启动 Web / PWA |
| `python -m app.cli backtest --test-start 2026-01-01` | walk-forward 回测 |
| `python -m app.cli closing` | 捕获临近开赛比赛的收盘价 |
| `python -m app.cli settle` | 结算已完赛的纸面注单（利润/CLV） |
| `python -m app.cli report` | 资金 / ROI / CLV 报表 |
| `python -m app.cli watch` | 常驻循环：抓数据+收盘价+结算+推送 |
| `python -m app.cli notify-test` | 发送一条测试推送 |
| `python -m app.cli hao-odds --date ...` | 抓好体育第二盘口写入 odds_snapshots（watch 每日自动） |
| `python -m app.cli signal-backtest` | 信号级回测：历史信号的 ROI/CLV（依赖盘口快照积累） |
| `python -m app.cli status` | 查看数据量 |

建议每天定时跑：

```powershell
python -m app.cli refresh --date 2026-11-04
```

Windows 任务计划程序 / cron 均可。更省事的方式是用 `watch`（见第 8 节），一条命令常驻完成所有事。

> 终端里中文乱码：先执行 `chcp 65001` 切到 UTF-8 即可（仅影响显示，不影响数据）。

---

## 2. 数据源与限制

| 数据 | 来源 | 说明 |
|---|---|---|
| 赛程/比分 | `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` | 免费、稳定、无需 key |
| 盘口 | ESPN scoreboard 的 `odds` 字段 | 通常只有 DraftKings 一家，部分比赛没有 |
| 伤停 | `site.api.espn.com/.../summary?event=ID` | `injuries` 字段，赛前刷新才有意义 |
| 历史赛果 | 同上，按日期循环 | 回测用 |
| 历史盘口 | 无免费稳定来源 | **从今天开始存快照**，这是最宝贵的资产 |
| 实时 PBP | `cdn.nba.com` liveData | 当前直接请求可能 403，需要浏览器头/签名；本版未接入 |

关键现实：

- 免费源只有**一家软盘**时，“Edge”很可能只是 stale line，不是真 edge。建议把阈值提高到 4%–5%，并结合线移动确认。
- 免费历史盘口基本拿不到，所以回测先看**分差/总分 MAE、Brier、校准**；CLV 需要靠前向纸面跟踪积累。
- 手动录入盘口是必要功能：看到什么盘口就录什么，系统按同一套模型重算。

---

## 3. 模型（当前版本）

### 3.1 分差模型（Massey / 加权岭回归）

对每场已结束比赛：

```text
home_margin = intercept
            + b_elo * (elo_home - elo_away)
            + b_rest * (rest_home - rest_away)
            + net_home - net_away
```

- `net_i`：球队净胜分强度（点/场）
- `elo`：按时间顺序在线更新，预测时只用赛前值，避免未来函数
- 权重：`0.5 ** (age_days / 120)`，近期比赛权重更高
- 正则：球队参数 L2 收缩，防止小样本爆炸

### 3.2 总分模型

```text
total = intercept + env_home + env_away
```

- `env_i`：球队“得分环境”（节奏 + 进攻 + 防守的综合）
- 伤停对总分做 0.5 倍调整（可自行改）

### 3.3 概率与定价

- 分差分布：`Normal(pred_margin, sigma_margin²)`
- 总分分布：`Normal(pred_total, sigma_total²)`
- 让分覆盖：`P(home cover) = 1 - Φ((floor(-line)+0.5 - μ) / σ)`
- 大小分：`P(over) = 1 - Φ((floor(line)+0.5 - μ) / σ)`
- 去水：两边赔率乘法归一化；缺一边时按 50/50
- `EV = p * decimal - 1`
- `Kelly = (p * decimal - 1) / (decimal - 1)`，默认 1/4 凯利，单注上限 3%

`sigma` 有下限（分差 10.5、总分 13.5），防止模型过度自信。请用回测校准。

### 3.4 球员 RAPM（v1，2026-11 新增）

ESPN summary 自带完整 play-by-play（含换人事件），不需要 `cdn.nba.com`（403）。管线：

1. 抓 PBP（断点续跑）：`python -m scripts.backfill_pbp --start 2026-10-20 --end 2026-11-02`
2. 训练：`python -m app.cli rapm-train`（换人重建场上阵容 → 每个 stint 的分差/回合数 → 加权岭回归）
3. 输出：单数字 RAPM（净分 / 100 回合），存 `rapm_models` 表；`GET /api/rapm` 查看
4. 应用：predict 时自动为 Out/Doubtful（×0.8）、Questionable/Day-To-Day（×0.4）球员生成 `injury_suggestions`（只建议，不改动手动 impact 的计算）

数据质量闸门：stint 分差合计与最终分差偏差 >6 分的场次直接丢弃（ESPN 偶发漏换人/漏比分事件）。已知局限：单赛季小样本、无 team fixed effects、lam=300 未做网格搜索。

### 3.5 赛时看板（v1，2026-11 新增）

PWA 第三个标签"赛时"：实时比分 + ESPN 逐 play 胜率 + 模型剩余时间投影，30 秒自动刷新。

- 数据源全免费：ESPN scoreboard（比分/时钟/节次/状态）+ summary 的 `winprobability` 曲线，不依赖被 403 的 `cdn.nba.com`
- 投影模型：`投影分差 = 当前分差 + 赛前预测分差 × 剩余时间比例`，总分同理；σ 按 √(剩余时间) 收缩，加时赛继续收缩不确定性
- 进行中的比赛还显示：ESPN 实时主胜率 + 动量箭头（近 10 个 play 胜率变化）、赛前让分/大小在当前状态下的覆盖概率、最近 6 个 play 文本流
- 排序：进行中 → 未开赛 → 已结束

局限：免费接口约 30 秒延迟；投影假设剩余得分节奏等于赛前预期（火热手感/垃圾时间会偏差）；ESPN 偶发缺 winprobability（显示为不可用）。

### 3.6 伤停

ESPN 只给状态，不给球员影响力。当前做法：

- 自动抓取 `Out / Day-To-Day` 等状态
- 在比赛详情里手动填 `impact`（该球员缺阵对分差的点数，正数表示该队损失）
- 保存后自动重算预测

这是个人研究版最实用的折中；RAPM（§3.4）现在会自动给出 `injury_suggestions` 建议。

---

## 4. 项目结构

```text
nba-quant/
├─ app/
│  ├─ api.py              # FastAPI 路由
│  ├─ cli.py              # 命令行入口
│  ├─ config.py           # 路径/阈值/环境变量
│  ├─ db.py               # SQLite schema 与连接
│  ├─ main.py             # FastAPI app + 静态/PWA
│  ├─ ingest/
│  │  ├─ espn.py          # ESPN 抓取与解析
│  │  └─ service.py       # 落库、去重
│  ├─ models/ratings.py   # Elo + 分差/总分模型
│  ├─ pricing/devig.py    # 去水/概率/EV/凯利
│  ├─ jobs/
│  │  ├─ predict.py       # 生成预测与 Edge
│  │  └─ backtest.py      # walk-forward 回测
│  └─ web/                # PWA 前端（无构建步骤）
│     ├─ index.html
│     ├─ app.js
│     ├─ styles.css
│     ├─ manifest.webmanifest
│     ├─ sw.js
│     └─ icon.svg
├─ tests/
├─ data/                  # SQLite + 原始 JSON + 回测报告
└─ pyproject.toml
```

---

## 5. HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/games?date=YYYY-MM-DD` | 当日比赛 + 最新预测 + Edge |
| GET | `/api/dates` | 有比赛的日期列表（看板空日自动跳转用） |
| GET | `/api/games/{game_id}` | 比赛详情、盘口快照、伤停 |
| GET | `/api/edges?date=...&min_ev=0.02` | 只返回正 EV 信号 |
| POST | `/api/odds` | 手动录入盘口 |
| POST | `/api/predict` | 重算某日预测 |
| POST | `/api/bets` | 记录纸面注单 |
| GET | `/api/bets` | 纸面注单列表（支持 `?game_id=` `?result=` 过滤） |
| DELETE | `/api/bets/{id}` | 撤销未结算的纸面注单 |
| GET | `/api/bets/analysis` | 收益分析：汇总+累计收益曲线+台账 |
| POST | `/api/injuries/{id}/impact` | 设置伤停影响 |
| GET | `/api/rapm` | 最新球员 RAPM 排行 |
| GET | `/api/trends` | 月度联盟得分/分差趋势 |
| GET | `/api/live?date=...` | 赛时看板：实时比分+ESPN 胜率+模型投影 |
| GET | `/api/model` | 最新模型摘要 |
| GET | `/api/backtest` | 回测报告 |

手动录入盘口示例：

```powershell
curl -X POST http://127.0.0.1:8000/api/odds `
  -H "Content-Type: application/json" `
  -d '{"game_id":"401909922","market":"spread","side":"home","line":-1.5,"price":1.91,"book":"manual"}'
```

---

## 6. 回测

```powershell
python -m app.cli backtest --test-start 2026-01-01 --test-end 2026-04-12
```

输出 `data/backtest_report.json`，并在终端打印：

- 分差 MAE / RMSE / bias
- 总分 MAE / RMSE / bias
- 主胜 Brier / 方向准确率
- 让分、大小分方向命中率（只有存到历史盘口时才有样本）
- 按月拆分

### 看什么指标

1. **MAE 是否低于“无脑预测主队 -3 / 总分 225”**；
2. **Brier 是否低于 0.25**（主胜 50/50 基线）；
3. **校准**：说 60% 的比赛是否真的赢约 60%；
4. **CLV**：纸面注单的下注价 vs 收盘价，长期为正才有意义；
5. 不要用单场输赢调参。

---

## 7. 纸面注单、自动结算与 CLV

研究闭环的关键：**信号 → 纸面注单 → 收盘价 → 结算 → CLV**。

1. 在比赛详情弹窗里对信号点“记一笔”（或 `POST /api/bets`、`scripts/seed_bet.py`）；
2. 开赛前，`closing`（或 `watch` 自动）会重抓临近开赛比赛的盘口，作为收盘价快照（默认开赛前 10 分钟窗口，`NBAQ_CLOSING_WINDOW` 可调）；
3. 赛后 `settle` 按最终比分结算注单：胜/负/走，并计算利润；
4. `CLV = 下注赔率 ÷ 收盘赔率 − 1`（同市场同方向，取开赛前最近一次盘口快照）；
5. `report` 或 `/api/bets/summary` 查看汇总：ROI、命中率、平均 CLV、CLV 为正的比例、分市场拆分。**PWA"收益"标签页**（`/api/bets/analysis`）：汇总统计卡 + 累计收益曲线（按比赛日）+ 分市场拆分 + 注单台账（比赛/市场/赔率/EV/结果/盈亏/CLV）。

```powershell
python -m app.cli closing     # 捕获收盘价
python -m app.cli settle      # 结算
python -m app.cli report      # 报表
```

> 判断模型是否有效，主要看**平均 CLV 是否长期为正**，而不是单场输赢。

---

## 8. 推送到手机（Telegram）

配置环境变量后，`refresh` / `watch` 每发现**新的**正 EV 信号就会推送（按 比赛/市场/方向/盘口/赔率 去重，不会重复打扰）：

```powershell
$env:NBAQ_TG_TOKEN="123456:ABC..."   # @BotFather 创建机器人后获得
$env:NBAQ_TG_CHAT_ID="你的chat_id"
python -m app.cli notify-test        # 验证配置
```

获取 `chat_id`：先给你的机器人随便发一条消息，然后浏览器打开
`https://api.telegram.org/bot<TOKEN>/getUpdates`，在返回 JSON 里找 `"chat":{"id": ...}`。

也支持免翻墙的 ntfy.sh：只需设置 `NBAQ_NTFY_TOPIC`（手机装 ntfy App 订阅同一 topic）。

### 常驻运行（推荐）

```powershell
python -m app.cli watch
```

一个循环做五件事：

- 每天首次运行：抓当日比赛+伤停、重训模型（自动按美国东部比赛日切换，北京时间白天对应的是前一晚的美国比赛日）
- 每次循环（默认 5 分钟，`NBAQ_WATCH_INTERVAL` 可调）：检查临近开赛的比赛 → 抓收盘价
- 自动结算已完赛注单并打印盈亏/CLV
- 重算预测，发现新信号 → 推送 Telegram
- 全程打印日志，Ctrl+C 停止；配合任务计划程序开机自启即可

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `NBAQ_TG_TOKEN` / `NBAQ_TG_CHAT_ID` | Telegram 机器人 | 未设置=关闭 |
| `NBAQ_NTFY_TOPIC` | ntfy.sh 主题 | 未设置=关闭 |
| `NBAQ_WATCH_INTERVAL` | watch 循环间隔（秒） | 300 |
| `NBAQ_CLOSING_WINDOW` | 收盘价捕获窗口（分钟） | 10 |
| `NBAQ_EDGE` / `NBAQ_MIN_EV` | 信号阈值 | 3% / 1% |
| `NBAQ_MARKET_BLEND` | 模型权重（1.0=纯模型） | 0.55 |
| `NBAQ_TOKEN` | API 访问令牌 | 未设置=关闭 |

---

## 9. 安卓端

### 方案 A：PWA（推荐，零打包）

1. 电脑运行 `python -m app.cli serve`；
2. 手机与电脑同一 Wi-Fi，访问 `http://<电脑局域网IP>:8000`；
3. Chrome 菜单 →“添加到主屏幕”。

注意：**Service Worker / 安装提示要求 HTTPS**（`localhost` 除外）。局域网 `http://192.168.x.x` 只能当普通网页用。

想要完整 PWA 体验，用 Tailscale：

```powershell
tailscale serve --bg 8000
```

然后用手机访问 Tailscale 给出的 `https://...` 地址，即可安装。

### 方案 B：Capacitor 打包 APK（后续）

前端是纯静态文件，可直接复用：

```powershell
npm create @capacitor/app@latest nbaq-android
# 把 app/web 下的文件复制到新项目的 www/
npx cap add android
npx cap open android
```

在 Android Studio 里改 API 地址（指向你的电脑/Tailscale HTTPS 地址），构建 APK 侧载即可。

### 方案 C：Termux 全本地

在安卓上装 Termux + Python，直接跑 `uvicorn`。可行但体验差：后台易被杀、依赖安装麻烦、回测性能低。不建议作为首选。

### 推送提醒（可选）

PWA 在 Android 上后台刷新不可靠。更稳的做法是服务端定时任务发现 Edge 后，通过 `ntfy.sh` 或 Telegram Bot 推送，手机只负责接收。

---

## 10. 安全与部署

- 默认绑定 `0.0.0.0:8000`，只建议在可信局域网使用。
- 设置 API Token：

```powershell
$env:NBAQ_TOKEN="your-long-token"
python -m app.cli serve
```

之后请求 `/api/*` 需要 `Authorization: Bearer your-long-token`（或 `?token=`）。

- 不要把端口直接暴露到公网；用 Tailscale / Cloudflare Tunnel + 访问控制。
- 数据源有速率限制，`backfill` 已默认 sleep 0.25s；遇到 429 请调大 `NBAQ_SLEEP`。

---

## 11. 已知限制 / 下一步

当前版本刻意保持简单，以下是明确缺口：

1. **没有实时 PBP**：赛中模型未接入；免费 `cdn.nba.com` 需要正确请求头/签名。
2. **没有 RAPM/阵容识别**：目前是球队级模型，球员缺阵靠手动 impact。
3. **只有一家盘口**：ESPN 的 DraftKings 线，不是共识线；免费额度允许时可接 The Odds API 做交叉验证。
4. **历史盘口缺失**：CLV 要从前向快照积累；`watch` 会自动抓收盘价，坚持跑一个月就有像样样本。
5. **无球员盘**：上场时间模型 + 负二项分布是下一阶段。
6. **个别历史比赛无结果**：少数延期/取消的比赛会一直挂在“未开赛”，会在每次 predict 时生成预测，无实际影响。

建议的迭代顺序：

- 第 1 周：每天定时 `refresh`，积累盘口快照；观察模型 MAE/校准。
- 第 2 周：加收盘快照任务（开赛前 1 分钟），开始记录 CLV。
- 第 3 周：球员级 RAPM（用 PBP 阵容数据）与自动伤停影响。
- 第 4 周：赛中状态机 + 实时胜率/让分/总分（需要低延迟源）。
- 之后：球员盘、自动结算、Telegram/ntfy 推送。

---

## 12. 免责声明

本项目仅用于个人数据分析、建模与回测研究，不构成任何投注建议，不提供下注、代购、资金结算或支付通道。体育投注在不同司法辖区法律地位不同；请自行确认并遵守当地法律及数据源服务条款。模型输出可能存在错误，使用风险自负。
