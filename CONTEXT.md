# CONTEXT.md — 项目交接文档（2026-11-04 全量工程优化后）

> 用途：把项目的全部上下文压缩在一页里。继续开发时，把本文档 + README.md + OPTIMIZATION_NOTES.md 一起交给 AI 或自己阅读即可无缝接上。
> 本版覆盖：从"项目是什么"到"两轮全量工程优化的全部结论与验收数据"。
> 更新日期：2026-11-04（北京时间晚间；已在 `F:\量化\nba-quant-20261103` 完成合并与本地复核，见第 10 节）

---

## 0. 项目是什么

**个人研究用** NBA 赛前量化分析工具（注意边界）：

- 定位：个人研究 + 决策辅助 + 纸面跟踪。**不做**自动下注、不接资金、不承诺盈利（合规：大陆体育投注违法，工具只做数据分析）
- 用户三个决策（已确认）：个人研究 / 先做赛前 / 免费数据源起步 / 后续要能在安卓端用
- 数据源：ESPN 免费接口（scoreboard + summary）、SBR 历史收盘线、The Odds API、HoopsAPI、好体育亚盘
- 技术栈：Python 3.12 + FastAPI + SQLite + 原生 HTML/JS PWA（无构建步骤）
- 本机工作副本（打包来源）：`D:\YYMODEL\nba-quant`（从 GitHub zip 解压，无 `.git`）
- **当前活跃工作副本**：`F:\量化\nba-quant-20261103`（有 `.git` 与 `origin`，`.venv` 已就绪，可直接 commit/push；`data/nba.db` 现在由 `data` 分支承载）

---

## 1. 环境（本机）

- Python 3.12.10（全局安装，依赖已装：fastapi 0.162.0、numpy 2.5.4、httpx、pytest、python-dotenv）
- **本机（F:）有 git**：`origin` = `https://github.com/gem08166/nba-quant.git`；网络走 TUN 代理时推送不稳，失败就先 `git pull --rebase origin main` 再重推（**不要**用 `-X ours`，会静默丢本地提交）
- 依赖装法：`python -m pip install -e ".[dev]"`（注意：早前装过 Temp 目录里的旧副本，已重装为工作副本 editable；**跑脚本时务必 `cd D:\YYMODEL\nba-quant`**，否则 `import app` 会解析到错误路径）
- `scripts/inspect.py` 会遮蔽标准库 `inspect`——任何需要 import numpy 的临时脚本请放在仓库根目录跑
- 磁盘 D 盘剩余约 22GB

---

## 2. 当前状态

| 项 | 状态 |
|---|---|
| 数据库 | `data/nba.db`：1427 场（2025-26 全季 1239 + 2026-27 季前 67 + 常规赛 121），8538 条盘口快照，1409 场已完赛，224 条预测，19 个模型 |
| 模型 | 球队模型 n=1338，sigma=(15.20, 19.14)；**新增跨季季前赛锚定**（默认 0.5） |
| 盘口数据 | 实时管线（非 sbr-*）：15 场 / 449 行 / 20 个快照时点 / 12 家书源（刚起步 2 天）；SBR 收盘回填：194 场 × 8 书源（**单时点，无法做线移动检验**） |
| PBP | 107 场 play-by-play（54231 行），players 468、starters 1070 |
| RAPM | 18 个模型，v1 |
| 纸面注单 | **0 条**（演示单 id=1 已在本机删除并备份，避免 ROI +106% 假头条污染收益页） |
| 测试 | **46 passed**（本轮 +8） |
| 数据库托管 | `data/nba.db` 已移出 main，改由独立 `data` 分支（单 commit 快照）承载；本机已 seed |

---

## 3. 核心结论（这是本项目最有价值的部分，别推翻）

### 3.1 模型有预测力，但相对市场无增量信息

| 检验 | 结果 |
|---|---|
| walk-forward 回测（841 场） | 分差 MAE **11.92** / 总分 MAE **15.31** / Brier **0.2035** / 方向 69.4% / ATS 47.6% (n=105) / O-U 50.5% (n=107) |
| 冻结 holdout（943 训 → 395 考） | 分差 MAE **12.13**（常数基线 14.46）/ 总分 **15.57**（基线 16.39）/ Brier **0.1916** / ECE 0.077 / 方向 70.9% |
| 收盘线基准（177 场） | 分差 MAE **9.89** / 总分 MAE **14.99** |
| 模型 vs 收盘线（498 注） | 平均 edge **+6.07%**（t=28.8），但收盘价下注 ROI **−11.32%**，命中 **43.15%** |
| 控制收盘线回归 | 分差 β=**−0.013**（t=−0.04）；总分 β=**−0.507**（t=−1.63） |
| Brier 对比 | 模型 0.2084 vs 市场 0.1844 |
| 最大 edge 分位 | ROI **−27.7%** / 命中 32.8%（胜负最大分位 −42.8% / 19.0%） |

