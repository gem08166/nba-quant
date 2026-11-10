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

### 10.18 2026-11-05 僵尸未结算注单修复 + git 推送稳定性

- **问题**：注单 #5（LAL@SAC total under 234.5）比赛已收尾（103-116）但永远 `open`。根因：`current_slate_date()` 翻到 2026-11-05 后，watch 每 15 分钟只 ingest 当日赛程，11-04 的完赛场次（尤其跨夜 02:00/03:00 开球的最后一场）再也不会被拉取，`settle` 看到的仍是 `STATUS_IN_PROGRESS`。
- **即时修复**：手动 `service.ingest_date("2026-11-04")` + `app.cli settle` → 注单 #5 结算 over/under 正确，台账 6 结算 3胜3负 ROI +14.05%。
- **根治**：`app/jobs/watch.py` 主循环新增「pending-day sweep」——每轮查询有 `open` 注单且 `slate_date < 今日` 的历史日期，逐日补 ingest；结算收敛（该日无 open+final 残留）后从 `settle_pending` 列表移除，不重复请求 ESPN 完赛场次（次日比分已稳定）。
- 测试：`tests/test_watch_pending.py`（SQL 语义：历史进行中/完赛日被选中、当日被排除、已结算不触发）。**65 passed**。
- **git 推送稳定性**：`git push`/`fetch` 到 github.com 间歇性失败（`Connection was reset` / `Failed to connect ... Timed out`），诊断确认是到 `20.205.243.166:443` 的 TCP **间歇性丢包**（裸 TCP 3 连 2 超时 1 次 0.1s 连通；httpx/curl 均可复现同样抖动）。非配置问题：无代理、无 TLS 指纹问题、凭证正常。**处理方式：循环重试**（实测 1-8 次内必通）。
- **与云端 auto-refresh 分叉的标准操作**（每晚 2 次 SGV tick + 手动触发都会在远端追加提交）：
  1. `git fetch origin`（失败重试）
  2. `git stash push -- site/data/static.json`（若 working tree 有 watch 导出的 dirty）
  3. `git rebase origin/main`；`static.json` 冲突一律 `checkout --theirs`（云端数据更新）
  4. `git stash drop`（丢弃本地 stale 导出，watch 下轮会重新生成）
  5. `git push origin main`（失败循环重试）
  可用一行：`git pull --rebase origin main` + 数据文件冲突取 theirs。
- 提交：`2b696f8`（实时台账/信号体验）、`9af00da→09771dc`（僵尸结算修复）均已推送 GitHub；Actions 自动触发 refresh-and-deploy。

### 10.19 2026-11-05 赛后复盘板块（信号历史）

- **需求**：赛时页给已结束比赛加「赛后板块」：显示赛后数据 + 该场曾经出现过的信号。
- **数据源**：`predictions` 表保留每场全部历史版本（每场最多 171 行），每次 re-predict 的 `details.edges` 都在里面。「曾经出现的信号」= 全部历史版本中 `signal=True` 的边缘，按 (market, side, line) 去重合并。
- **后端**（`app/jobs/live.py`）：
  - `merge_signal_versions(rows)`：合并同一信号的多次出现——记录 `ev_max`/`edge_max`（历史最高）、`price_best`（曾出现的最优价）、`price_last`、`first_seen`/`last_seen`、`n_seen`、`bettable_ever`/`gated_ever`（曾可下注/曾被分歧闸门拦下）。
  - `signal_history(conn, game_id, home, away)`：SQL 用 `details LIKE '%"signal"%'` 预过滤 + 解析；完赛时用 `signal_backtest.settle_edge` 给每条信号附 `result`（won/lost/push）。
  - **缓存**：`_SIGNAL_CACHE` 按 game_id、TTL 600s、超 500 条清空——`/api/live` 每 30s 被本机+手机轮询，全量解析 JSON 太重。
  - `live_board`：state ∈ (post, in) 的比赛附 `signals_history`（仅 post 附结算结果）。
- **前端**（`live.js` + `styles.css`）：
  - `postBlock(game)`：赛后复盘卡片——赛前预测 vs 实际（分差/总分误差、主胜方向 ✓/✗）、纸面注单结算汇总（条数/盈亏/ROI）、信号 chips（市场 方向 线 @最优价 EV · 可下注/观察 · 命中/落空，悬停显示出现次数/书源/首见/最近时间）。
  - `settleSignal()`：JS 版结算（与 `settle_edge` 同逻辑），静态模式/本机快照在客户端结算。
  - `static.js espnLive`：静态模式用导出快照的 `prediction.edges`（signal=True）近似信号历史（无去重历史，命中结果客户端结算）。
  - CSS：`.post-block`/`.post-title`（🏁）/`.post-sig-title`/`.bet-chip.sig`（蓝边）。
- **测试**：`tests/test_post_signals.py` 5 例（去重合并、跨线分离、观察标记、完赛结算、无最终比分不结算）。**70 passed**。
- **验证**：`/api/live?date=2026-11-04` 14 场全部有 `signals_history`（如 MIN@HOU：ML 客 @4.01 EV+47.5% 出现 168 次 → 命中；CHI@NY 大小 U233.5 EV+12.3% 出现 114 次 → 落空）；浏览器 DOM 14 blocks / 28 sig chips ✅。
- **教训**：给 `live.js` 插函数时一次替换把 `function liveCard(game) {` 声明行吞掉，导致顶层裸语句 `Illegal return statement`——整个文件不执行且报错位置远离真因；`new Function(src)` 探测不出（Function 体允许 return），要用 `eval`/直接看 console。

### 10.20 2026-11-05 赛后复盘独立成板块

- **调整**：赛后内容从「赛时卡片内嵌」改为**独立 tab「赛后复盘」**（赛前看板 / 赛时 / 赛后复盘 / 收益 / 研究图表），避免用户切到赛时页却看不到（当天未完赛时容易误以为功能坏了）。
- **前端**：
  - `index.html`：新增 `#tab-post` 面板（状态栏 + `#post-stats` 汇总 + `#post-games` 卡片区 + 「跳到最近完赛日」按钮）；tabs 加 `data-tab="post"`。
  - `live.js`：`postBlock()` → `postCard()`（完整赛后卡片：队名/比分/分节/注单 chips/赛后复盘块）；新增 `loadPost()`（只渲染 state=post 的场次 + 顶部 8 个汇总指标：已结束场次、分差 MAE、总分 MAE、主胜方向、历史信号数、信号命中率、结算注单数、注单盈亏）；`jumpToLastFinished()` 改为跳到最近有完赛的日期并切到赛后 tab。
  - `research.js`：tab 切换器加入 `"post"` 面板与 `window.loadPost?.()` 钩子。
  - `styles.css`：`.post-card`/`.post-signals` 样式；统计卡用现有 `.stat`/`.stat .v.green`（无 `.pos/.neg`）。
- **后端不变**：`live.py` 的 `signal_history()` 仍随 `/api/live` 返回 `signals_history`（含完赛结算结果），赛后 tab 复用同一接口。
- **验证**：11-04 → 14 张赛后卡片 / 28 个信号 chip / 汇总「分差 MAE 9.77 · 总分 MAE 9.16 · 主胜方向 9/14 · 信号 28 条命中 14/28 (50%) · 注单 6 条盈亏 +2.95」；11-05 → 空状态提示 + 「跳到最近完赛日」可跳到 11-04。**70 passed**。
- **新增运维**：`scripts/serve-ctl.ps1`（serve 常驻控制 start/stop/status/restart）+ Startup 目录 `nbaq-serve.vbs` 开机自启。
  - **坑**：`pythonw.exe` 直接 Start-Process 且不重定向输出时，uvicorn 启动写 stdout 会因无效句柄崩溃（静默退出）；必须 `-RedirectStandardOutput/-RedirectStandardError` 到文件。
  - **坑**：用 `-RedirectStandardOutput` 管道启动的 serve 会随启动它的 shell 进程一起被杀（管道断裂 → 崩溃）。所以 serve 必须用 `serve-ctl.ps1` 的独立进程 + 文件重定向方式启动。

### 10.21 2026-11-05 视觉重构：记分台设计系统

- **设计定位**：不做"网页仪表盘"，做**NBA 记分台（scorer's table）**——冷调石墨控制台 + 纸白记分卡。主角是数字（分差/赔率/EV/MAE），视觉重量压在数字上。
- **色彩**（放弃近黑+酸绿）：
  | 角色 | 色值 |
  |---|---|
  | 记分台底 | `#101319`（冷调石墨，非纯黑） |
  | 面板 | `#191d26` |
  | 发丝界线 | `#2b313d` |
  | **记分纸** | `#e8e4d9`（暖白）+ 墨 `#171a20` |
  | 琥珀 | `#e8a33d`（进行中/观察） |
  | 记号笔绿 | `#46b95c`（赢/正值/可下注） |
  | 记录红 | `#e0574f`（输/负值/实时比分） |
  | 冷蓝 | `#4b8fd6`（引用数据：ESPN/盘口/历史信号） |
- **字体**（关键决策）：数字是这个软件的主要视觉纹理，必须有性格。
  - **数字/比分/赔率/图表刻度**：`IBM Plex Mono`（自托管 woff2，400/500/600）
  - **队名/标题**：`IBM Plex Sans Condensed`（600/700）——NBA 记分牌上球队名的排版逻辑
  - **正文中文**：系统栈（PingFang SC / 微软雅黑 / Noto Sans SC）
  - 字体文件放在 `app/web/fonts/`，`@font-face` + `font-display: swap`；已加入 `sw.js` 缓存清单（`nbaq-v21`）保证离线可用
- **布局**：
  - 卡片从「圆角胶囊 + 同款软阴影」改为**记分卡纸片**：4px 直角、发丝边、左缘 2px 状态色条（绿=信号 / 琥珀=进行中 / 纸白=已结束）
  - 统计条从「一堆独立圆角卡片」改为**控制台读数条**：单边框 + 内部发丝分隔 + 无 gap
  - 导航从「胶囊分段控件」改为**下划线分区**（active 绿下划线）
  - 表格/统计读数用 Plex Mono 等宽，成排数字自动对齐
