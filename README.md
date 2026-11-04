# NBA Quant（个人赛前研究版）

一个**个人研究用**的 NBA 赛前量化工具：

- 免费数据源：ESPN 记分牌 / 摘要（比分、盘口、伤停、PBP）+ SBR 历史收盘线 + 好体育 / HoopsAPI / The Odds API 第二书源
- 模型：Elo + 调整后分差模型 + 球队得分环境模型 + 球员 RAPM（v1）
- 输出：预测分差、预测总分、主胜概率、让分/大小分/胜负的模型概率、公平概率、Edge、EV、凯利仓位
- 前端：移动端优先的 PWA（安卓浏览器可“添加到主屏幕”，后续可打包 APK）
- 存储：SQLite（单机、零运维；云端由 `data` 分支持久化）
- 回测：按月 walk-forward，输出 MAE / Brier / 方向命中率；另有收盘线基准与价格/线移动 CLV

> 本项目只做数据分析与研究，不提供下注执行、资金通道或收益承诺。请遵守所在地法律与数据源条款。

---

## 0. 验证结论（2026-11 更新，先看这里）

本项目首先是一个**研究面板**，不是选注器。用 SBR 历史收盘线做独立检验后，结论是：

| 检验 | 结果 |
|---|---|
| walk-forward 回测（841 场） | 分差 MAE **11.92** / 总分 MAE **15.31** / Brier **0.2035** |
| 冻结 holdout（943 训 → 395 考，70/30 按时间切） | 分差 MAE **12.13**（常数基线 14.46）/ 总分 MAE **15.57**（基线 16.39）/ Brier **0.1916** |
| 收盘线基准（177 场） | 分差 MAE **9.89** / 总分 MAE **14.99** |
| 模型 vs 收盘线（498 注） | 平均 edge **+6.07%**，但收盘价下注 ROI **−11.32%**，命中率 **43.15%** |
| 控制收盘线后的增量信息 | 分差模型 β=**−0.013**（t=−0.04）；总分模型 β=**−0.507**（t=−1.63） |
| 信号→线移动方向（61 信号） | 顺线比例 **30.6%**（z=**−2.33**），显著**反向** |
| 最大 edge 分位 | ROI **−27.7%**、命中率 32.8%（胜负最大分位 −42.8%） |

含义：模型本身优于“无脑预测 0 分”（MAE 11.24 vs 12.85），且冻结 holdout 确认模型稳定优于常数基线、超参过拟合不严重——但这些信息已经被收盘线定价；**控制收盘线后，模型没有可检测的增量信息**。此外，**信号→线移动检验同样未通过**（63 条信号中 35 条有线移动，顺线比例仅 31.4%，z=−2.2，显著低于 50%——模型看好的那侧，市场随后反而反向移动）。因此：

- 首页的“信号 / 最佳 EV”只应视为**模型与市场的分歧观察**，不是下注依据；
- `signal` 与 `bettable` 是两个独立标注：`bettable` 只说明价格真实可拿（非软书、共识 ≥3 家、同场不叠加），**不构成对模型优势的背书**；
- 若继续做模型，必须先回答“下注时市场还不知道什么”（伤停、临场轮换、线移动等）；
- 验收标准（固定）：①线移动顺线比例显著 >50%（`python -m app.cli linemove`）；②收盘价下注 ROI 的 95% CI 下限 > −vig；③控制收盘线后模型系数显著 > 0。任一长期不过，信号保持在观察层。

---

## 1. 快速开始（Windows PowerShell）