**结论**：模型优于"无脑预测 0 分"（MAE 11.24 vs 12.85），但这些信息已被收盘线定价；控制收盘线后 β≈0 → **模型没有可检测的增量信息**。

### 3.2 三条独立证据指向同一结论（"信号是乐观偏差"）

1. **β≈0**：控制收盘线后模型系数不显著
2. **线移动反向**（`scripts/linemove_analysis.py`）：61 条信号中 36 条有线移动，顺线比例仅 **30.6%**（z=**−2.33**）——模型看好的那侧，市场随后**显著反向**移动
3. **blend 扫描**（`scripts/blend_sweep.py`）：blend=0（纯市场线选边）ROI **+3.14%**；blend≥0.15 全为负（−5.3%~−9.5%）；平均 edge 随 blend 单调上升（+0.4%→+10.2%）——**模型权重伤害选边**

→ **信号定位为「模型 vs 市场分歧观察」，不构成下注依据。** 项目自己的 `research.js` 基准对照卡已诚实写明。

### 3.3 "照信号下注"方向的闸门体系（已建好，等数据）

| 层 | 实现 | 状态 |
|---|---|---|
| 第 0 层：线移动验收 | `scripts/linemove_analysis.py` / `app.cli linemove` / 研究页 gate-card | **首验未通过**（z=−2.33） |
| 第 2 层：bettable 闸门 | `signal`（模型算出 EV）与 `bettable`（价格真实可拿）分离：软书价降级、共识<3 家降级、同场让分/ML 互斥 | 已上线；今日 7 信号 → 1 条 bettable |
| 第 2 层：sharp 锚定 | `NBAQ_SHARP_ANCHOR=1` 用 Pinnacle 类 sharp 书去水值当 fair | 已实现，默认关（实时源暂无 sharp 书） |
| 第 3 层：组合保护 | 同场互斥 `NBAQ_MUTUAL_EXCLUSIVE=1` | 已上线 |

---

## 4. 本轮（两轮）全量工程优化的交付清单

### 第一轮：工程正确性
- **P0-1 Pages 部署慢一个 run**：deploy job 原来重新 checkout 触发 SHA → 永远发布上一轮数据。现在 refresh job 上传 artifact，deploy 只发布同一 run 产物。
- **P0-2 共识退化成单书**：① workflow 里第二书源移到 predict 之前 ② `odds_snapshots.last_seen_at`（报价未变也刷新，修复"稳定线被误判超龄"）③ `NBAQ_MIN_FRESH_BOOKS=3` 新鲜书源不足时回退全量共识并标 stale（旧逻辑有 1 家新鲜就丢掉其余 11 家）。
- **P1-1 PWA sw.js 404**：`export_static.py` 现在把 `sw.js` 复制到站点根目录。
- **P1-2 演示注单**：已删（DB 备份在临时目录）。
- **P1-3 CI 跑 pytest**。
- **P2-1 SQLite 移出 Git**：DB 走独立 `data` 分支（Actions restore + force-push 单 commit 快照），主仓只留 `site/` 和抓取状态。
- **P2-2 CLV 加线移动**：`bets.clv_line`（分，正=拿到更好的线），收益页新增统计卡/列。
- **P2-3 整数盘口走盘感知**：`cover_prob/over_prob` 改为 `P(win)+0.5·P(push)`；`model_clv_history` 改用共享概率函数（spread ROI −7.02%→−5.88%，是口径修正不是数据变化）。
- **P2-4** README 重写 + MIT LICENSE。

### 第二轮：可下注闸门 + 验证体系
- `scripts/linemove_analysis.py`（线移动验收）+ `tests/test_linemove.py`
- `bettable` 闸门（`app/config.py`/`app/jobs/predict.py`/`app/web/app.js`）+ `tests/test_bettable.py`
- `scripts/holdout_eval.py`（冻结 holdout 70/30）+ `app.cli holdout` + `tests/test_holdout.py`
- `scripts/blend_sweep.py`（blend 扫描固化）+ `tests/test_blend_sweep.py`
- 跨季 regime 修正（季前赛锚定）+ `tests/test_season_carry.py`
- 前端：看板"可下注/可下注最佳 EV"统计卡、"可下/仅观察"pill；研究页 gate-card 面板（线移动 + holdout vs walk-forward vs 基线）

---

## 5. 关键设计决策与踩坑记录

### 5.1 切分必须按时间，严禁随机
随机 70/30 会让测试期比赛通过 Elo/球队强度泄漏进训练集。holdout 与 blend_sweep 都按时间切（同日比赛不拆）。

### 5.2 walk-forward vs 冻结 holdout 的分工
- walk-forward（每月重拟合，841 场全用）：模拟真实生产使用，统计功效高，但超参（σ 下限/MARKET_BLEND/regime 修正）是看着全部数据定的
- 冻结 holdout（943→395）：模型参数/σ/Elo 终值全在 cutoff 前生成，量化超参过拟合
- 结论：两者接近（11.92 vs 12.13）→ **无超参过拟合**