- **记忆点（唯一）**：**纸白记分卡**——已结束比赛的最终比分渲染成暖白纸片（`#e8e4d9` 底 + 深墨字 + 细竖纹），像从记分台裁下来的一张纸。全站其他元素保持克制。
- **去掉的默认套路**：所有 `text-transform: uppercase` 眉标、全局 radial 渐变、卡片渐变底、同款圆角、软阴影、emoji 图标（🏁 改用细线分隔）。
- **图表配色**同步到新调色板（`research.js` COLORS + canvas 字体改 Plex Mono）。
- **代码修复**：`cmd_serve` 改用显式 `uvicorn.Config` + `uvicorn.Server` + `server.install_signal_handlers = lambda: None`——`uvicorn.run()` 在 pythonw（无控制台）下会等 stdin，读到 EOF 后进程自行退出，表现为「服务启动几秒后消失、无崩溃日志」。已复现并验证修复（pythonw 下 30s+ 稳定）。
- **验证**：DOM 检查通过（4px 直角、Plex Mono 数字、纸白记分卡 `rgb(232,228,217)` 底 + `rgb(23,26,32)` 字、卡片左缘纸白色条、14 张赛后卡 / 28 信号 chip 正常）；**70 passed**。
- **提交**：`ee89823`（rebase 后）。

### 10.22 2026-11-05 常驻服务的自愈机制（进程被宿主连带杀）

- **症状**：serve / watch 反复"莫名消失"。watch.log 里最后一行是正常的 `betssync`，没有崩溃栈。
- **根因（三层，逐层排查）**：
  1. **`uvicorn.run()` 在 pythonw（无控制台）下会等 stdin**，读到 EOF 后进程自行退出 → 改用显式 `uvicorn.Config` + `uvicorn.Server` + `server.install_signal_handlers = lambda: None`。
  2. **管道重定向**：用 `Start-Process -RedirectStandardOutput` 起的进程，日志管道跟着启动它的 shell 一起断 → 崩溃。必须用独立进程 + 文件重定向。
  3. **宿主 Job Object（真正的主因）**：IDE / 终端这类宿主会给子进程分配 Job Object 并设 `KILL_ON_JOB_CLOSE`——**宿主 shell 一结束，所有后代进程被连带杀掉**。表现为"我重启了一下服务，过一会儿它自己没了"。普通 `Start-Process` / `schtasks`（需管理员）/ WMI `Win32_Process.Create` 都无法避开。
- **解法**：`scripts/launch_detached.py` —— 用 ctypes 调 `CreateProcessW`，带 `CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW` 标志，真正脱离宿主 Job。
  - 注意：不能用 `STARTF_USESTDHANDLES` 传日志句柄——`bInheritHandles=True` 时 Windows 要求所有句柄可继承，Python 默认不可继承，会得到 `ERROR_INVALID_HANDLE(6)`。日志让子进程自己开文件。
  - 必须用 `python.exe`（不是 pythonw）——pythonw 无控制台时输出句柄无效会直接崩。
- **守护**：
  - `scripts/serve_watchdog.py`（60 秒一轮）：探测 serve 进程 + 8000 端口，异常就用 breakaway 重新拉起；日志 `data/serve-watchdog.log`。
  - `scripts/watch_guard.py`（120 秒一轮）：同样守护 `app.cli watch`；日志 `data/watch-guard.log`。
  - `scripts/startup.cmd` → Startup 目录 `nbaq-serve.vbs` / `nbaq-watch.vbs`：登录自启时拉起两个守护。
- **验收**：杀掉 serve → 62 秒内自动恢复（首页 200）；杀掉 watch → 120 秒内自动恢复；日志有完整记录。
- **另一条线索**：git 推送失败期间积累了一批僵尸 `git-remote-https` / `git-credential-manager` 进程（12:58 起），**锁死了凭据管理器**，导致后续所有 git 操作（包括 python 调 git）都被卡住中断。清理后 push 立即成功。→ 推送卡住时先 `Get-Process git*` 查僵尸。

### 10.23 2026-11-05 静态模式赛后复盘修复

- **症状**：手机（静态模式）在赛后复盘页点「跳到最近完赛日」报 **"静态模式不支持：/api/live?date=..."**。
- **根因**：
  1. `jumpToLastFinished()` 直接调 `api("/api/live?date=...")`，而 `static.js` 的 `staticApi()` **没有 /api/live 映射**（赛时/赛后数据在静态模式走 `espnLive()` 直连 ESPN）。
  2. 更深一层：静态导出原本**只含当前比赛日**的 `board.games`，跳到往期日期时没有赛前预测/盘口/信号历史 → 即使不报错，赛后板块也是空的。
- **修复**：
  1. `live.js::jumpToLastFinished()` 加模式分支：`APP_MODE === "static" ? espnLive(day) : api(...)`。
  2. 新增 `scripts/history_pack.py::build_history()`：导出最近 10 个比赛日的复盘包（每场含赛前预测/盘口线/信号历史+命中结果/最终比分），写进 `static.json` 的 `payload.history`（约 +43KB）。
  3. `static.js::espnLive()` 优先用 `history[date]` 的数据（预测/盘口/信号），没有该日期时才回退到当前 board 的 edges 近似。
- **验证**：静态模式注入 payload 后点跳转 → 到 2026-11-04，14 张赛后卡 / 26 个信号 chip / 汇总统计完整，无报错。`static.json` 从 132KB → 186KB（+43KB，手机可接受）。**70 passed**。
- **导出结果**：`history: 10 day(s)`（11-04 13 场、11-02 15 场、11-01 4 场…）。

### 10.24 2026-11-06 静态模式赛后复盘三处修正

1. **跳转竞态（真正导致"跳了但内容被覆盖"）**：切到「赛后复盘」tab 会先按当前日期触发一次 `loadPost`，用户立刻点「跳到最近完赛日」又触发一次——**旧请求（当前日期）后返回，把新日期的渲染覆盖掉**，表现为"跳转成功但显示的是原来那天/空白"。
   - 修法：`loadPost` / `loadLive` 加**请求令牌**（`postToken` / `liveToken`），每次加载自增，`await` 之后校验是否仍是最新一次，过期响应直接丢弃（含 catch 里的错误文案）。
2. **静态模式跳转太慢**：原来逐个日期请求 ESPN（手机上 9 秒）。改为直接用静态导出 `history` 的日期键定位（**0.6 秒**），API 模式再走逐日探测兜底。
3. **跳到没有预测的日期会看到空复盘**：DB 里 **2026-11-04 之前没有 prediction 记录**（模型从 11-04 才开始保存），那些日期只有比分没有分析。
   - `history_pack.build_history()` 只导出「至少有 1 场赛前预测」的日期（`history: 1 day`），`static.json` 从 186KB 降到 180KB。
   - 跳转优先选**真的有赛前预测**的日期；`loadPost` 遇到"有比分但无预测"的日期会明确提示原因，不再静默空白。
4. **守护进程单例判断 bug**：venv 的 `python.exe` 是 trampoline 启动器，**一个逻辑实例会派生两个命令行相同的进程**，按"进程个数 > 1"判断会永远误认为已有实例 → 守护启动即退出 → serve/watch 无人托管（正是"服务反复消失"的最后一环）。
   - 修法：改用**文件锁**（`msvcrt.locking` + `data/*.lock`），进程退出自动释放，语义准确。
- **验证**：竞态场景（点 tab 立刻点跳转）最终稳定渲染 11-04 的 14 张赛后卡 / 26 个信号 chip / 完整统计；跳转 539ms；单例锁实测第二次启动输出 `already running - exiting`。**70 passed**。

### 10.25 2026-11-06 赛中胜率模型（研究模块，不产生滚球信号）

- **定位**：只做**记录与校准研究**，不输出 edge、不进信号/可下注通道。目的有二：①把滚球页的"模型主胜"换成口径可复现的模型；②从今天起积累赛中轨迹，为将来与滚球盘口做校准对照打基础。
- **模型**（`app/models/live_wp.py`）：
  ```
  μ(t) = 分差 + w · 赛前预期 · (t/T)
  σ(t) = sqrt(σ_full² · (t/T)^(2γ) + σ_floor²)
  P(主胜) = Φ( μ(t) / σ(t) )
  ```
  - `t`：有效剩余秒（1–4 节为常规时间剩余；加时只算当前加时剩余时钟）
  - 参数 `w=1.0`（赛前预期完整带入，理论上与 live.py 投射口径一致）、`σ_full=24`、`γ=0.6`、`σ_floor=1.6`
  - `w` 固定不拟合：只有约 11% 的回合能取到赛前预测，网格会给出不稳定解（训练集选 0.0、全样本选 1.0）
- **标定**（`scripts/live_wp_fit.py`）：用 `pbp_plays` 的 60,905 个历史回合 / 120 场，按**比赛日做 70/30 时间切分**，网格搜索最小化 logloss。
  - 样本外：**logloss 0.4555 / Brier 0.1533**（恒定 50% 基线 logloss 0.6932）
  - 报告与参数写入 `data/live_wp_report.json` / `data/live_wp_params.json`（`data/*` 不入主仓，CI 每轮重算）
- **记录**：新表 `live_wp`（game_id/period/clock/比分/分差/剩余秒/赛前预期/胜率），`watch` 每轮给进行中的比赛写一行（`UNIQUE(game_id, period, clock)` 去重）。只记录，不推送。
- **API**：`/api/live-wp`（验收报告+参数）、`/api/live-wp/{game_id}`（该场轨迹）。静态模式降级为只返回报告。
- **页面**：研究图表页新增「赛中胜率模型 · 校准验收」卡片（样本内/样本外指标 + 10 档可靠性表 + 参数 + 偏差提示）；滚球卡片的「模型主胜」改用校准后的 `model_wp`。
- **诚实结论（重要）**：**逐回合数据高度相关，有效样本接近场次数（当前仅 120 场）**，分箱校准在样本外最大偏差 15.4pp，只能作方向性参考。已在报告和页面明写"需要累积 30 天以上比赛再评估"，不得用于任何决策。
- **测试**：`tests/test_live_wp.py` 7 例（开赛 50%、分差/时间单调、赛前预期衰减、加时口径、参数读写）。**79 passed**。
- **CI**：`refresh.yml` 在 backfill_pbp 之后新增 `python -m scripts.live_wp_fit || true`，保证 Pages 上的验收报告每日更新。

### 10.26 2026-11-06 滚球盘口快照（原始记录，不解释口径）

- **背景**：用户问能否解析雷速体育。评估结论：技术上可行（签名接口 + 设备指纹），但**维护成本高、违反其服务条款**，且**没有我们缺的数据** → 不做。
- **关键发现**：已集成的**好体育接口本身就带滚球盘口**，只是代码里 `inplay` 一直传 `false`。实测 `inplay=true` 返回 49 场进行中的比赛；单场 `match/simple?inplay=true` 返回完整滚球盘：
  ```
  1x2 / ah(亚盘) / ou(大小) / ah_2h / ou_2h / 1x2_2h /
  h-ou(主队得分) / a-ou(客队得分) / rptsio(得分区间) / oe(单双) / wtbo(加时)
  ```
