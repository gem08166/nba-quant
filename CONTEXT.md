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
3. 收盘线基准下一步从单家 `sbr-fanduel` 改成 8 家共识（`backfill_closing` 已回填 8 书源，报告侧尚未用共识）。