### 5.3 跨季 regime 修正：失败方案也要记录
- 第一版"上赛季尾月残差外推"**验收失败**：2026-10 开局 16 场 MAE 13.68→14.25、|bias| 5.36→7.65（方向反：上赛季末得分上行但新赛季低开）→ 废弃
- 替代"本赛季季前赛锚定"（季前赛被训练集排除，但无泄漏且直接测量新赛季环境）：MAE 13.68→**13.39**、|bias| 5.36→**4.19** ✅
- 教训：holdout 场景（as_of 在赛季中）**根本触发不到**跨季分支，必须用生产路径（逐日重训）重放才测得出来
- 局限：只在赛季开局 ~1 周有意义（之后逐日重训自愈），样本仅 16 场 1 个赛季，明年开季需复核

### 5.4 定价层细节（都来自实际踩坑）
- 书源别名归一化（Draft Kings/DraftKings 曾被当两家）
- 离群书源剔除（hao-tiyu ML 反向案例）
- 陈旧最优价降级（1xbet 12h 未更新的 4.01 会虚造 edge → 用共识公允反推"可成交价上限"）
- 单边报价不出 edge（不按 50/50 造假信号）
- ML EV 用共识中位价（最优价仅展示）
- EV 用同一 `ev_price` 算（不做价差假设）

### 5.5 `_next_season_label` 的坑
写错过成 `start+1`/`start+2` → 产出 "2026-26" 这种不存在标签 → 跨季分支静默失效。正确：`f"{start+1}-{str((start+2)%100).zfill(2)}"`。已有测试锁定。

---

## 6. 常用命令

```powershell
cd D:\YYMODEL\nba-quant
python -m pytest -q                                    # 46 passed
python -m app.cli status
python -m app.cli predict --date 2026-11-04
python -m app.cli backtest --test-start 2026-01-01     # walk-forward
python -m app.cli holdout --split 0.7                  # 冻结 holdout
python -m scripts.blend_sweep                          # blend 扫描
python -m scripts.linemove_analysis                    # 线移动验收
python -m scripts.model_clv_history --start 2025-10-21 # 收盘线基准
python -m scripts.export_static --out site             # 导出静态站
```

---

## 7. 待办与下一步（按优先级）

1. ~~【用户操作】git 提交推送~~ **已完成（2026-11-04 晚，本机 F:）**：`data` 分支已 seed，main 已切换到"db 不进主仓"策略。
2. **复核线移动闸门**：实时快照积累 2-4 周后重跑 `app.cli linemove`。若 z 仍 <0 → "照信号下注"方向正式关闭，资源转球员盘（props niche）。**本机复跑（63 信号 / 35 有移动）：顺线 31.4%，z=−2.2，仍未通过。**
3. **评估加固**：7 家 SBR 共识收盘线（现在用单家 sbr-fanduel）、bootstrap CI、回测复用生产 `build_market`。
4. **sharp 书源接入**：`NBAQ_SHARP_ANCHOR` 需要 Pinnacle 进入实时管线（HoopsAPI 侧）。
5. **球员盘（props）**：PBP 107 场 + 阵容重建 + RAPM 已有，是最可能有真实 edge 的 niche（props 市场效率低于主盘）。
6. **明年 10 月**：复核跨季修正（见 5.3）。

---

## 8. 边界与免责

- 仅个人数据分析/建模/回测研究，不提供下注执行、资金通道或收益承诺
- 大陆体育投注违法；执行层面只适用于合规辖区
- 数据源脆弱：SBR 网页抓取、好体育逆向 checksum 多镜像；存在 ToS 风险
- 时区近似：`dates.py` 固定 UTC−5，夏令时期间有 1 小时偏移（影响很小，注释已承认）

---

## 9. 文件索引

```
app/
  config.py            # 阈值与开关（EDGE 5%、MIN_FRESH_BOOKS、BETTABLE_*、SHARP_ANCHOR、SEASON_CARRY）
  db.py                # schema + 迁移（last_seen_at / clv_line）
  api.py               # REST（含 /api/linemove、/api/holdout）
  jobs/
    predict.py         # 共识/定价/bettable 闸门/同场互斥
    settle.py          # 结算 + 价格 CLV + 线移动 CLV
    signal_backtest.py # 信号级回测
    backtest.py        # walk-forward
  models/ratings.py    # 分差/总分模型 + regime 修正 + 跨季季前赛锚定
  pricing/devig.py     # 去水 + 走盘感知概率
  i18n.py              # 球员英文名 → 中文名（查 data/player_zh.json）
  web/                 # PWA 前端（app.js/bets.js/research.js/static.js/live.js/localbets.js）
scripts/
  export_static.py     # 静态站导出（含根级 sw.js）
  holdout_eval.py      # 冻结 holdout
  blend_sweep.py       # MARKET_BLEND 扫描
  linemove_analysis.py # 信号→线移动验收
  build_player_zh.py   # 抓虎扑名单生成中文名映射（data/player_zh.json）
  model_clv_history.py # 模型 vs 收盘线
data/                  # nba.db（现已不进主仓）+ 各报告 JSON
site/                  # 静态站产物（进主仓）
tests/                 # 46 个测试
OPTIMIZATION_NOTES.md  # 两轮优化详解 + git 操作步骤
README.md              # 用户文档（含第 0 节验证结论）
```