- **实现**（`app/ingest/hao.py`）：
  - `fetch_inplay_matches()`：`inplay=true` + 过滤 NBA（cid=12, tid=7067）
  - `fetch_live_market(iid)`：`match/simple?inplay=true`
  - `store_live_odds(conn, slate)`：队名匹配（±1 天窗口，滚球可能跨东部零点）→ 原样写 `live_odds`
  - 表 `live_odds`：`game_id / book / iid / period / clock / score / market_raw(JSON) / detail_raw / captured_at`，唯一键 `(game_id, book, period, clock, score)` 去重
  - **不做语义解释**：滚球线的口径（全场 vs 从现在起、`absK` 含义）需要真实 NBA 样本验证后再解析，先存原始 JSON 保证数据不失效
- **接入**：`watch` 每轮（15 分钟）调用一次；新增 CLI `python -m app.cli live-odds` 手动抓取；`/api/live-odds/{game_id}` 返回原始快照。
- **与 `live_wp` 的配合**：两张表都带 `period/clock/比分/时间戳`，将来可精确对齐「同一时刻、同一比分下，模型说 X% vs 市场说 Y%」——这是判断滚球 edge 的唯一可靠方式。
- **测试**：`tests/test_live_odds.py` 5 例（原始字段完整保留、同钟同分去重、新时钟新增、无滚球、队名不匹配跳过）。**84 passed**。
- **现场状态**：当前没有 NBA 比赛进行中（只有 NCAA），所以 `live_nba=0`；NBA 开打时 `watch` 会自动开始记录。

### 10.27 2026-11-06 滚球盘口上赛时面板 + 发现并修复 collector.js 语法错误

- **滚球线口径已实测确认**（用进行中的 NBA 比赛样本，非推测）：
  ```
  Q1 04:58 · 19-17（主 +2）· 总分 36
  ou.k = 226.5  → 全场总分（含已得分），不是剩余分
  ah.k = -1.5 / -2.5 / -3.5，absK = -3.5 / -4.5 / -5.5
  三条均满足 absK = k - (主分 - 客分)  → k = 全场让分，absK = 从现在起的让分
  1x2 = 胜负（d 为占位，篮球无平局）
  ```
  第二场比赛再次验证：23-19（+4），k=-4.5，absK=-8.5 ✓
- **主盘选择**：多档盘口取「两侧赔付最接近平水」的一档（`_pick_main_line`）。示例：让分三档里 -2.5（1.81/1.95）最均衡；大小三档里 226.5（1.89/1.87）最均衡。
- **展示**：`app/jobs/live.py::live_market_snapshot()` 读该场最近一条 `live_odds`，`hao.parse_live_market()` 解析成 `{handicap,total,moneyline}`（港赔→欧赔），挂在 `/api/live` 的进行中比赛上；赛时卡片新增「滚球盘口」块，显示 `让分 -4.5 / 大小 225.5 / 胜负`、两侧价格、节次时钟与数据源。
- **仍不产生信号**：滚球盘口只做展示，不进入 signal/bettable 通道。
- **同时修复一个已在线上生效的严重 bug**：`app/web/collector.js` 在另一个会话的提交（`4c5f3aa`）里多了一层重复的 `if (oddsKey) {` 而少一个 `}`，**整个文件解析失败**（`Unexpected token ')'`），导致手机采集端完全不可用。已删除重复判断。
- **新增防护**：`tests/test_js_syntax.py` —— 轻量词法扫描（跳过注释/单双引号/模板字符串含 `${}` 嵌套/正则字面量），检查 `() [] {}` 配平；含正反用例（能捕获"少一个花括号"，且不误报字符串/注释/正则里的括号）。pytest 覆盖不到 JS，这类事故以后会在 CI 拦下。
- **测试**：`tests/test_live_market.py` 5 例（主盘选择、absK 关系、赔率换算、空市场）。**92 passed**。

### 10.28 2026-11-06 滚球盘口接入手机静态模式

- **问题**：`/api/live` 只有本机后端有；手机是静态模式（从坚果云/Pages 拉 `static.json`），看不到滚球盘。
- **做法**：
  1. `scripts/export_static.py`：导出时调用 `hao.store_live_odds(conn, slate)`（顺带归档），再对进行中的比赛写 `item["live_market"]`（复用 `live.live_market_snapshot()`）。
  2. `app/web/static.js::espnLive()`：把 `boardGame.live_market` 透传到 item → 赛时卡片复用同一个 `liveMarketBlock()` 渲染。
  3. `app/web/live.js`：没有赛前预测的进行中比赛也显示滚球盘（原来只显示"无法做投影"）。
- **刷新频率**：`odds-refresh.yml` 每 30 分钟跑一次 `export_static`，所以手机端最迟 30 分钟更新一次滚球盘。
- **验证**：静态模式实测渲染 `滚球盘口 · 好体育 · Q1 00:46 · 让分 -6.5（1.87/1.89）· 大小 226.5（1.85/1.91）· 胜负 1.40/3.15`；`absK = k − margin` 第三次独立验证通过（-15.5 = -6.5 − 9）。

### 10.29 2026-11-06 滚球盘口「记一笔」

- **交互**：赛时卡片的滚球盘每个方向都变成可点按钮（让分 主/客、大小 大/小、胜负 主/客），点击弹出**浮动面板**（`position: fixed`，不会被 30 秒自动刷新打断），显示：注额、可改赔率、模型概率、EV。
- **记录内容**：`market/side/line/price/stake` + `model_prob` + `ev` + 备注 `滚球 让分 主 -6.5 @1.95 · 好体育 q1 00:29`。
- **模型概率口径**（bets 表 `model_prob NOT NULL`，不能留空）：
  - 让分 → `live_cover.spread_home`（客队取 1 − 它）
  - 大小 → `live_cover.over`（小分取 1 − 它）
  - 胜负 → `model_wp`
  - 都缺失时记 0.5，并在面板上标注"模型概率缺失，按 50% 记录"
  - 面板与备注都写明「赛前模型投射，未经滚球验证」，避免把滚球注单误当成已验证信号
- **两种模式**：API 模式写 `bets` 表；静态模式走 `LB.add()` 写本机 `localStorage`（key `nbaq:bets:v1`），顺带存 `slate_date`/`matchup` 供台账分组。
- **验证**：实测点击「主 1.95」→ 面板显示 `让分 主 -6.5 · 模型 68.4% · EV +33.4%` → 确认后入库 `id=16 ... note='滚球 让分 主 -6.5 @1.95 · 好体育 q1 00:29'`（测试注单已删除）；静态模式实测写入 localStorage 成功，字段完整。**92 passed**。

### 10.30 2026-11-06 滚球「记一笔」支持自定义盘口

- **需求**：大小分（以及让分）要能改成自己实际看到的线。
- **实现**：面板新增「盘口」输入框（moneyline 不显示），默认取当前滚球线，步长 0.5。
- **关键点：改盘口会重算模型概率**（否则记录的 EV 是错的）。前端用与后端 `devig` 相同的公式：
  ```
  让分：cover = Φ((proj_margin + line) / σ_margin)
  大小：over  = 1 − Φ((line − proj_total) / σ_total)
  ```
  `proj_margin/proj_total/σ` 来自 `/api/live` 的 `live` 投射（静态模式由 `jsLiveProjection` 产出），Φ 复用 `static.js::jsNormalCdf`。面板会显示「（按盘口 220.5 重算）」。
- **实测**：同一条大小盘，盘口 226.5 → 模型 47.3% / EV −10.2%；改成 220.5 → 模型 62.3% / EV +18.3%，入库 `line=220.5, model_prob=0.6225, ev=0.1828`，备注 `滚球 大小 大 220.5 @1.90 · 好体育 q2 11:13`（测试注单已删除）。
- **兜底**：没有 `live` 投射时退回原始模型概率；模型概率缺失时按 50% 记录并在面板标注。

### 10.31 2026-11-06 注单按「赛前 / 滚球」分组统计

- **背景**：滚球注单的 CLV 口径是「相对下注时的滚球线」，与赛前 CLV（相对收盘线）不可混算，混在一起会污染赛前结论。
- **判定**：`app/jobs/settle.py::bet_kind()` —— 备注含「滚球」即算滚球注单（赛时面板下的注单备注固定以"滚球 ..."开头，见 `live.js`）。
- **后端**：
  - `summarize()` 增加 `by_kind = {"pregame": {...}, "live": {...}}`，每组含 settled/open/wins/losses/pushes/stake/profit/roi/hit_rate/CLV/分市场；顶层字段保持不变（向后兼容）。
  - `/api/bets/analysis` 的每条注单补 `kind` 字段。
  - CLI `report` 末尾输出「分场景：赛前 / 滚球」一行。
- **前端**：
  - 收益页新增「场景」对比表（已结算/未结算/盈亏/ROI/命中率/平均 CLV）；没有滚球注单时给一句提示。
  - 台账新增「场景」列：滚球用琥珀色 `滚球` 标签，赛前用灰字。
  - 静态模式（手机）在 `localbets.js` 里同步实现 `lbKind()` 与 `by_kind`，台账从备注推断（API 模式优先用 `kind` 字段）。
- **测试**：`tests/test_bet_kind.py` 3 例（分类、分组金额、与整体一致）。**95 passed**。
- **实测**：`/api/bets/summary` → 赛前 6 结算/2 未结算/盈亏 +2.95/ROI +14.0%；滚球 0 结算/1 未结算。收益页与台账显示正常。

### 10.32 2026-11-06 赛时页「刷新数据」按钮

- **问题**：勇士那场（GSW vs MIA，03:00Z 开赛）在赛时页没有滚球盘口。排查结论：**不是匹配或解析问题，是抓取时机**——watch 每 15 分钟才跑一轮，刚开赛那几分钟盘口还没入库。手动执行 `python -m app.cli live-odds` 立刻 `matched: 2, written: 2`，两场都有完整盘口。
- **新增接口** `POST /api/live/refresh?date=`：重新 `ingest_date`（比分/状态）+ `hao.store_live_odds`（滚球盘口）+ `live_wp.record_snapshot`（胜率轨迹），返回各项统计；单次约 7 秒。
- **前端**：赛时页标题栏新增「刷新数据」按钮，点击后按钮变「刷新中…」，完成后 toast 报告 `已刷新：2 场进行中，新增 N 条滚球盘口` 并重绘；失败时 toast 报错。
- **静态模式**：没有后端，只能重拉 ESPN 实时比分并把滚球盘口说明为「每 30 分钟随快照更新」；`refreshStaticPayload()` 用 try/catch 包住，避免快照通道故障时连带阻断比分刷新。
- **实测**：点击后两场都出现滚球盘（PHX Q3 07:34 让分 −12.5/大小 225.5；GSW Q1 01:07 让分 −1.5/大小 228.5），toast 正确。**95 passed**。

