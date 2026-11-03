# CONTEXT.md — 项目交接文档（给今晚继续开发的你 / 或任何 AI 会话）

> 用途：把项目的全部上下文压缩在一页里。继续开发时，把本文档 + README.md 一起交给 AI 或自己阅读即可无缝接上。
> 更新时间：2026-11-03（北京时间晚上，第二次更新）

---

## 0. 环境变更（2026-11-03 晚）

- 项目已从 `D:\nba-quant` 迁到 **`F:\量化\nba-quant-20261103`**（旧盘的 venv 没带过来，本机 Python 也被清理过）
- 现用 **uv** 重建：uv.exe 在 `C:\Users\Administrator\tools\uv\uv.exe`，venv 为本目录 `.venv`（CPython 3.12.16）
- 恢复环境：`& "$env:USERPROFILE\tools\uv\uv.exe" pip install --python .venv\Scripts\python.exe -e ".[dev]"`
- 跑命令统一用 `.\.venv\Scripts\python.exe -m app.cli ...`（本机 PATH 里没有独立 Python）
- 注意：`scripts\inspect.py` 会遮蔽标准库 `inspect`，凡是要 import numpy 的临时脚本请放在仓库根目录跑

---

## 1. 项目是什么

**个人研究用 NBA 赛前量化分析工具**（注意边界）：

- 定位：个人研究 + 决策辅助 + 纸面跟踪。**不做**自动下注、不接资金、不承诺盈利（合规：大陆体育投注违法，工具只做数据分析）
- 用户三个决策（已确认）：个人研究 / 先做赛前 / 免费数据源起步 / 后续要能在安卓端用
- 数据源：ESPN 免费接口（scoreboard + summary），无 API key
- 技术栈：Python 3.12 + FastAPI + SQLite + 原生 HTML/JS PWA（无构建步骤）
- 位置：`D:\nba-quant`（venv 已建好：`.venv`，依赖已装，`pip install -e ".[dev]"`）

## 2. 当前状态（截至本文档更新）

| 项 | 状态 |
|---|---|
| 数据库 | `data/nba.db`：1427 场比赛（2025-26 全季 1239 + 2026-27 当前 174+），25 条盘口快照，1409 场已完赛；**新增 PBP：107 场本赛季 play-by-play（2026-10-20~11-02），players/starters/pbp_plays 表** |
| 模型 | 球队模型：n=1342，sigma_margin=14.50，sigma_total=19.92，**含总分 regime 修正（shift=-2.64）**；**球员 RAPM v1：107 场 3370 stints，单数字（净分/100回合），Luka +10.3 / SGA +6.7 量级合理** |
| 回测 | walk-forward 2026-01~04+10~11（841 场）：分差 MAE 11.92，总分 **MAE 15.31 / RMSE 19.15**（bias +0.76），Brier 0.2031。**已剔除全明星周末表演赛污染**（Team Stars/Stripes/World、London Lions 共 4 场，见 ratings.load_finals 的 id 过滤） |
| 功能 | 数据抓取✅ 模型✅ 预测/Edge✅ PWA 看板✅（**空比赛日自动跳到最近有比赛的日期**） 手动录盘口✅ 伤停 impact✅（**RAPM 建议一键采用**） 纸面注单✅（**记一笔已完善：内联表单可改价格/盘口、重复防抖、本场注单列表+撤销**） **每日自动备份✅（data/backup/ 滚动 7 份）** 收盘价捕获✅ 自动结算+CLV✅ **结算结果推送✅** Telegram/ntfy 推送✅ **watch 加固✅（日志落盘 data/watch.log、连续失败/空赛程告警、每日 PBP+RAPM+第二盘口）** 赛时看板✅（ESPN 实时源+剩余时间投影，未在真实 live 场次验证） 收益页✅ **第二盘口源✅（hao-tiyu 入 odds_snapshots，watch 每日抓取；观察期后可参与融合定价）** **信号级回测✅（`signal-backtest`，等快照积累）** |
| 测试 | 23 个 pytest 全过（`.\.venv\Scripts\python.exe -m pytest -q`） |
| 可视化 | PWA"研究图表"标签页（原生 canvas 零依赖）：月度 bias/MAE、联盟得分趋势、RAPM 排行、预测vs实际散点、校准曲线。新增 `/api/trends`、`app/web/research.js`。**修复了 sqlite 跨线程 bug（db.connect 加 check_same_thread=False，此前所有 API 端点都在赌线程池运气）** |
| 注单 | 1 条演示注单（OKC ML，已结算 won，CLV +3%）— 可删可留 |