---

## 10. 2026-11-04 晚间：合并「新版本」包到本机工作副本

**来源**：`F:\量化\新\nba-quant-20261104-1807.zip`（另一台机器 `D:\YYMODEL\nba-quant` 在基线 `e9fd412` 上做的两轮工程优化，含第 3 轮审查修复）。

### 10.1 合并结论

- 该包对代码是**严格超集**：本机在 `6387939` 的三连修（跨市场闸门 / 共识中位价 EV / 分歧风险分级）都在包内，包额外带：`last_seen_at` 新鲜度、`MIN_FRESH_BOOKS=3`、sharp 锚定、bettable 闸门、同场互斥、走盘感知概率、CLV 线移动、holdout/blend/linemove 三件验收仪器、跨季季前赛锚定。
- 合并方式：代码/测试/脚本整体采用包内版本；`data/nba.db` 保留本机（含 SBR 194 场回填 + 最新盘口）；报告 JSON 全部用本机 DB 重算（不复制对方产物）；`site/` 重新导出。
- 合并时补的两个小修：
  1. `bettable` 增加前置条件（必须 `signal` 且未被 `gated`）——包内旧逻辑会把被分歧闸门拦下的 ML 也标成"可下注"；
  2. `divergence_risk` 写入 prediction details 并在 `/api/games` 暴露（前端此前自己重算）。
  3. **静态站「记一笔」恢复**：原先 `body.static-mode [data-bet]` 直接 `display:none`，Pages 上无法记录纸面注单。现改为浏览器 localStorage（`app/web/localbets.js`，key `nbaq:bets:v1`），收益页本地结算（让分/大小分/胜负，含走盘），并标注「本机浏览器记录 · 不同步」；盘口/伤停的手工修改仍保持只读。
- `app/web/sw.js` 缓存版本 `nbaq-v13` → **`nbaq-v14`**（research/app/bets 均有改动，不升版本会让已装 PWA 继续吃旧缓存）。

### 10.2 数据库托管方式变更（重要）

- `data/nba.db` 不再进 main：`.gitignore` 改为 `data/*`（仅放行 `odds_api_state.json` / `hoops_state.json` / `backup/`）。
- 已在本机 seed `data` 分支（孤儿单 commit，仅 `nba.db`）：`git fetch origin data && git show origin/data:nba.db > data/nba.db`。
- Actions 每次 run 从 `data` 分支 restore、跑完 `wal_checkpoint` 后 force-push 回去，主仓只提交 `site/` 与抓取状态。
- **本机以后同步 DB**：`git fetch origin data` 后 `git show origin/data:nba.db > data/nba.db`（本地改了 DB 也不要 `git add data/nba.db`，它已被忽略）。

### 10.3 本机复核数据（2026-11-04 20:1x，14 场 slate）

| 项 | 结果 |
|---|---|
| `pytest -q` | **46 passed** |
| walk-forward（test-start 2026-01-01） | 分差 MAE 11.9x / 总分 MAE **15.30** / RMSE 19.14 / bias +0.70 |
| 冻结 holdout（70/30，395 场考试） | 分差 12.133 / 总分 15.567 / Brier 0.1916 / ECE 0.0774 |
| blend 扫描（98 注） | blend=0 → ROI **+3.14%**；blend=0.55 → **−9.51%**（模型权重仍伤害选边） |
| 收盘线报告（498 注） | ROI **−10.94%**（ML −15.28 / spread −5.88 / total −11.61），hit 43.35% |
| 线移动（63 信号） | 顺线 **31.4%**（z=−2.2）→ 未通过 |
| 今日信号 | 7 条 signal / **1 条 bettable**（CLE ML +4.8%）；MIN@HOU ML +26.8% 因 1xbet 软书被降级观察 |
| 收益页 | 演示单已删，`0 结算 · ROI —` |