### 10.33 2026-11-06 滚球封盘处理

- **实测到的封盘特征**（99-75 第四节垃圾时间那条快照）：
  ```json
  ah: [{"k":"-20.5","h":"0.00","a":"0.00"}]
  ou: [{"ov":"0.00","ud":"0.00","k":"220.5"}]
  "1x2": null,  ml: {"h":"0.00","a":"0.00"}
  ```
  → **两侧价格全为 `"0.00"`、`1x2` 变 null**。同一时刻另一场仍是正常赔率，可确认是封盘而非接口故障。
- **解析判定**（`hao.parse_live_market`）：
  - 有市场结构（`ah/ou/1x2/ml` 键有值）但无任何有效价格 → `suspended: true`
  - 完全没有市场键 → `empty: true`（视为"本来没有"，不是封盘）
  - 部分市场有价、部分无价 → `partial: true`（如让分还在、大小已停）
  - 单档一侧为 0 的档位会被主盘选择跳过（`_pick_main_line` 只取两侧有效的档）
- **数据新鲜度**：`live_market_snapshot` 增加 `age_seconds` 与 `stale`（>300 秒），因为滚球价格变动极快，过期价格不能当作可成交价。
- **封盘时回退显示**：沿最近 60 条快照向上找，附 `last_valid`（上一次有价格的盘口 + 它的节次/时钟），面板显示"（上次有效）"并灰显，避免整块空白。
- **交互保护**：`suspended` 或 `stale` 时**不渲染「记一笔」按钮**，只展示数字；注释说明原因。这样不会把 0.00 当成赔率记进台账、也不会用过期价格下注。
- **分析层**：`/api/live-odds/{game_id}` 每条快照附带 `suspended` / `has_market`，后续做"模型 vs 市场"对照时可以过滤掉封盘样本。
- **实测**：真实数据里 ORL@PHX（Q4 大比分）确实处于封盘，面板显示 `封盘中` + 上次有效 `Q3 07:34 让分 -12.5`，`lm-bet` 按钮数为 0。**98 passed**。

### 10.34 2026-11-06 `live_odds.suspended` 列 + 历史注单回填

**1. 封盘标记落库**
- `live_odds` 新增 `suspended INTEGER`；`_migrate` 自动 `ALTER` 并**回填历史行**（逐行解析 `market_raw`，失败置 NULL）。
- `store_live_odds` 写入时就算好：价格全 0 / 市场消失 → `1`。
- `live_market_snapshot` 找「上次有效」改成直接 `WHERE COALESCE(suspended,0)=0`，不再逐条重新解析 JSON（原来最多回溯 60 条）。
- `/api/live-odds/{game_id}` 直接带 `suspended`，分析可 `WHERE suspended=0` 过滤。
- 迁移实测：17 条正常 + 1 条封盘，标记正确。

**2. 历史注单回填**（`scripts/backfill_bets.py`）
- 遍历有终局比分的预测，取**每个 (game, market, side, line) 首次出现的 signal=True**（模拟当时下注），用信号时刻可拿到的报价（`_entry_snapshot`），拿不到才退回信号价。
- 用 `settle_edge` 立即结算，CLV/线移动按赛前收盘价计算。
- 备注统一 `回填 赛前信号 · <book> · EV … · <日期>`，**与真实决策区分**。
- 参数：`--stake`（默认 1）、`--start/--end`、`--dry-run`；重复运行会跳过已存在的注单（幂等）。
- **首次回填结果：21 条 · 10 胜 11 负 · 盈亏 −1.86 · ROI −8.9% · 命中 47.6%**（与此前 signal_backtest 的负 ROI 结论一致）。

**3. 注单分三类统计**
- `bet_kind()` 扩展为 `pregame / live / backfill`（备注含「滚球」→ live，含「回填」→ backfill）。
- `summarize.by_kind` 三组；收益页对比表变成「赛前（跟信号）/ 滚球 / 历史回填」三行，并注明回填是回顾性数据、不能当成绩。
- 台账「来源」列三种标签：赛前（灰字）/ 滚球（琥珀）/ 回填（冷蓝）。
- 静态模式 `localbets.js` 同步。

**4. 测试**：`tests/test_backfill_bets.py` 4 例（只回填一次、用当时报价、幂等、dry-run、日期过滤）+ 封盘入库 2 例 + `bet_kind` 回填分类。**104 passed**。

### 10.35 2026-11-06 回填口径收紧 + 赛前看板手动「记一笔」

**1. 回填 21 条 → 11 条（口径修正）**
- 原口径按 `(market, side, line)` 各回填一次，导致 9 场比赛膨胀成 21 条：
  - **盘口漂移被当成不同信号**（UTAH@NO 大小 232.5 + 232.0；MIN@HOU 让分 8.5 + 9.5；CHI@NY 大小 234.5 + 233.75）
  - **同场让分 + 胜负同时下注**（高度相关 = 双倍仓位，正是 `MUTUAL_EXCLUSIVE` 闸门要防的）
- 新口径（默认 `strict`）：
  - 同一市场只取**首次出现**的信号（盘口漂移视为同一决策）
  - 同场让分/胜负二选一，保留 EV 更高的（与 `predict.MUTUAL_EXCLUSIVE` 一致）
  - 每场最多 2 条（让分或胜负 + 大小）
- 结果：**11 条 · 5 胜 6 负 · 盈亏 −1.31 · ROI −11.9%**（另有 3 条与真实注单重复被跳过）。
- 新增 `--raw`（旧口径）、`--replace`（先删旧回填再重填）；`replace` 只删备注以「回填」开头的记录。

**2. 赛前看板「记一笔」**
- 每张**未开赛**卡片底部新增「记一笔」按钮（已开赛/完赛不显示，进行中走滚球面板）。
- 面板：市场（让分/大小/胜负）+ 方向 + 盘口 + 赔率 + 注额；切换市场自动带出对应默认盘口（来自预测的 `lines`），改盘口/赔率实时重算模型概率与 EV。
- 模型概率：胜负用 `home_win_prob`；让分/大小用与后端 `devig` 相同的正态近似（`jsNormalCdf`）。无预测时按 50% 记录并提示。
- 备注 `手动 大小 大 218.5 @1.95 · 2026-11-04`，与「滚球」「回填」区分，仍归入赛前组。
- **实测**：点按钮 → 切大小分 → 改 218.5 → 模型 76.6% / EV +49.3% → 入库正确（测试注单已删除）。**106 passed**。

### 10.36 2026-11-06 注单按「真实 vs 模拟」分开统计

- **口径**（用户确认）：
  - **我的注单（真实）** = 跟信号记一笔 + 手动录入 + 滚球
  - **信号模拟** = `backfill_bets.py` 回填的回顾性注单
- **后端**：`settle.bet_source()`（回填 → `sim`，其余 → `mine`）；`summarize()` 新增 `by_source`（保留原有 `by_kind` 细分）；`/api/bets/analysis` 每条带 `source`。
- **前端**：
  - 收益页对比表改成两层：主行「我的注单（真实）」+ 缩进明细（赛前/滚球）+ 主行「信号模拟（历史回填）」；下方一句话说明两者口径不同、模拟不是成绩。
  - 台账「来源」列：`真实·赛前`（灰）/ `真实·滚球`（琥珀）/ `模拟`（冷蓝）。
  - 静态模式 `localbets.js` 用 `lbMerge()` 把赛前+滚球合成 `mine`，回填为 `sim`。
- **当前数据**：真实 6 结算/3 未结算 · 盈亏 +2.95 · ROI +14.0%；模拟 11 结算 · 盈亏 −1.31 · ROI −11.9%。**108 passed**。

### 10.37 2026-11-06 台账分区 + 自定义记一笔

- **台账分区**：注单台账拆成两块独立表格，各自带小计：
  ```
  我的注单（真实）      9 条（结算 6）· 盈亏 +2.95 · ROI +14.0%
  信号模拟（历史回填）  11 条（结算 11）· 盈亏 -1.31 · ROI -11.9%
  ```
  上方还有一句提示说明模拟不是成绩。列的字段不变（比赛日/比赛/来源/市场/方向/盘口/赔率/注/EV/结果/盈亏/CLV/线移动）。
- **自定义记一笔**：收益页工具栏新增「＋ 自定义记一笔」（两种模式都有）。
  - 面板：**比赛下拉**（当前日期的全部场次，已结束的标注）+ 市场 + 方向 + 盘口 + 赔率 + 注额。
  - 切换比赛/市场自动带出该场预测的默认盘口；改盘口/赔率实时重算模型概率与 EV（与 devig 同式）。
  - 备注 `自定义 大小 大 215.5 @1.91 · 2026-11-04`，归类为「真实 · 赛前」（不是模拟）。
  - 静态模式同样可用（写入本机 localStorage）。
- **实测**：14 场下拉、切到大小分自动带 215.5、模型 60.1%/EV +14.7%、入库正确（测试注单已删除）。**108 passed**。

### 10.38 2026-11-06 赛中自适应采样（900s → 120s）

- **问题**：watch 固定 900 秒一轮，一场 NBA 比赛只能采到 6~8 个点，滚球轨迹和盘口快照太稀疏，做不了校准。
- **做法**：`app/config.py` 新增 `LIVE_INTERVAL`（`NBAQ_WATCH_LIVE_INTERVAL`，默认 120s）。watch 每轮查 `games` 里是否有 `STATUS_IN_PROGRESS/STATUS_HALFTIME`：
  - 有比赛 → 下一轮 **120 秒**
  - 没有 → 回到常规 `WATCH_INTERVAL`
  - 状态切换时才打印日志（避免每轮刷屏）
- **效果**：单场采样点从 ~7 个提升到 ~50 个，`live_wp` 与 `live_odds` 的时间序列可用于校准。
- **顺带验证跨日**：slate 翻到 2026-11-06 后，pending-day sweep 把 11-05 的 3 条 open-but-final 注单补拉并结算，当前未结算注单为 0。

### 10.39 2026-11-06 统计卡与累计曲线按真实/模拟分开

- **顶部统计卡**：`#bets-stats` 拆成两组（`我的注单（真实）` / `信号模拟（历史回填）`），每组各自显示 已结算(胜/负/走)、盈亏、ROI、命中率、平均 CLV、CLV 为正、平均线移动、未结算。没有 `by_source` 的旧快照回落到原来的单组渲染。
- **累计收益曲线**：`/api/bets/analysis` 新增 `curves: {mine, sim}`（`curve` 保留为整体，向后兼容）。前端新增 `research.js::drawMultiLineChart()`：
  - 日期取并集、缺失日沿用上一个累计值；
  - 绿色=真实、蓝色=模拟，带图例和 0 轴虚线；
  - 点数多时自动抽稀 x 轴标签。