## 3. 架构与文件地图

```
数据流：ESPN API → ingest(归一化+落库) → ratings(Elo+岭回归) → predict(概率+融合定价)
       → edges(EV/凯利) → PWA 看板 / Telegram 推送 → 纸面注单 → closing 收盘价 → settle 结算+CLV

app/
  config.py      # 所有阈值/路径/环境变量（先看这个）
  dates.py       # current_slate_date(): NBA比赛日=美国东部日期, 用 UTC-5 近似（北京时间白天=前一晚美国比赛日）
  db.py          # SQLite schema + connect()内置轻量迁移(_migrate)
  api.py         # FastAPI 路由（/api/games /edges /bets /bets/summary /model /backtest ...）
  main.py        # app 装配 + token 认证 + PWA 静态服务
  ingest/espn.py     # ESPN 抓取/解析（注意 dates 参数要 YYYYMMDD 无横线）；parse_plays/parse_starters(PBP)
  ingest/service.py  # 落库、盘口去重（价格没变就不存）、refresh_game_odds(收盘价用)、backfill_pbp
  models/ratings.py  # Elo(无未来函数) + Massey式分差模型 + 得分环境总分模型，加权岭回归；总分截距向近30天常规赛均分回归（regime修正）
  models/rapm.py     # 球员RAPM：ESPN PBP换人事件重建阵容→stint→加权岭回归（净分/100回合）+ 伤停impact建议
  pricing/devig.py   # 去水/cover_prob/over_prob/EV/凯利（有0.5连续性修正）
  jobs/predict.py    # 预测 + 市场融合定价 + edge 生成（跳过 POSTPONED/CANCELED；附带 injury_suggestions）
  jobs/settle.py     # 结算/CLV/收盘价捕获/报表汇总
  jobs/notify.py     # Telegram + ntfy 推送（按game/market/side/line/price去重）
  jobs/watch.py      # 常驻循环：每日抓取+训练(+PBP回填+RAPM) → 收盘价 → 结算 → 预测 → 推送
  jobs/backtest.py   # 按月 walk-forward
  jobs/live.py       # 赛时看板：ESPN scoreboard 实时状态 + winprobability + 剩余时间投影
  jobs/backup.py     # 每日数据库备份（VACUUM INTO，data/backup/ 滚动 7 份）
  jobs/signal_backtest.py  # 信号级回测：历史 edge 按当时价格入场、收盘快照算 CLV
  ingest/hao.py      # 第二盘口源（好体育亚盘站）：多镜像故障转移 + checksum 自动发现
  ingest/theodds.py  # The Odds API 多书源客户端（省额度：状态文件限流 + 剩余额度追踪）
  web/               # PWA 前端（index.html/app.js/research.js(研究图表)/live.js(赛时)/bets.js(收益)/styles.css/manifest/sw.js/icon.svg）
scripts/
  inspect.py       # 查看月度总分/分差均值 + 未完赛场次（文件名会遮蔽标准库inspect，import numpy 的脚本别放本目录直接运行）
  show_edges.py    # 打印某日预测与全部 edge（取最新一条预测）
  seed_bet.py      # 手动插注单（可模拟收盘价，用于测试结算）
  backfill_pbp.py  # 回填ESPN PBP（断点续跑）；python -m scripts.backfill_pbp --start ... --end ...
```

## 4. 关键设计决策（为什么是这样）