### 10.4 下一步（合并后）
0. **投注助手 P0（2026-11-04 晚已完成）**：①收盘窗口 10→120 分钟（`CLOSING_WINDOW_MIN`）；②新增 `odds-refresh.yml` 每 30 分钟轻量 tick（`watch --once`：抓盘口/收盘/结算/预测/推送，**不提交 main**，只 force-push data 分支 + 部署 Pages）；③ntfy 推送接通（标题 UTF-8 字节修复 ascii 报错），本机 topic 在 `.env`，CI 走 repo secret `NBAQ_NTFY_TOPIC`（待用户在 Settings→Secrets 添加，同时加 `NBAQ_ODDS_API_KEY`、`HOOPS_API_KEY`——此前 CI 里这两个源一直没 key，多书共识只靠 hao-tiyu）；④本机 `app.cli watch` 常驻（会话内）。
1. 推送本机改动并等 Actions 用 `data` 分支跑通一轮（重点看 restore 是否成功、`site/data/static.json` 的 `generated_at` 是否等于本 run）。
2. 线移动检验需继续积累实时快照（当前 63 信号 / 35 有移动），2-4 周后复跑。
### 10.6 手机 APK（Capacitor，2026-11-05 进行中）

- 结论先行：APK 里用 **CapacitorHttp**（原生网络）绕过 CORS → 手机可直连坚果云 WebDAV，"手机做数据采集"和"独立运行"都成立；TWA/PWABuilder 套壳则不行（仍是浏览器内核）。
- 架构：APK = 打包的静态站（`site/`）+ 原生网络桥。数据优先级：坚果云 `nbaq/static.json`（CI 每 30 分钟推）→ `github.io` 兜底；注单本地存 + 坚果云双向同步（`localbets.js::lbDavSync`，hash 去重）；设置（坚果云账号/应用密码/Odds API key）只存手机。
- 阶段 2/3 采集：`collector.js` 把 ESPN scoreboard 的 odds 转成 The Odds API events 形状（价格换十进制），可选并入 Odds API 数据，推 `nbaq/snapshots/inbox/snap-*.json`；PC `betssync.run()` 拉取 → `theodds.ingest_events`（从 store_odds_api 重构出的共享入口）→ 入库 → MOVE done。
- CI：`odds-refresh.yml` 在 export 后跑 `bets-sync --push-static`（推 static.json 到坚果云），secrets 加了 `NBAQ_WEBDAV_USER/PASS`。
- 构建：`mobile/build.ps1`（npm install → 拷 www → cap add/sync → gradlew assembleDebug）→ `mobile/nbaq-debug.apk`。环境：Node 26 + Temurin JDK 17（winget 装好）+ 已有 Android SDK（`%LOCALAPPDATA%\Android\Sdk`）。
3. 收盘线基准下一步从单家 `sbr-fanduel` 改成 8 家共识（`backfill_closing` 已回填 8 书源，报告侧尚未用共识）。

### 10.5 台账坚果云同步（2026-11-05）

- **实测结论：坚果云 WebDAV 无 CORS 头**，浏览器（手机 PWA）直连必挂——同步只能在 PC 服务端做。
- 架构：PC `watch` 每 5 分钟跑 `betssync.run()`（`app/jobs/betssync.py` + `app/jobs/webdav.py`）：
  1. 拉 `nbaq/inbox/*.json`（手机「导出台账」→ 坚果云 app 上传）→ 按 `ref` 去重合并进 bets 表 → MOVE 到 `inbox/done/`；
  2. 拉 `nbaq/bets.json`（云端全量，可能来自另一台 PC）合并；
  3. 推送本地全量台账回 `nbaq/bets.json`（内容 hash 没变就跳过，省坚果云请求配额）。
- `bets` 表新增 `ref` 列（唯一索引）：静态端 = 注单字符串 id（`local-...`），服务端 = `srv-<id>`，云端无 ref 时用 `gen-<md5>` 兜底。
- 手机 PWA 收益页新增「导出台账 / 导入台账」（文件中转，走坚果云 app）。
- 配置：`.env` 里 `NBAQ_WEBDAV_USER`（注册邮箱）+ `NBAQ_WEBDAV_PASS`（坚果云网页 → 账户信息 → 安全选项 → 添加**应用密码**，不要用登录密码）。留空 = 不同步。手动跑：`python -m app.cli bets-sync`。

### 10.7 2026-11-05 本机环境重建与全面复核（D: 工作副本）

- `.venv` 从另一台机器带来的 uv trampoline 在本机无法启动（`uv trampoline failed to spawn Python child process / os error 2`）→ 重命名为 `.venv.old`，用系统 Python 3.12.10 重建，`pip install -e ".[dev]"`。
- 复核结果：`pytest -q` **55 passed**；`app.cli status`（19.9MB DB：1427 场 / 11170 条盘口 / 2212 条预测 / 30 个模型）；`export_static` 正常；`uvicorn` 起服务后 `/api/health`、`/api/games`、`/api/linemove`、`/api/holdout`、`/api/live`、`/api/model-clv`、`/`、`/static/*`、`/sw.js` 全部 200；浏览器实测看板/收益/研究/赛时四页正常，赛时页 9 场进行中。
- 复核中修复 3 个问题：
  1. `nbaq --help` 崩溃：`holdout` 的 help 字符串含未转义 `%`（argparse 会做 %-格式化）→ 改为 "first N percent"。
  2. API 模式研究页「walk-forward（对照）」永远显示 "—"：该行只读 `window.STATIC_PAYLOAD`（静态模式才有）→ 缺省时改为 `await api("/api/backtest")`。
  3. `export_static` 默认日期用 `date('now')`（UTC）：北京时间早上 8 点后 UTC 跨日，会把还在进行的今天 slate 跳过去 → 改用 `current_slate_date()`（UTC-5），无比赛时回退到该日。