- **实测**：真实 10 结算（3 胜 7 负，−4.05，ROI −14.5%，命中 30%）/ 模拟 11 结算（−1.31，−11.9%）；画布已绘制（非空像素抽样 10 处），标题改为「累计收益 · 真实 vs 模拟」。**108 passed**。

### 10.40 2026-11-06 累计曲线支持「归一化 / 金额」切换

- **为什么**：真实注单注额不固定（1/2/5），直接累计金额会被仓位结构扭曲，不能公平地和模拟（固定 1 单位）比较。
- **后端**：`/api/bets/analysis` 新增 `curves_unit`，把每笔盈亏换算成「每注 1 单位」再累计（`profit / stake`），保留 4 位小数；`curves`（金额）与 `curve`（整体）不变。
- **前端**：
  - 曲线标题右侧加分段按钮 `归一化 | 金额`（`#profit-mode`），选择存 `localStorage["nbaq:profit-mode"]`，默认**归一化**。
  - `bets.js` 抽出 `renderProfitChart()`，切换时只重绘画布，不重新请求。
  - `drawMultiLineChart()` 支持 `unit` 参数（归一化时 y 轴标 `u`），并按数值跨度自动用 1/2 位小数；x 轴改用完整日期排序、显示时再截断，修掉跨年排序隐患。
  - 静态模式：`static.js::lbAllAnalysis()` 也返回 `curves` / `curves_unit`，`window.LB` 新增 `kind` / `source`；`localbets.js::lbCurve(bets, normalize)` 同步支持归一化。
- **实测**：金额口径 真实 −4.05 / 模拟 −1.31；归一化后 真实 **−3.38u** / 模拟 **−1.31u**（模拟全是 1 单位注，所以两种口径几乎相同，验证了换算正确）。动态与静态模式切换均正常、无控制台错误。**108 passed**。

### 10.41 2026-11-06 ntfy 只推送通过可下注阀门的信号

- **旧行为**：`collect_signal_items()` 只检查 `edge["signal"]`，于是软书价、共识不足、分歧闸门拦下、同场相关降级的**观察项**也会被推成"正 EV 信号"。
- **新行为**：默认 `bettable_only=True`，必须同时满足 `signal`（模型算出正 EV）与 `bettable`（价格真实可拿 + 共识够深）。
  - 推送标题改为「N 个可下注信号」；文案首行加 `✅ 可下注`，并附最优价书源 `（DraftKings）`。
  - `watch` 每轮打印 `notify: X 个信号，Y 个通过可下注阀门（其余仅观察，不推送）`，被压下的数量可见。
  - 需要统计观察项时传 `bettable_only=False`（CLI 默认仍只推可下注）。
- **实测（11-06）**：7 个 signal、**0 个 bettable** → ntfy 静默。原因全是「共识书源数 < 3」（今天只有 DraftKings + hao-tiyu 两个源）。11-04 有 271 个 bettable（多书源日），说明阀门本身可放行，不是写死不放。
- **取舍**：宁可少推不可假推。想让推送更活跃可调 `NBAQ_BETTABLE_MIN_BOOKS=2`（默认 3），但会重新引入浅共识假 edge。
- **测试**：新增 `tests/test_notify_bettable.py`（只收 bettable / 原始模式含观察项 / 文案含可下注与书源 / 去重幂等）。**112 passed**。

### 10.42 2026-11-06 僵尸状态修复 + 采样间隔只看当日

- **发现**：11-04 有一场比赛（`401909935`，116-114）跨 slate 后没人再拉，状态永远停在 `STATUS_IN_PROGRESS`。watch 的「有比赛进行中」查询没有限定日期，于是采样间隔被永久压在 120s，全天高频刷接口。
- **修复**：
  - 新增 `watch.has_live_games(conn, slate)`，只查**当天**是否进行中。
  - 新增 `watch.stale_slate(conn, slate, days=7, min_age_minutes=60)`：取 7 天内最早一个仍有未完赛比赛的日期；用该日 `games.updated_at` 做节流，1 小时内不重复补拉（常驻 watch 与 CI 的 `watch --once` 都安全）。
- **实测**：重启后立刻 `repair stale slate 2026-11-04`，该场已变为完赛；全库不再有 `IN_PROGRESS` 行，采样回到 900s（今晚开赛再自动切 120s）。
- **测试**：`test_watch_pending.py` 新增 `has_live_games` 跨日期忽略、`stale_slate` 窗口与节流三个用例。**115 passed**。

### 10.43 2026-11-07 架构切换：Actions 主跑 + 云主机只做 serve

> 来自另一台机器（`nba-quant-bot`）的 8 个提交，本机已 `git pull` 同步。

- **新架构**：
  - GitHub Actions 恢复为主数据源：`odds-refresh` 每 30 分钟 `watch --once`，`refresh` 每天 02:00/08:00 UTC 全量。
  - 云主机（`deploy/`）只常驻 `serve` + 看门狗；watch/guard 不再常驻，避免和 CI 抢数据、重复推送。
  - 静态站发布到 `gh-pages`，`site/` 不再进 main（已加入 `.gitignore`）。
  - The Odds API 额度闸门在 state 文件缺失时回退读 `odds_snapshots` 时间（CI 每次只还原 nba.db）。
- **本机处理**：停掉本地 `watch` + `watch_guard`（避免第三个数据源重复推送/抢额度）；`serve` 保留用于本机查看；`data/odds_api_state.json`、`data/hoops_state.json` 变为未跟踪的运行时文件并保留。
- **注意**：本机 `data/nba.db` 不会自动更新；要最新数据用 `cmd /c "git show origin/data:nba.db > data\nba.db"`（先备份）。`site/` 需要本地静态站时跑 `python -m scripts.export_static --out site`。
- **修复（11-07 09:4x）**：`refresh.yml` 里还留着旧步骤 `Commit static site + API state`（`git add site data/odds_api_state.json data/hoops_state.json`）。`site/` 与两个 state 文件现在都被 `.gitignore` 忽略，`git add` 直接报错 → 后续 `Persist database to data branch` 被跳过。表现：`refresh-and-deploy` 每天 02:00/08:00 UTC 失败、`data` 分支停在旧快照，但 30 分钟的 `odds-refresh` 仍成功（Pages 数据还在）。已删除该步骤，发布只走 Pages artifact。

### 10.44 2026-11-07 本机开启「静默采样」

- **目的**：CI 每 30 分钟（实际 50~80 分钟）一个滚球样本，太稀；本机只做采样，不当发布者。
- **`.env` 改法**（备份在 `data/backup/.env.bak-20261107`）：
  ```ini
  NBAQ_ODDS_API_KEY=
  HOOPS_API_KEY=
  NBAQ_NTFY_TOPIC=
  NBAQ_WEBDAV_USER=
  NBAQ_WEBDAV_PASS=
  NBAQ_WATCH_INTERVAL=900
  NBAQ_WATCH_LIVE_INTERVAL=120
  ```
- **效果**：无比赛 15 分钟一轮；检测到进行中自动切 **120s**。只写本地 `live_odds` / `live_wp`，不推送、不覆盖坚果云、不耗 Odds API / HoopsAPI。
- **实测**：启动即 `notify=off`，7 场进行中 → `live detected → interval 120s`；01:54 与 01:57 两批快照，间隔约 2.5 分钟；日志出现「未配置 NBAQ_ODDS_API_KEY，跳过」「hoops skipped: 429」。
- **代价**：本机预测的共识书源更少（没有 Odds API），Edge/EV 会与 Pages 不一致；数据不会进 `data` 分支。
- **停止**：`powershell -File scripts\watch-ctl.ps1 stop`（未启 watch_guard，不会自动复活）。

### 10.45 2026-11-07 本机看板读取 GitHub 台账（只拉不推）

- **目标**：本机也当看板，显示 CI 结算后的注单，但绝不反向污染云端。
- **`betssync.run(pull_only=True)`**：
  - 只拉 `nbaq/bets.json` + **只读合并** `nbaq/inbox/*.json`（手机导出的片段）；
  - inbox 文件**不 MOVE**（留给 CI，否则 CI 永远看不到）；
  - 跳过 `ensure_folders`、跳过回推、跳过 static 推送。
- **单调更新 `merge_rows`**：同 ref 的云端注单若已结算（won/lost/push），可补齐本地 `open` 的 result/profit/收盘线/CLV；反向（已结算 → open）拒绝。返回新增 `updated` 计数。
- **watch**：改为 `betssync.run(conn, pull_only=True)`；不再每轮跑 `export_static`（本地看板走 API，不需要 site/）。CI 的静态站/台账推送由 workflow 里独立的 `bets-sync --push-static` 负责。
- **本机 `.env`**：恢复 `NBAQ_WEBDAV_USER/PASS`（只读用），`NBAQ_NTFY_TOPIC`、`NBAQ_ODDS_API_KEY`、`HOOPS_API_KEY` 仍为空。
- **手动刷新本机台账**：`python -m app.cli bets-sync --pull-only`（绝不回推）。
- **校验**：本地 21 条 = 云端 21 条，result/profit 零不一致；`/api/bets/analysis` 显示 已结算 21 / 盈亏 −5.36（真实 10 条 −4.05，模拟 11 条 −1.31）。
- **测试**：新增 `tests/test_betssync_pull_only.py`（只拉不推/只读合并 inbox/不 MOVE/不建目录；正常模式仍会 MOVE + 推），`test_betssync.py` 增加单调更新用例。**117 passed**。

### 10.46 2026-11-07 赛时数据时效：自动跟进快照 + 如实标注快照年龄

- **问题**：`gem08166.github.io/nba-quant` 赛时页更新慢。根因不是页面，是**发布频率**：
  GitHub 对公开仓库的 `schedule` 有节流，`*/30` 实测被压到 **约 60 分钟一轮**（03:15 / 02:15 / 18:42 / 17:41…）。
  赛时页面只在打开时读一次静态快照，之后 30s 轮询只重拉 ESPN 比分 → 盘口可能一小时不动，还显示「更新于刚刚」。
- **前端（`static.js` / `live.js`）**：
  - 赛时页每 **60s** 自动跟进快照：走 Pages **条件请求**（`If-None-Match`，304 几乎零流量），不再要求手动点「刷新」；
  - `adoptPayload()` 单调采纳：坚果云 / Pages / APK 三通道版本可能不同，**绝不用更旧的快照覆盖更新的**；
  - 手动「刷新」仍坚果云优先（最新），再回落 Pages；
  - 状态栏如实显示：`比分实时（ESPN）· 盘口快照 N 分钟前`，超过 20 分钟追加「（GitHub 定时任务延迟）」；
  - 无快照时给出明确文案，不再假装实时。
  - 注意：比分/分节比分/胜率曲线本来就走 ESPN 实时接口（约 1s），慢的只有滚球盘口快照。