```powershell
cd F:\量化\nba-quant-20261103
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
| `python -m app.cli holdout --split 0.7` | 冻结 holdout 验证（前 70% 训练 → 后 30% 一次性考试） |
| `python -m scripts.blend_sweep` | MARKET_BLEND 扫描（选边质量基线冻结，每次改模型后必跑） |
| `python -m app.cli closing` | 捕获临近开赛比赛的收盘价 |
| `python -m app.cli settle` | 结算已完赛的纸面注单（利润/CLV） |
| `python -m app.cli report` | 资金 / ROI / CLV 报表 |
| `python -m app.cli watch` | 常驻循环：抓数据+收盘价+结算+推送 |
| `python -m app.cli notify-test` | 发送一条测试推送 |
| `python -m app.cli hao-odds --date ...` | 抓好体育第二盘口写入 odds_snapshots（watch 每日自动） |
| `python -m app.cli signal-backtest` | 信号级回测：历史信号的 ROI/CLV（依赖盘口快照积累） |
| `python -m app.cli linemove` | 信号→线移动方向检验（可下注闸门第 0 层验收） |
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
| 盘口 | ESPN scoreboard + 好体育 / HoopsAPI / The Odds API | 多书共识；无 key 时至少保留 ESPN 单书 |
| 伤停 | `site.api.espn.com/.../summary?event=ID` | `injuries` 字段，赛前刷新才有意义 |
| 历史赛果 | 同上，按日期循环 | 回测用 |
| 历史收盘线 | SBR（`sbr-*`，7 家书源） | 2025-10-21 起回填；用于 CLV / 收盘线基准 |
| 实时 PBP | ESPN summary 的 play-by-play | 已接入阵容重建与 RAPM，不需要 `cdn.nba.com` |

关键现实：

- 免费源单书时有 stale line 风险；本项目用多书共识 + `last_seen_at` 新鲜度 + 离群剔除，并在新鲜书源不足 3 家时回退全量共识并标记 `stale`。
- SBR 历史收盘线已可用，所以现在能直接做“模型 vs 收盘线”的检验，而不只是前向积累。
- 手动录入盘口仍是必要功能：看到什么盘口就录什么，系统按同一套模型重算。

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
- 让分覆盖（整数线考虑走盘）：`P(cover) = P(margin > -line) + 0.5·P(margin = -line)`
- 大小分（整数线考虑走盘）：`P(over) = P(total > line) + 0.5·P(total = line)`
- 多书共识：各书源分别去水 → 线取众数 → 剔除离群书源 → 取平均公允概率；每侧最优价保留展示，EV 用共识中位价
- 去水：两边赔率乘法归一化；缺一边时跳过该侧 edge（不再按 50/50 造信号）
- `EV = p * decimal - 1`
- `Kelly = (p * decimal - 1) / (decimal - 1)`，默认 1/4 凯利，单注上限 3%
- 信号阈值默认 `edge ≥ 5%` 且 `EV ≥ 1%`（`NBAQ_EDGE` / `NBAQ_MIN_EV`）

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
4. `价格 CLV = 下注赔率 ÷ 收盘赔率 − 1`（同市场同方向，取开赛前最近一次盘口快照）；
5. `线移动 CLV`（单位：分，正数 = 拿到了更好的线）：让分 `下注线 − 收盘线`；大球 `收盘线 − 下注线`；小球 `下注线 − 收盘线`；胜负无线，不计算；
6. `report` 或 `/api/bets/summary` 查看汇总：ROI、命中率、平均价格 CLV、平均线移动、CLV 为正的比例、分市场拆分。**PWA"收益"标签页**（`/api/bets/analysis`）：汇总统计卡 + 累计收益曲线（按比赛日）+ 分市场拆分 + 注单台账（比赛/市场/赔率/EV/结果/盈亏/价格 CLV/线移动）。

```powershell
python -m app.cli closing     # 捕获收盘价
python -m app.cli settle      # 结算
python -m app.cli report      # 报表
```

> 判断模型是否有效，主要看**价格 CLV 与线移动是否长期为正**，而不是单场输赢。

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
| `NBAQ_ODDS_API_KEY` | The Odds API 多书源（放 `.env` 文件） | 未设置=关闭 |
| `NBAQ_ODDSAPI_MIN_HOURS` | Odds API 两次拉取最小间隔（小时，省额度） | 20 |
| `NBAQ_WATCH_INTERVAL` | watch 循环间隔（秒） | 300 |
| `NBAQ_CLOSING_WINDOW` | 收盘价捕获窗口（分钟） | 10 |
| `NBAQ_EDGE` / `NBAQ_MIN_EV` | 信号阈值 | **5%** / 1% |
| `NBAQ_STALE_HOURS` | 盘口新鲜度阈值（小时） | 7 |
| `NBAQ_MIN_FRESH_BOOKS` | 少于此数量的新鲜书源时回退全量共识并标记 stale | 3 |
| `NBAQ_BETTABLE_MIN_BOOKS` | bettable 闸门：共识书源数下限 | 3 |
| `NBAQ_MUTUAL_EXCLUSIVE` | 同场让分/胜负互斥（只留 EV 最高的一条可下注） | 1 |
| `NBAQ_SHARP_ANCHOR` | fair 概率锚定 sharp 书（Pinnacle 类；当前实时源未含 sharp 书，开启无效果） | 0 |
| `NBAQ_MARKET_BLEND` | 模型权重（1.0=纯模型） | 0.55 |
| `NBAQ_TOTAL_REGIME_SEASON_CARRY` | 跨季季前赛锚定比例（0=关闭；0.5=保守，验收最优 1.0） | 0.5 |
| `NBAQ_TOTAL_REGIME_SEASON_MIN_BLEND` | 季前赛小样本收缩下限（n=10 → 0.5，n≥30 → 1.0） | 0.5 |
| `NBAQ_TOKEN` | API 访问令牌 | 未设置=关闭 |

---

## 9. 安卓端

### 方案 A+：GitHub Pages 静态 PWA（真·无后端，推荐）

电脑可关机。GitHub Actions 每天两次自动跑完整管线（抓取 → 第二书源盘口 → 训练 → 预测 → 结算 → 回测 → 导出静态站），并把**同一个 run** 的 `site/` 产物部署到 GitHub Pages；**赛时页由手机直连 ESPN**（CORS 已验证），投影数学在前端运行。

> 部署修复：旧 workflow 的 deploy job 会重新 `checkout` 触发 SHA，导致永远发布上一轮数据。现在 refresh job 直接上传 Pages artifact，deploy job 只负责发布同一 run 的 artifact。

> 数据库不在 `main`：`data/nba.db` 由 Actions 持久化到独立的 `data` 分支（每次覆盖为单 commit 快照），主仓只保留静态站和抓取状态，避免仓库随赛季膨胀。

一次性部署：

1. GitHub 建仓库，`git remote add origin git@github.com:<你>/nba-quant.git && git push -u origin main`
2. 仓库 Settings → Secrets and variables → Actions 添加 `NBAQ_ODDS_API_KEY`、`HOOPS_API_KEY`（.env 里的值）
3. Settings → Pages → Build and deployment → Source 选 **GitHub Actions**
4. Actions 页手动 Run 一次 "refresh-and-deploy" 验证
5. 手机访问 `https://<你>.github.io/<仓库名>/` → Chrome"添加到主屏幕"（HTTPS，可安装 PWA）