1. **市场融合定价 `MARKET_BLEND=0.55`**（最重要）：免费源只有 DraftKings 一家软盘，且早期赛季球队评分来自上赛季、对阵容变化不敏感。实测纯模型会让 PHI@DET 出现"模型+9.8 vs 盘口-1.5"的 8 分假分歧。融合后 edge 缩水但可信。**若要做纯模型研究，`NBAQ_MARKET_BLEND=1.0`**
2. **σ 下限**：margin 10.5 / total 13.5（config），防过度自信。实测模型原始 sigma 14.5/19.7，高于经验值 11-13/14-16，回测 RMSE 15.07/22.02 大致自洽
3. **比赛日 = 美国东部日期**：ESPN `dates=YYYYMMDD`（无横线！）按美国日期返回该日 19:00-22:30 ET 的比赛（UTC 时间是次日 00:00Z-03:30Z）。`dates.py` 用 UTC-5 近似取当前比赛日
4. **总分 regime 修正（已实现，2026-11-03）**：2025-26 赛季总分从 10-12 月的 233-235 降到 1-2 月的 225-226，全季加权回归追不上。现在 `ratings.fit` 会把总分截距向"近 30 天常规赛实际均分"回归（只算常规赛、跨休赛期自动跳过）。回测 bias 从 +2.59 降到 +0.91，RMSE 21.80→21.71。参数：`NBAQ_TOTAL_REGIME_WINDOW`(30天)/`NBAQ_TOTAL_REGIME_BLEND`(1.0)/`NBAQ_TOTAL_REGIME_MAX_GAP`(10天)。剩余 bias 靠市场融合缓解；进一步可试把 total 的 `MARKET_BLEND` 单独调到 0.4
5. **盘口快照去重**：`insert_odds` 只在 line/price 变化时插新行，避免每天全量刷爆
6. **推送去重**：notifications 表按 (game, market, side, line, price) 唯一，同一信号只推一次
7. **ESPN 免费盘口只有部分比赛有**（约 4/14），没有的比赛靠 UI 手动录盘口（`POST /api/odds`），录完自动重算
8. **历史盘口拿不到**：回测的让分/大小分方向命中率永远空，靠 `watch` 前向积累收盘价，坚持 1 个月才有 CLV 样本

## 5. 已验证的环境事实（省得再踩坑）

- ESPN `site.api.espn.com/.../nba/scoreboard?dates=YYYYMMDD`：免费稳定，无 key；**日期必须无横线**
- ESPN summary（`?event=ID`）含 injuries / odds / boxscore / winprobability
- `cdn.nba.com` liveData 直接请求 **403**（需要浏览器头/签名，赛中阶段再解决）
- ESPN 历史日期查询**没有盘口**（odds=0），只有比分
- 2026-11-03 是 NBA 休赛日（0 场），下一批是 11-04 的 14 场
- `stats.nba.com` 未接（需要特殊 header，被云 IP 屏蔽风险）
- Windows 终端中文乱码：`chcp 65001` 即可，纯显示问题

## 6. 常用命令

```powershell
cd F:\量化\nba-quant-20261103; .\.venv\Scripts\Activate.ps1; chcp 65001
# 若 venv 不在：& "$env:USERPROFILE\tools\uv\uv.exe" pip install --python .venv\Scripts\python.exe -e ".[dev]"

python -m app.cli refresh --date 2026-11-04   # ingest+train+predict(+推送)
python -m app.cli watch                       # 常驻：全部自动化（推荐挂着）
python -m app.cli serve                       # Web/PWA, http://127.0.0.1:8000
python -m app.cli closing / settle / report   # 收盘价 / 结算 / 报表
python -m app.cli backtest --test-start 2026-01-01
python -m app.cli notify-test                 # Telegram 配置验证
python -m app.cli status                      # 数据量
.\.venv\Scripts\python.exe -m pytest -q       # 测试
```

环境变量：`NBAQ_TG_TOKEN`/`NBAQ_TG_CHAT_ID`（Telegram）、`NBAQ_NTFY_TOPIC`、`NBAQ_WATCH_INTERVAL`(300s)、`NBAQ_CLOSING_WINDOW`(10min)、`NBAQ_EDGE`(3%)、`NBAQ_MARKET_BLEND`(0.55)、`NBAQ_TOKEN`（API 认证）。

## 7. 待办（按优先级，今晚可挑着做）

1. ~~**总分 bias 校准**~~ ✅ 已完成（2026-11-03，见 §4 第 4 条；bias +2.59→+0.91）。后续可选：total 单独用更激进的 `MARKET_BLEND`（如 0.4）
2. ~~**球员级 RAPM**~~ ✅ v1 已完成（2026-11-03）。**ESPN summary 自带完整 PBP（含 Substitution 事件）**，不需要 cdn.nba.com（403）。管线：`scripts/backfill_pbp.py` 抓 PBP → `app/models/rapm.py` 换人重建阵容→stint 分差/回合数→加权岭回归（单数字，净分/100回合，lam=300）→ `predict` 时自动给 Out/Day-To-Day 球员出 `injury_suggestions`（不动手动 impact 数学）。命令：`python -m app.cli rapm-train`；API：`GET /api/rapm`。watch 每天自动补前两天 PBP 并重训。
   已知局限：a) 107 场小样本，弱队效应会漏进球员系数（无 team fixed effects）；b) 上赛季评分未接入（球员流动，先验混合待做）；c) lam 未网格搜索。后续：ORAAM/DRAPM 拆分、把 suggestion 写入 UI