- 同步：`export_static --out site` 后把 `site/` robocopy 到 `mobile/www`（与 `build.ps1` 的步骤一致），保持 APK 源与站点一致。
- 本机限制：无 git（PATH 里没有可执行文件）、无 Android SDK / JDK21 / Node → 不能在本机构建或安装 APK；已有 APK 4.08MB，需在有 SDK 的机器重建或在手机上直接安装。
- 数据库：本机 `data/nba.db` 19.9MB（比另一台 8.9MB 新），保留本机不动；主仓仍不跟踪 DB。

### 10.8 2026-11-05 实时注单台账（收益页）

- **数据链路**：`watch --once`（本地 5 分钟 / CI 每 30 分钟）每轮 ingest 实时比分（`games.live_home_score/live_away_score`）→ `settle` 完赛自动结算 → 收益页读取。
- **API**：`/api/bets/analysis` 的每条注单新增 `game_status`、`live_home_score`、`live_away_score`、`final_home_score`、`final_away_score`；静态导出的 `bets.json/static.json` 同字段。
- **前端**：未结算注单显示「目前赢盘/输盘/贴盘 / 目前领先/落后」（API 与静态模式同口径）；收益页可见时每 **30 秒**静默刷新，切回浏览器标签立即刷新。测试：`tests/test_bets_live.py`（2 例）。
- **延迟**：本地 watch ≈ 5 分钟；云端 CI cron ≈ 30 分钟（GitHub cron 可能再晚几分钟）；完赛到结算最多一个 tick。手机端需重建 APK 才能带上新的 bets.js（静态数据本身已同步到 `mobile/www`）。

### 10.9 2026-11-05 watch 常驻（15 分钟间隔）

- 配置：`.env` 增加 `NBAQ_WATCH_INTERVAL=900`（15 分钟）；`CLOSING_WINDOW_MIN=120`（开赛前 2 小时持续抓收盘价）。
- 每轮（15 分钟）：ESPN 比分/盘口/伤停 → 收盘价捕获 → 完赛结算+CLV+ntfy → 预测重算 → 导出静态站+坚果云推送 → 新信号推送。
- 每日 slate 切换时额外：重训模型、DB 备份、好体育、The Odds API（20h 门）、Hoops（24h 10 次预算）、PBP+RAPM。
- 常驻方式：任务计划程序需要管理员权限（本机被拒），改用**启动文件夹 + 隐藏启动脚本**（无需管理员）：
  - `scripts/watch-ctl.ps1`：start / stop / status / restart（内含单实例检查）。
  - `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\nbaq-watch.vbs`：登录时隐藏启动。