- **CI**：`odds-refresh.yml` 的 cron 从 `*/30` 改 `*/10`（每次 60-80s，公开仓库不占额度；Odds API 仍有 `NBAQ_ODDSAPI_MIN_HOURS=20h` 闸门，节奏加快不会多耗）。
  GitHub 仍可能压到 20-60 分钟；要做到 5-10 分钟必须用**外部定时器**调 `workflow_dispatch`：
  ```bash
  curl -X POST -H "Authorization: Bearer <PAT>" \
    -H "Accept: application/vnd.github+json" \
    https://api.github.com/repos/gem08166/nba-quant/actions/workflows/odds-refresh.yml/dispatches \
    -d '{"ref":"main"}'
  ```
  PAT 只需 `actions:write`，放到 cron-job.org / UptimeRobot 之类的免费定时器里即可（10 分钟一次）。
- **静态快照年龄来源**：`static.json.generated_at`（UTC），前端按本地时间算差值。

### 10.47 2026-11-07 滚球盘口的真实瓶颈：CI 被好体育 403

- **实测**：GitHub Actions 的 `watch --once` 日志：
  `live_odds skipped: all hao mirrors failed: 403 Forbidden`；`data` 分支快照里 `live_odds` 表 **0 行**。
  即**赛时盘口从来不是「更新慢」，而是 CI 根本抓不到**（本机 IP 可以，Actions 的 IP/ASN 被拦）。
- **修复思路**：本机是唯一能抓滚球盘口的地方 → 本机采、两路分发：
  1. **手机快速通道**：本机每轮（赛中 120s）把 `nbaq/live.json`（只含 `markets{game_id: live_market}`，1-2KB）推到坚果云；
     前端 `static.js::refreshLiveOverlay()` 每 60s 拉取，按 `game_id` 覆盖盘口；
     只覆盖「captured_at 不更旧」的盘口，并按 `captured_at` 在客户端重算 `age_seconds/stale`（本机停推后 5 分钟自动变「已过期」，不会假装新鲜）。
  2. **Pages 通道**：本机 watch 在赛时每 `NBAQ_CI_TRIGGER_MINUTES`（默认 10）分钟用 `workflow_dispatch`
     触发一轮 CI（不受 schedule 限流）；`scripts/export_static.py` 导出前从坚果云拉 `nbaq/live.json`
     合并进 `board.games[].live_market`（>15 分钟视为过期、丢弃）。CI 的 static.json 再推 Pages + 坚果云。
- **令牌**：`NBAQ_GH_TOKEN`（classic `repo` 或 fine-grained `actions:write`），只存在本机 `.env`；
  `scripts/trigger_refresh.py` 是独立 CLI（`python -m scripts.trigger_refresh --dry-run`），watch 复用它的 `trigger_workflow()`。
  失败只打印、不中断采样；`workflow_dispatch` 的用户是仓库所有者，CI 内该环境变量为空 → 不会自我触发。
- **掉线语义**：本机关机/断网 → 15 分钟后 Pages 的盘口被导出逻辑丢弃，手机端 5 分钟后标记过期；
  比分仍由 ESPN 实时提供，页面不会显示"假新鲜"。
- **验证**：本机日志 `live overlay -> webdav: 2 game(s)`、`ci trigger: 已触发 odds-refresh.yml`；
  `nbaq/live.json` 的 `generated_at` 与本机采样同步；`export_static` 打印 `live overlay merged: 2 game(s)`。
- **顺带修复**：分离进程（`scripts/launch_detached.py`）启动 `serve/watch` 时 `sys.stdout=None` 导致秒退——
  `app/cli.py::_ensure_std_streams()` 兜底到 devnull，`tests/test_cli_streams.py` 回归。

### 10.48 2026-11-07 注单导出/导入：同一注单只落一次

- **目标**：手机导出的 JSON、电脑导出的 JSON、台账 JSON 互相导入时，重复注单绝不产生第二条。
- **身份判定（两级）**：
  1. `ref`（数字 id 归一成 `srv-<id>`，与 `bets_payload` 一致；手机本地注单补 `ref=id`）；
  2. 自然键 `natural_key()` = `game_id | market | side | line(3位小数) | placed_at(UTC秒)`。
     换了 ref、毫秒 vs 时区写法、`-2.5` 字符串 vs 浮点，都认得出是同一条。
- **合并规则（单调，绝不降级）**：
  - open ← 已结算：覆盖 result/profit 并补收盘线/CLV；
  - 已结算 ← 已结算（结果相同）：只补空缺的 closing/clv/profit；
  - 已结算 ← open：跳过；
  - 同一批文件里的重复行、无效行：分别计入 `duplicates`/`invalid`。
- **入口**：
  - API：`GET /api/bets/export`（`{schema:"nbaq.bets/1", exported_at, count, bets}`）、
    `POST /api/bets/import`（接受裸数组 / `{bets:[…]}` / `{rows:[…]}`）；
  - CLI：`bets-export --out PATH`、`bets-import --file PATH [--dry-run]`；
  - 前端：收益页「导出台账 / 导入台账」在**两种模式都可用**（API 模式走后端，
    静态模式走 localStorage `LB.importRows`），导入后 toast 明确显示
    「新增 X · 合并 Y · 跳过重复 Z」。
- **导出包容错**：`bets_payload` 的 games/teams JOIN 改 LEFT JOIN——服务端还没 ingest
  那场比赛时注单也导得出（matchup 回退为 game_id），不再整批丢失。
- **测试**：`tests/test_betssync_dedupe.py` 9 个用例（同文件幂等/自然键跨 ref/单调升级/
  已结算补 CLV/批内去重/无效行/包装对象/round-trip/未 ingest 比赛），
  `test_betssync.py` 断言更新；pyproject 显式 ignore 坚果云「冲突」副本。
  **140 passed**。
- **冒烟**：`bets-export`（30 条）→ `bets-import` × 2 全部 `added=0, duplicates=30`；
  `POST /api/bets/import` 同样幂等。

### 10.49 2026-11-07 台账「— / 裸 game_id」修复：缺失 slate 补 ingest

- **现象**：台账里 5 条注单显示「比赛日 — · 401909937」。
- **根因**：这些比赛（401909936 PHX@ORL、401909937 GS@MIA）属于 **2026-11-05 slate**，
  而 11-05 那天 CI 的 data 分支持久化正好坏掉（09:39 才修好），整个 slate 从未入库。
  注单还在（导出/导入链路没丢），但 `bets LEFT JOIN games` 全是 NULL → 日期/对阵都显示不出来。
- **即时修复**：按注单 `placed_at` 推断缺失日期，`ingest_date('2026-11-05')` 补拉（2 场，皆 FINAL）。
  注单与比赛重新接上；`bets-sync --pull-only` 后云端 `ledger_updated=5` 同步。
- **长效兜底（`watch.py`）**：新增 `_missing_slate_days()` / `_repair_missing_slates()`——
  每轮检查「有注单但 games 缺行」的比赛日并补 ingest。现有 pending-day 扫描只看已入库
  比赛（JOIN games），盖不住这种缺行情况。CI 的 `--once` 也会走这一兜底。
- **测试**：`tests/test_watch_missing_slate.py`；**142 passed**。

### 10.50 2026-11-07 台账按比赛日分组 + 注单编辑

- **台账分组**：`renderLedger` → `ledgerByDate()`——真实/模拟两大节内按比赛日分组
  （日期倒序，组内按记录时间倒序），组头显示日期 + `结算数/条数 · 盈亏小计`；
  没有日期的归「未知日期」。表格去掉重复的比赛日列。
- **注单编辑**：每行末尾「编辑」按钮 → 浮动面板（与记一笔同款交互）：
  - 未结算：可改 市场/方向/盘口/赔率/注额/备注/结果（market-side 联动校验）；
  - 已结算：只允许 结果/盈亏/注额/备注（盘口/赔率锁定，保护结算口径）；
  - 手动标记结果没给盈亏时按 注额×赔率 自动算；改注额按比例重算已结算盈亏；
  - 改回「未结算」清掉 closing/CLV；删除仅限未结算；
  - 身份：API 行用主键 `id`（`ref` 的 `srv-<n>` 只是别名——手机合并来的注单
    ref 数字 ≠ 行 id，不能当主键）；静态模式本地注单直接改 localStorage，
    服务端注单静态只读（等快照覆盖）。
- **API**：`PATCH /api/bets/{id}`（`BetPatch`，服务端字段级校验 + 单调保护）；
  `DELETE` 已有（仅 open）。
- **修复**：编辑面板在「已结算服务端注单」上崩掉（be-delete 按钮不渲染时
  `addEventListener` 直接 TypeError，save/cancel 也没挂上）→ `?.addEventListener`。
- **测试**：`tests/test_bet_edit_api.py` 9 用例（临时库 + `dependency_overrides`，
  不碰真实 DB）。**151 passed**。
- **端到端**：本地 serve 验证 分组渲染 / 面板打开 / stake 2.0→3.0（profit 2.85 自动
  重算）→ 保存 → DB 落库 → 再改回。

### 10.51 2026-11-07 注单删除：未结算 confirm、已结算输入确认文本

- **后端**：`DELETE /api/bets/{id}` 加 `?force=true`——未结算直接删；
  已结算需要 force（破坏历史样本要显式确认），返回 `deleted_ref` 便于审计。
- **前端**：编辑面板的「删除」按钮现在**所有可定位的注单都显示**（红色 danger 样式）：
  - 未结算 → 普通 `confirm()`；
  - 已结算 → `prompt()` 要求输入「删除」两字才执行（防误触，注明影响统计）；
  - 无身份的旧导出注单仍不可删。
- **静态模式**：本地注单直接删 localStorage；服务端注单只读（删除也被拦，
  等下一轮快照自然恢复——静态快照是权威）。
- **验证**：`test_delete_open_only` 更新（400 → force 200）；本地实测删除
  已结算 #20：prompt 文案正确、30→29 条、DB 确认消失。**151 passed**。

### 10.52 2026-11-07 编辑面板盈亏实时计算

- **问题**：编辑注单时改注额/赔率/结果，盈亏框不会跟着算，容易存出口径不一致的数据。
- **修复**（`openBetEditPanel`）：
  - 盈亏框下方加实时提示：`按公式应为 +2.85 · 与你填的一致`；手填了不一致的值会显示
    `你填的差 +4.00`（防止误存）；
  - 盈亏**留空时**改注额/赔率/结果 → 自动回填计算值（所见即所得）；
  - 保存时盈亏留空 → 前端直接带上公式值（`stake×(price−1)` / `−stake` / `0`），
    与后端口径一致，不再依赖后端兜底。