3. **球员盘（props）**：分钟模型（GBM/分层）+ 负二项产出率 → 蒙特卡洛。ESPN summary 里有球员数据，**现在 players 表和 PBP 管线已就绪**
4. **自动伤停影响**：给常见球星维护一个 impact 先验表（config 或 DB），Out 时自动扣分
5. **The Odds API 交叉验证** → ✅ 已接入（2026-11-03）：key 放 `.env`（`NBAQ_ODDS_API_KEY`，已 gitignore）。**省额度策略**：状态文件 `data/odds_api_state.json` 控制间隔，默认 20h 内只拉一次（h2h+spreads+totals × us = 3 credits/次 ≈ 90/月）；watch 每日自动 + CLI `odds-api --date [--force]`；每次打印剩余额度（响应头 x-requests-remaining），额度状态持久化。一次请求=多家 bookmaker（DraftKings/FanDuel/Bovada/BetMGM/BetRivers）全部入库 odds_snapshots（book=书名），首日实测 3 credits 入库 150 行、额度剩 497。**待做**：融合定价从"ESPN 单线"升级为"多书去水共识"（数据已就绪）
5b. **好体育免费亚盘源**（与 Odds API 互补、零额度）：`app/ingest/hao.py` 已逆向并验证——`tournament/info`(sid=2) 内嵌 matches 列表 + `match/simple` 单场盘口（`ah` 多档亚盘/港式水位、`ou` 多档大小、`ml`）；x-checksum=base64("按域名绑定hex|<镜像域名>|<uuid>|<毫秒>|")。**多镜像确认**：39374.app 与 333ty331.app 是同一 tiger-fluid 产品的不同部署实例（数据逐字节相同），hao.py 已实现跨镜像故障转移 + 从首页 HTML 自动发现新 checksum。watch 每日入库（book='hao-tiyu'）；对比脚本 `python -m scripts.compare_books`。首日观察：与 ESPN 价差 0.02-0.06 级别、覆盖 5/14 场但含 ESPN 空缺场次，互补
6. **注单管理 UI**：现在只有 `/api/bets` 和首页一个统计块，可以加一个"注单"标签页（列表+CLV）
7. ~~**赛中实时**~~ ✅ v1 已完成（2026-11-03）：**不需要 cdn.nba.com**——ESPN scoreboard 就是实时源（clock 秒数/period/state pre|in|post），summary 的 `winprobability` 给逐 play 主胜率曲线。实现：`app/jobs/live.py`（live_projection：投影=当前比分+赛前模型×剩余时间比例，σ 按 √剩余时间收缩）+ `GET /api/live?date=` + PWA"赛时"标签页（30 秒自动刷新，进行中卡片含 ESPN 胜率条/动量箭头/投影四格/赛前盘口覆盖概率/最近 plays）。**注意：11-03 休赛日，"in 状态"渲染只在合成数据下验证过，11-04 有真实比赛时看一眼**
8. ~~**小问题：延期赛**~~ ✅ 已修（predict 跳过 `STATUS_POSTPONED/CANCELED`，`SKIP_STATUSES` in predict.py）
9. **安卓 APK**：PWA 已可用（Tailscale Serve 走 HTTPS 才能安装），Capacitor 打包见 README §9

## 8. 继续开发时的注意事项

- 改了模型/定价逻辑后：先 `pytest`，再 `backtest --test-start 2026-01-01` 对比指标，别只看一晚的信号
- `predictions` 表每次 predict 都插新行（保留历史），API/脚本永远取最新一条；新写查询时注意
- settle 的 CLV 依赖"开赛前"的快照（按 tipoff 过滤时间戳），手动测试可用 `seed_bet.py --closing-price` 模拟
- ESPN 接口字段是宽松解析的（tolerant parser），遇到解析失败先看 `data/raw/<date>/` 里存的原始 JSON
- 版本管理：✅ git 已初始化（`data/`、`.venv`、日志已 gitignore；改完代码 `git add -A && git commit`）
- 本项目数据库已纳入每日自动备份（data/backup/ 滚动 7 份）