- 修复：`pythonw` 下 `sys.stdout` 为 `None` 会让 `Tee` 崩溃（隐藏常驻必踩）→ `Tee.write/flush` 判空；`sys.stderr` 也重定向到日志，traceback 可落盘。测试：`tests/test_watch_tee.py`（2 例）。
- 运维命令：
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 status
  powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 start
  powershell -ExecutionPolicy Bypass -File scripts\watch-ctl.ps1 stop
  ```
- 实测一轮：ingest 14 场 / 74 伤停 → backup → hao +2 → odds-api 跳过（12.1h<20h）→ hoops 429 退避 → RAPM 3370 stints → closing 3 场 → betssync static_pushed=True。
- 成本：96 轮/天；predictions 约 1344 条/天（~5MB/天，约 150MB/月）；坚果云 static.json 约 23MB/天。若 DB 增长过快，下一步加预测节流或按周归档 predictions。

### 10.10 2026-11-05 注单台账 → 比赛实时板块 跳转

- 收益页台账里的比赛名改为可点击链接（`.linklike`）：点击后：
  - 注单比赛进行中/中场 → 切到「赛时」页，等待实时卡片渲染后滚动到该场并高亮 1.8 秒；
  - 未开赛/已完赛 → 切到「赛前看板」、自动切到该比赛日，滚动并高亮该场。
- 实现：`app/web/app.js` 新增 `focusGameCard` / `window.jumpToGame`（轮询等待数据渲染，最多约 18s）；`live.js` 卡片加 `data-game`；`bets.js` 台账比赛名加 `data-goto-*` 与委托点击；`styles.css` 加 `.linklike` / `.card.flash`。
- 兜底：静态模式（手机）赛时页的 ESPN 直连只返回部分场次时，自动回退到赛前看板定位该场；都找不到才提示。
- 实测：API 模式点击 CLE@OKC 注单 → 赛时页 17s 后定位并高亮（`/api/live` 拉 ESPN 约 15s）；静态模式 → 赛时页未命中后回退看板，13.5s 定位成功。
- 已知体验点：首次点进行中注单需等 `/api/live`（约 15s）才有卡片；若之前已打开过赛时页则是秒级。

### 10.11 2026-11-05 可下注大小分显示盘口 + 赛时每节得分

- **大小分盘口**：此前「可下注」面板/信号徽章/弹窗只给让分显示线，大小分只有方向没有分值。现在三处统一显示 `大小分 · 小分 233.5 @ 1.95`（`fmtTotal(edge.line)`）。顺带修了一个符号 bug：away 让分被二次取负（`edge.line` 本身已是该侧线，之前显示成相反数）。
  - 文件：`app/web/app.js`（`edgeText` / `renderBettablePanel` / 弹窗边缘表）。
- **赛时每节得分**：
  - 后端 `app/jobs/live.py`：`_parse_event` 解析 ESPN `linescores` → `home_linescores` / `away_linescores`（Q1..Q4/OT，缺失为 null）。
  - 静态模式 `app/web/static.js`：`jsParseEvent` 同步解析（`jsLinescores`）。
  - 前端 `app/web/live.js`：新增 `quarterScores()`，赛时/完赛卡片在比分下方显示 `Q1 29:21 Q2 27:21 Q3 35:33 Q4 16:27 合计 107:102`（客:主）。
  - 样式 `styles.css`：`.qs-row/.qs-col`（横向滚动、合计列绿色描边）。
- 实测：`/api/live` 返回 `home_linescores=[21,21,33,27]`；页面赛时卡显示 `Q1 29:21 … 合计 107:102`；可下注面板显示 `大小分 · 大分 218.5 @1.94 +7.7%`。**59 passed**；`site/` 与 `mobile/www` 已重新导出/同步。
- 注意：浏览器若已打开旧页面，需强制刷新（Ctrl+F5）才能拿到新 JS；SW 为 network-first，联网刷新即可。

### 10.12 2026-11-05 赛时板块标注有注单的比赛

- 后端 `app/jobs/live.py`：`live_board` 一次性查出所有 `result='open'` 注单，按 `game_id` 聚合为 `open_bets`（market/side/line/price/stake），随 `/api/live` 每场返回。
- 前端 `app/web/live.js`：`openBetsOf(game)` 合并三处来源（接口 `open_bets` / 本机 `window.LB.read()` / 导出快照），按 id 去重；卡片顶部显示 `注单 N` 琥珀色标签，卡片描边变琥珀色（`.card.has-bet`），比分下方显示每条注单的单据 `让分 客 -3.5 @1.88`、`大小 小 233.5 @1.99`、`胜负 主 @1.64`。
- 未开赛但有注单的场次同样显示（方便赛前确认）。
- 样式：`styles.css` 新增 `.bet-tag` / `.bet-row` / `.bet-chip` / `.card.has-bet`。
- 实测：5 场有未结算注单的卡片全部带 `注单 1/2` 标签与单据；`/api/live` 返回 `open_bets` 正确。**59 passed**；`site/` 与 `mobile/www` 已同步。

### 10.13 2026-11-05 看板卡片显示投注内容

- 赛前看板（`#games`）每张卡片新增：顶部 `注单 N` 琥珀标签、卡片琥珀描边、`.meta` 下方一行单据 chips（`让分 CHA -3.5 @1.88` / `大小 小 233.5 @1.99` / `胜负 OKC @1.64`）。
- 数据来源：`load()` 额外请求 `/api/bets?result=open`，按 `game_id` 聚合后传给 `renderGames(games, betsByGame)`；静态模式走 `static.js` 的 `/api/bets` 映射（同一套代码）。
- 与赛时板块（§10.12）共用 `.bet-tag/.bet-row/.bet-chip/.card.has-bet` 样式，PC 与手机一致。
- 实测：5 场有未结算注单的卡片全部显示标签与投注内容（API 模式 + 静态模式）；**59 passed**；`site/` 与 `mobile/www` 已同步。

### 10.14 2026-11-05 球队名称与赛况中文化