### 从旧版本迁移（SQLite 移出 Git）

首次升级时手动建一次 `data` 分支（之后 Actions 自动维护）：

```bash
# 确认数据库没有未落盘的 WAL
python -c "import sqlite3; c=sqlite3.connect('data/nba.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"

# 建只含 nba.db 的孤儿分支
git checkout --orphan data
git rm -rf --cached .
git add -f data/nba.db
git commit -m "db: initial snapshot"
git push -u origin data

# 回到 main，停止跟踪数据库
git checkout main
git rm --cached data/nba.db
git add .gitignore .github app scripts tests README.md LICENSE CONTEXT.md
git commit -m "chore: data branch + deploy/freshness/PWA/CLV fixes"
git push
```

行为说明：页面自动探测 `data/static.json`——存在则进入**静态模式**（顶部会显示，写操作隐藏只读）；本地 `serve` 时自动走 API 模式，同一套前端。静态模式下"记一笔"等写操作不可用，记账在本地 API 模式补录。

### 方案 A：PWA + 本地后端（功能完整，需电脑开机）

1. 电脑运行 `python -m app.cli serve`；
2. 手机与电脑同一 Wi-Fi，访问 `http://<电脑局域网IP>:8000`；
3. Chrome 菜单 →"添加到主屏幕"。

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

1. **模型没有可检测的增量信息**：控制收盘线后系数不显著，线移动检验也显著反向（见第 0 节）。继续加模型复杂度不会自动改变这一点。
2. **收盘线基准仍偏单书**：`scripts/model_clv_history.py` 目前用 `sbr-fanduel` 单家收盘线；下一步改为 7 家 SBR 共识，并给 ROI/edge 加 bootstrap 置信区间。
3. **回测与生产口径未完全对齐**：回测不含伤停 impact、不含市场融合，σ 用样本内残差；下一步让回测复用生产的 `build_market`/`consensus_market`。
4. **无球员盘**：上场时间模型 + 负二项分布是下一阶段。
5. **RAPM 只做建议**：`injury_suggestions` 不会自动改写预测，避免小样本噪声直接污染定价。
6. **数据源脆弱**：SBR 是网页抓取、好体育是逆向 checksum 多镜像，接口一改就可能断，且存在 ToS 风险。
7. **时区近似**：`dates.py` 固定 UTC−5，夏令时期间有 1 小时偏移（影响很小，注释已承认）。
8. **个别历史比赛无结果**：少数延期/取消的比赛会一直挂在“未开赛”，每次 predict 会生成预测，无实际影响。

建议的迭代顺序：

- 先修数据与部署的可信度（已完成）：同 run 部署、盘口时序、`last_seen_at` 新鲜度、PWA SW、CI 测试。
- 可下注闸门体系（已完成，等数据）：bettable 三闸门 + 线移动验收。实时快照积累 2-4 周后重跑 `linemove`，z 仍 <0 则"照信号下注"方向正式关闭。
- 评估加固（下一步）：7 家 SBR 共识收盘线、bootstrap CI、回测对齐生产口径。
- 模型层的前提：伤停/临场轮换/线移动等"下注时市场还不知道"的信息，且要先通过线移动闸门。
- 最后：球员盘（props 是散户模型最可能的 niche）、赛时状态机、更多推送渠道。

---

## 12. 免责声明

本项目仅用于个人数据分析、建模与回测研究，不构成任何投注建议，不提供下注、代购、资金结算或支付通道。体育投注在不同司法辖区法律地位不同；请自行确认并遵守当地法律及数据源服务条款。模型输出可能存在错误，使用风险自负。

---

## 13. License

MIT License，见 [LICENSE](LICENSE)。Copyright (c) 2026 gem08166.