- **验证**：面板实测改注额 3.0 → 自动 +2.85；结果改 lost → 自动 −3.00；
  手填 1.00 提示差值 +4.00。**151 passed**（纯前端改动，API 无变化）。

### 10.53 2026-11-07 复盘页信号标注：可下注 vs 阀门拦下的观察项

- **核实结论**（11-04 至今 39 组聚合信号，按 `merge_signal_versions` 口径）：
  | 口径 | 组数 |
  |---|---|
  | `bettable_ever=True`（任一版本通过阀门） | **5** |
  | 阀门拦下（版本里 bettable=False 且有原因） | **26** |
  | 无 bettable 字段（11-04 闸门上线前的旧 schema） | **8** |
  边级别（未聚合）总计 1130 条 signal 边：bettable=True 260 条；
  拦下原因分布 = 软书最优价 772（smarkets 275 / hao-tiyu 233 / betway 93 / 1xbet 73 / unibet 11 /
  其余混合）+ 共识书源不足 261（0家130 / 2家101 / 1家30）。
  **复盘页大多数信号并不是可下注信号**——之前 `bettable_ever ? "可下注" : gated ? "观察" : ""`
  把旧 schema 的 8 组显示成没有标注（看似可下注），且"观察"不区分原因。
- **改动**（`live.py::merge_signal_versions`）：
  - 聚合组新增 `reasons`（bettable_note 计数）、`n_bettable`（通过阀门的版本数）、
    `schema_old`（存在无字段版本）；
  - 新增 `_bettable_label()`：`可下注` / `旧数据(无闸门)` / `观察·软书<书名>` /
    `观察·仅N家书源` / `观察·分歧` / `观察`，输出到 `bettable_label` 字段；
  - 前端 `postCard`：`可下注` 绿色加粗，其余灰色；hover 提示含
    `其中 N 个版本通过阀门`。
- **测试**：`tests/test_signal_label.py` 6 用例。**157 passed**。
- **效果**：复盘页现在如实区分"当时真能下的"（5/39）和"只是观察"（34/39），
  统计信号 ROI 时不会再把观察项误当可下注信号。

### 10.54 2026-11-07 信号模拟按可下注口径重新回填

- **背景**：旧回填（11 条模拟注单）用的是"signal=True 全量"口径——其中大部分当时
  阀门不会放行（软书价/书源不足）。复盘页核实后（10.53），信号模拟应该回答
  "跟单可下注信号值不值"，所以按阀门口径重填。
- **`backfill_bets.py --bettable-only`**：
  - `_select_signals()` 增加 bettable 过滤：只取 `bettable=True` 的边；
    同一信号取**首个通过阀门的版本**（模拟"阀门放行才下注"）；
  - 11-04 闸门上线前的旧 schema 信号（无 bettable 字段）**排除而非假设**——
    无法判定就不假装能判定（8 组旧信号不参与）；
  - 备注以「回填 可下注」开头（区分于旧的「回填 赛前信号」）；
  - `--replace` 先删 `note LIKE '回填%'` 再重填。
- **口径对比（1u/注）**：
  | 口径 | 注数 | 胜/负 | 盈亏 | ROI |
  |---|---|---|---|---|
  | 旧：signal 全量（含观察项） | 11 | 5/6 | −1.31 | −11.9% |
  | 新：仅 bettable（重放阀门） | 5 | 4/1 | +4.07 | **+81.5%** |
  其中 4 条与真实注单同键（当时确实跟了），回填去重跳过；实际入库 1 条
  （401909930 total/over 218.5 @1.91 → 胜 +0.91）。
  ⚠️ 样本极小（5 条），+81.5% 无统计意义，只说明阀门方向的样本质量更好。
- **云端同步**：本地台账已推坚果云（pushed=true），CI/手机下一轮拉取。
- **测试**：`tests/test_backfill_bettable.py` 3 用例（无字段/False 排除、
  首个通过版本入选、bettable_only=False 保持旧口径）。**160 passed**。

### 10.55 2026-11-07 台账清空功能（双重确认 + 自动备份）

- **API**：`POST /api/bets/clear`（`{scope, confirm}`）：
  - `scope="all"` + confirm="CLEAR"：删除全部注单（额外前端 confirm 二次确认）；
  - `scope="backfill"` + confirm="回填"：只删 `note LIKE '回填%'` 的模拟注单
    （重新回填前清样本用）；
  - **清空前自动备份**到 `data/backup/bets-clear-<ts>.json`（`nbaq.bets/1` 完整形状，
    可直接 `bets-import` 找回）；
  - 确认文本不符 → 400。
- **前端**：收益页工具栏新增「清空…」（红色 danger）→ 浮动面板选范围 + 输确认文本；
  静态模式只清本机 localStorage（服务端注单等快照），提示先导出。
- **CLI**：`bets-clear [--backfill-only] [--yes]`，同口径备份。
- **顺带修复**：`normalize_refs` 在 ref 唯一索引下可能撞 UNIQUE（清空重填后
  ref='srv-<id>' 已存在）导致整轮同步崩溃 → 改为 `NOT EXISTS` 防御，跳过占用行。
- **验证**：`tests/test_bets_clear_api.py` 4 用例（确认文本/仅回填/全部/备份落盘）；
  端到端实测清空回填组（11 条备份落盘、统计卡归零）→ 重新 `--bettable-only` 回填
  1 条 → 台账推云。**164 passed**。

### 10.56 2026-11-09 代码维护检查（修 6 个问题）

- **健康快照**：serve/watch 正常；slate 11-02…11-09 全部入库（11-09 8 场）；31 条注单
  全部结算；`data` 分支快照 09:00Z、Pages 09:00Z；DB 29MB、日志 0.1MB、备份 7×24MB。
- **① 缺 slate 兜底从未真正生效**：`_repair_missing_slates` 调 `ingest(conn, day)`，
  但 `service.ingest_date(slate_date)` 只吃日期字符串 → 每次都抛异常并被 except 吞掉。
  改为 `ingest(day)`，测试补上签名断言。
- **② 缺 slate 日期推断偏一天**：原来 `substr(placed_at,1,10)`（UTC），凌晨 00:00–05:00Z
  的注单会指向后一天。改用 `date(placed_at,'-5 hours')` 近似 ET。
- **③ 本机盘口覆盖层每轮白打 ESPN**：`live_overlay` 走 `live_board`（每场 summary + sleep）。
  改为只查 `games` + `live_market_snapshot`，零网络请求。
- **④ ref 冲突会让同步崩**：`bets.id` 是普通 rowid，删除后复用，历史 `srv-53` 可能已属于
  id=37；`normalize_refs` 撞 UNIQUE 直接抛错。改为两遍：正常补 `srv-<id>`，冲突行退
  `srv-<id>-x`；补测试。回填脚本插入后也补 ref（之前留 NULL）。
- **⑤ 日志无轮转 + Tee 脆弱**：`watch.log` 只增不减（README 声称会滚动但代码没有）；
  Tee 在文件打不开时直接崩（曾因坚果云锁定 read-only 导致 watch 起不来）。
  新增 `_rotate_log`（5MB → .1/.2/.3），Tee 打不开时降级为只写 stdout。
- **⑥ 清理**：删除 56 个坚果云「冲突」副本（`app/web` 里的会被 export 带进发布目录）；
  `export_static` 加 `ignore_patterns("*冲突*")`；pytest 用 `conftest.collect_ignore_glob`
  替代非法的 `ignore` 配置项（警告消失）；删除 17 处未使用 import；`predict.py` 去掉 BOM；
  把死配置 `BETTABLE_PRICE_OUTLIER` 接入 `_robust_pair_stats(tol=...)`（默认值不变）。
- **结果**：**165 passed**（1 个仅 starlette 弃用警告），compileall 通过。

### 10.57 2026-11-09 删除墓碑：清空/删除必须跨设备生效

- **问题（维护时实测）**：本机清空回填注单后，`bets-sync` 的 pull 阶段又把云端台账里的
  旧注单**合并回来**（日志 `ledger_merged: 10`）。即「清空」只对本地生效，CI/其他设备
  一同步就复活；CI 的 data 分支 DB 也还会把它们导出到 Pages。
- **方案：删除墓碑 `nbaq/deleted.json`**（`{schema, updated_at, refs[]}`，上限 1000）：
  - `betssync.record_deletions(refs)`：并集写入（API 清空/单条删除、CLI 清空都调用）；
  - `betssync.apply_deletions(conn, refs)`：**合并之后**统一删除（inbox/台账先合并，
    墓碑最后生效，手机重新导出的已删注单也不会复活）；
  - 本机 `pull_only` 与 CI 的 `watch --once` 都会应用墓碑 → CI DB 同步删除、
    静态导出/数据分支不再带回。
- **验证**：清空 12 条回填 → 墓碑 12 条 → 重新回填 1 条可下注 → 本地/云端 20 条（1 条模拟）；
  再触发 CI 后 Pages 也是 20 条、旧「回填 赛前信号」全部消失。
- **CI 补齐**：`refresh.yml`（每日两次）原本完全不跑 betssync → 它的导出会带旧注单。
  在恢复 DB 后加 `bets-sync --pull-only`（合并云端台账 + 应用墓碑，不回推）。
- **手机端补齐**：`LB.davSync()` 现在也会拉 `nbaq/deleted.json`，把本机已被删的注单
  一并移除，否则手机一推又复活（toast 显示「按云端墓碑删除 N 条」）。
- **服务端兜底**：`merge_rows(..., tombstones=…)` 在合并阶段直接跳过墓碑 ref
  （返回 `blocked` 计数）——旧版手机把已删注单重新推到云端也复活不了。
- **测试**：`tests/test_betssync_tombstones.py` 4 例（按 ref 删除幂等 / 合并阶段拦截 /
  run 先合并后删 / 墓碑并集与持久化）。

### 10.58 2026-11-09 模拟注单自动化：赛前先填 + 赛后兜底

- **需求**：每场比赛结束后都要有模拟单；更好的做法是赛前先填（不做事后回填，
  避免后视偏差）。