- **球队名**：`app/i18n.py` 新增 `TEAM_SHORT_ZH`（30 队缩写→简称，兼容 ESPN 变体 GS/GSW、NO/NOP、NY/NYK、SA/SAS、UTAH/UTA、WSH/WAS）、`TEAM_FULL_ZH`（英文全名→中文全名）、`team_zh/team_short_zh/matchup_zh`。API `_game_payload` 返回 `matchup_zh`、`home/away.name_zh`、`short_zh`；`bets_analysis` 返回 `matchup_zh`。
- **赛况**：`i18n.play_zh` 三层翻译——球员名前缀（`player_zh.json`，524 人，支持 Jr./重音/后缀）、队名昵称（Nets→篮网…）、动作短语约 60 条正则（三分/跳投/上篮/扣篮/罚球/篮板/犯规/失误/抢断/盖帽/挑战/暂停…），支持 `makes 28-foot three point jumper`、`pullup jump shot`、`free throw 1 of 2` 等模式。API `/api/live` 每条 play 返回 `text_zh`。
- **前端**：`app.js` 内置球队中文映射（`teamShortZh/teamNameZh/matchupZh`），看板卡片/详情弹窗/可下注面板/台账/赛时卡片全部使用中文队名；赛时标题「第N节」、分节表 `第1节…加时`；最近赛况优先用 `text_zh`。
- **静态模式（手机）**：`static.js` 内置同款赛况翻译规则 + 懒加载 `data/player_zh.json`（`export_static.py` 已复制到 `site/data/`），`espnLive` 输出 `matchup_zh/home_name_zh/text_zh`；修复静态模式 `state.date` 未同步到导出日期导致赛时页拉到错误日期的问题。
- 测试：`tests/test_i18n.py`（4 例）；**59 passed**；`site/` 与 `mobile/www` 已重新导出/同步。
- 实测：看板「底特律活塞 / 76人@活塞」、赛时「孟菲斯灰熊 / 步行者@灰熊」、赛况「第4节 38.5 阿龙-内史密斯 进攻犯规 失误」、弹窗「费城76人 @ 底特律活塞」、台账「森林狼 @ 火箭」；静态模式同样中文。

### 10.15 2026-11-05 过时信号自动取消

- 判定（`app/api.py::_expire_edges`，读取时动态计算，不改写 DB 历史）：
  1. 比赛状态已开赛/中场/完赛/延期/取消，或 `tipoff <= now` → 过期；
  2. 信号生成后超过 `NBAQ_SIGNAL_MAX_AGE_HOURS`（默认 12h）未更新 → 过期。
- 过期后：`signal=False`、`bettable=False`，附 `expired=True` 与 `expired_reason`；`/api/games`、`/api/games/{id}`、`/api/edges` 三处统一生效。
- 前端：看板卡片徽章改为「信号已过期 · 比赛已开始或结束」；详情弹窗状态列显示「已过期」（悬停显示原因）；信号/可下注统计与可下注面板自动排除过期项。
- 实测：当前 14 场全部已开赛/完赛 → 信号 0、可下注 0、最佳 EV —，卡片与弹窗均标注已过期。**64 passed**（新增 `tests/test_signal_expiry.py` 5 例）。
- 注意：预测历史保留在 DB（回测/信号回测仍可用），只在展示/闸门层失效。

### 10.16 2026-11-05 信号直接「记一笔」

- **可下注面板**：每行新增「记一笔」按钮（`.bp-row` 改为 div + 第 6 列按钮）；行点击仍打开比赛详情，按钮点击直接打开弹窗并展开内联下注表单（注额/成交价/盘口可改）。
- **看板信号徽章**：未过期信号徽章内同样加「记一笔」按钮（`data-quick-bet` + 边缘序号）。
- **事件**：`#games` / `#bettable-panel` 委托处理 `[data-quick-bet]` → `openGame()` 后 `placeBet(index)`。
- **样式**：`.bp-row` 网格 6 列（加 auto 按钮列），移动端媒体查询 flex 自动换行。
- 验证方式：向 2026-11-05 两场注入 3 家书源的合成总分盘口（214.5）→ 面板出现「热火 @ 勇士 大小分 · 大分 214.5 @1.91 +39.5% 记一笔」→ 点击打开弹窗内联表单 ✅；看板卡片徽章「大小分 · 大分 214.5」带「记一笔」✅。验证后测试盘口已从 DB 删除并重导出（避免假信号残留）。
- 说明：Windows 下 venv 的 `pythonw.exe` 是启动器，会派生基础解释器子进程——任务管理器里看到两个 `pythonw` 属正常（同一逻辑实例，父子进程）；`watch-ctl.ps1 start/stop/status` 按命令行匹配，会同时处理两个进程，不会出现双实例。

### 10.17 2026-11-05 已结束比赛的注单信息

- **看板卡片**：已结束比赛也显示注单，带结果与盈亏——`让分 黄蜂 -3.5 @1.88 负 -5.00`（负红色 / 胜绿色 / 走灰色）；未结算注单仍显示普通单据。`load()` 改为拉取 `/api/bets`（全部）并按比赛聚合。
- **赛时卡片**：`live_board` 的 `open_bets` 更名 `bets` 并包含已结算注单（market/side/line/price/stake/result/profit）；`live.js` 的 `gameBetsOf` 合并后端/本机/导出快照，「注单 N」标签只数未结算，单据 chip 显示结果与盈亏。
- CSS：`.bet-result`（pos 绿 / neg 红）。静态导出无需改动（bets 已含 result/profit）。