- **`app/jobs/simulate.py`**：
  - `ensure_sim_bets(conn, slate)`：**赛前先填**——`collect_signal_items(bettable_only=True)`
    里每条可下注信号立刻记 1u 模拟注单（当时报价、`result=open`），比赛结束后由
    `settle` 正常结算；ref = `sim-<game|market|side|line 的 md5>`，CI 与本机各自先填
    也只会落一条；墓碑里的 ref 不再生成（用户清空后不会自动长回来）。
  - `backfill_finished(conn, days=3)`：**赛后兜底**——最近 3 天已完赛但漏记的可下注
    信号补成已结算模拟单（覆盖 CI 停摆/本机关机）。
  - 备注统一以「模拟」开头；`settle.bet_kind` 与前端 `lbKind` 都把它归到模拟组。
- **回填口径修正**：`backfill(sim_only=True)` 只把「回填/模拟」注单当作已存在——
  真实注单不再挡住模拟样本（否则跟着信号下过的比赛会从模拟统计里消失）。
- **watch**：predict 后先填、settle 后兜底（兜底 30 分钟一次）。
- **墓碑一致**：兜底回填也用 `sim-<hash>` 作为 ref，并跳过墓碑——用户清空模拟单后
  不会被下一轮兜底自动长回来。
- **实测**：本地先填今日 0 条（今天 0 个可下注信号）；7 天兜底补 4 条
  （3 胜 1 负，+2.66u），台账 20 → 24 条（真实 19 + 模拟 5）；重复执行 `inserted=0`。
- **测试**：`tests/test_simulate.py` 6 例（先填幂等/墓碑保护/ref 稳定/分类/
  兜底稳定 ref/兜底墓碑）。**175 passed**。

### 10.59 2026-11-09 默认注额统一为 2

- 前端：自定义记一笔（`cb-stake`）、滚球记一笔（`lm-stake`）、信号「记一笔」
  （`mb-stake`）与信号行内注额输入（`data-stake`）默认值 `1 → 2`。
- 后端：`simulate.ensure_sim_bets(stake=2.0)`、`simulate.backfill_finished(stake=2.0)`、
  `scripts/backfill_bets.backfill(stake=2.0)` 与 CLI `--stake` 默认值 `1 → 2`。
- 已存在的模拟注单保持原注额（不改写历史）；新产生的模拟/真实注单默认 2u。

### 10.60 2026-11-09 时间统一北京时间

- **前端**：`fmtTime` / `fmtDayTime` / `fmtSignalTime`（app.js）、`hm()` 与状态栏「更新于」
  （live.js）全部显式 `timeZone: "Asia/Shanghai"`——不再跟随设备时区；
  「数据已是最新」提示也改成北京时间。
- **推送**：`notify._fmt_beijing()`（固定 UTC+8，避免 Windows 缺 tzdata），
  `format_signal` 的开赛时间改为 `11-10 11:30（北京时间）`，不再是原始 `...T03:30:00Z`。
- **保持不变**：比赛日（`slate_date`）仍是 NBA 的 ET 赛程日，作为筛选/分组键不改；
  页面上所有**具体时刻**（开赛、预测、信号、更新时间）均为北京时间。
- **测试**：`tests/test_notify_time.py` 3 例（UTC→BJT/空值/推送文案）。**178 passed**。

### 10.61 2026-11-09 真实注单重复清理 + 防重复

- **现象**：GitHub 台账 44 条（真实 19 + 模拟 25），用户反馈有重复。
- **成因**：
  1. 同一笔真实下注被手机和 PC 各记一次（ref 不同、`placed_at` 差几分钟），
     自然键按精确时间匹配不到 → 真实组出现两条；
  2. 模拟单与真实单指向同一信号 → 两个分组各一条（设计内重叠，保留）。
- **清理**：新增 `scripts/dedupe_bets.py`（默认预览，`--apply` 执行）：
  同场/同市场/同方向/同线 + 赔率差 ≤0.02 + 下注时间差 ≤48h 视为同一笔，
  保留注额最大的，删除其余（先备份 `bets-dedupe-<ts>.json`，并写墓碑）。
  实测删除 2 条（401909925 @1.88 的 1u、401909293 @1.95 的 2u），
  台账 44 → 42（真实 17 + 模拟 25）；401909937（1.98 vs 1.87）视为两笔保留。
- **防复发**：`betssync._find_similar_real()`——真实注单导入时按「同盘口 +
  赔率差 ≤0.02 + 时间差 ≤12h」识别为同一笔，直接跳过；模拟/回填不参与此匹配。
- **测试**：`tests/test_betssync_real_dedupe.py` 4 例（跨设备重复/不同赔率不合并/
  时间差过大不合并/模拟与真实不互相吞并）。**182 passed**。

### 10.62 2026-11-10 模拟单只在收益页显示 + 同场同市场只记一次

- **展示范围**：模拟/回填注单只出现在「收益」页（真实/模拟两个分组）；
  赛前看板卡片、赛时卡片、赛后复盘的注单 chip 与统计一律过滤掉模拟单
  （`app.js::isSimBet`、`live.js::gameBetsOf`），避免和真实下注混淆。
- **只记一次**：模拟单身份从 `sim-<game|market|side|line>` 改为 **`sim-<game|market>`**——
  盘口漂移（214.5→215.5、218.5/218.0/219.0）不再重复记一条。
  `ensure_sim_bets` 与 `backfill_finished` 都先用 `(game_id, market)` 检查是否已有模拟单，
  旧 ref 的历史记录也能挡住新记录。
- **清理**：`scripts/dedupe_bets.py --sim --apply` 按 `(game, market)` 保留最早一条，
  删除 3 条盘口漂移产生的重复并写墓碑（含新式 `sim_ref`，防自动补回）。
  模拟组 33 → **30**（全部按 game+market 唯一）。
- **清空联动**：API/CLI 清空模拟单时，除删除行的 ref 外还墓碑 `sim_ref(game, market)`，
  清空后不会被自动重新补单。
- **测试**：`tests/test_simulate.py` 增至 7 例（ref 市场级/旧 ref 也能挡住重复）。
  **183 passed**。

### 10.63 2026-11-10 从 333体育导入近 7 天真实注单

- **来源**：内置浏览器登录 333体育 → `/history/order` 的 API 响应（`settlement.data`），
  导出 `data/333-bets.json`（27 单，0 串关）。
- **`scripts/import_333_bets.py`**：
  - ah→spread / ou→total / ml→moneyline；h→home / a→away / ov→over / ud→under；
  - 欧赔直接入库、注额=ante、盈亏=netWinAmount、结果按盈亏正负；
  - 球队中文全名 → 缩写按**后缀**匹配（修掉「菲尼克斯太阳」被「尼克斯」抢走）；
  - kick（北京时间）→ ET 比赛日 → 按 slate + 对阵找 `game_id`；
  - ref=`hao-<orderId>`，重复导入幂等；`--replace-manual` 删除之前手动记的同一笔
    （盘口差 ≤0.5 / 赔率差 ≤0.15 / 时间差 ≤24h），先备份 + 写墓碑。
- **导入侧旁路**：`merge_rows` 对 `hao-` 前缀的 ref 跳过「12h 同笔」启发式，
  因为平台订单号就是唯一事实（避免把同场同盘口的两张真实订单误合并）。
- **结果**：真实 17 → **27**（删除 17 条手动重复 + 导入 27 条平台订单），
  模拟 30 不变；台账 **57**。真实组投入 63u、盈亏 **−6.50u**、ROI −10.3%，
  与平台 `totalNetWin -6.5` 完全一致；精确重复 0 组。
- **测试**：**183 passed**。

### 10.64 2026-11-10 修复：墓碑必须先删再合并

- **现象**：333 导入后本地/云端 57 条，但 Pages/data 分支只有 56 条，缺
  `hao-261104124044-vtK34T`（森林狼 @ 火箭 胜负客 @3.05）。
- **根因**：`betssync.run()` 先合并台账、后应用墓碑。被删除的手动注单
  `srv-7` 与平台订单**自然键完全相同**（同一秒下注、同盘口同价），合并阶段
  把平台订单当成重复跳过；随后墓碑才删掉手动单 → 两条都没了。
- **修复**：墓碑改到合并**之前**执行（合并后再兜底删一次，计数累加）。
  平台订单因此能正常落库。
- **测试**：新增回归用例（墓碑行与平台订单同自然键 → 平台订单必须保留）。
  **184 passed**。

### 10.65 2026-11-10 333 订单导入器支持原始 API 响应 + 未结算单

- `scripts/import_333_bets.py` 的 `normalize_payload()` 自动识别三种输入：
  1. **原始 API 响应** `{data:{settlement:{data}, unSettlement:{data}}}`（今后浏览器里
     保存整个响应体即可直接导入；`unSettlement` 以 `result=open` 入库，赛后自动结算）；
  2. 原始订单数组（带 `gameContent.detail`）；
  3. 之前导出的紧凑数组。
- `to_bet()` 支持 open 单（profit=None），`merge_rows` 的单调更新会等赛后结算补结果。
- 球队匹配改为**后缀优先**（`菲尼克斯太阳` 不再被 `尼克斯` 抢走）。
- 测试：`tests/test_import_333.py` 5 例。**189 passed**。

### 10.66 2026-11-10 333 体育成为真实注单唯一事实源

- **策略**：`import_333_bets.py` 默认 `--authoritative`：
  1. 替换与平台订单重复的手动转记；
  2. 删除「本次快照窗口内、但不在快照里」的**已结算**真实注单（备份 + 墓碑）；
  3. 未结算真实注单不删（快照可能不含未结算区）；模拟/回填不参与。
  只增不删时用 `--no-authoritative`。
- **窗口**：本次快照 betTime 的 min/max ±12h；若原始响应还有 `paging.nextCursor`
  （未取完），不删 `hao-` 订单，避免分页截断误删。
- **实测**：以 27 条订单为准，清理 0 条额外记录（当前真实注单已经全部来自 333）。
- **测试**：`find_prunable()` 用例（窗口内额外单删除 / 窗口外保留 / open 保留 /
  模拟保留）。**190 passed**。

### 10.67 2026-11-10 重建台账 + 网页端「清缓存」

- **一次性重建**：`scripts/reset_bets.py`（默认预览，`--apply` 执行）：
  - 删除全部注单（真实 + 模拟），先备份 `bets-reset-<ts>.json`；
  - 重写墓碑：保留模拟单 ref + `sim_ref(game, market)`（旧模拟不复活），
    **移除所有 `hao-` 墓碑**（允许重新导入 333）。
- **执行**：清空 59 条（真实 27 + 模拟 32）→ 导入 333 的 27 条 →
  台账只剩 **27 条真实**（0 模拟），盈亏 −6.50u，已推云端。
- **网页端「清缓存」**：顶栏新增按钮——注销 Service Worker、删除 CacheStorage、
  清 sessionStorage 后硬刷新；保留 localStorage（本机注单、主题、坚果云配置）。
  实测点击后缓存清空、页面重载正常。
