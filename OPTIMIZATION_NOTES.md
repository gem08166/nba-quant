# 全量工程优化 — 变更清单与推送步骤

生成时间：2026-11-04（本地工作副本 `D:\YYMODEL\nba-quant`，基线 commit `e9fd412`）

## 一、修了什么（按优先级）

### P0-1 Pages 部署永远慢一个 run
- 文件：`.github/workflows/refresh.yml`
- 症状：线上 `static.json generated_at=04:58` 而仓库 HEAD 是 `05:10`；线上"最佳 EV +33.8%"（MIN ML @4.01），仓库实际已是 +26.8%（共识中位价）。根因是 deploy job 重新 `actions/checkout`，拿到的是**触发 SHA**，永远发布上一轮的 `site/`。
- 修复：refresh job 在导出静态站后直接 `actions/upload-pages-artifact`；deploy job 只剩 `actions/deploy-pages`，不再 checkout。线上从此显示本 run 的数据。

### P0-2 多书共识退化成单书
- 文件：`app/config.py`、`app/db.py`、`app/ingest/service.py`、`app/jobs/predict.py`、`.github/workflows/refresh.yml`
- 症状：5 场比赛库里有 12 家书源，最新预测 `n_books=1`；上一轮是 9-12 家。三个原因一起修：
  1. **抓取顺序**：workflow 里第二书源（hao/odds-api/hoops）现在在 `train+predict` **之前**跑；
  2. **`last_seen_at`**：`insert_odds` 在报价没变时也会更新最后看到时间（旧行为只记最后变化时间，稳定的线被误判超龄）；`predict` 新鲜度改用 `last_seen_at`（旧库无该列时回退 `captured_at`）；
  3. **新鲜度回退**：新增 `NBAQ_MIN_FRESH_BOOKS=3`——新鲜书源 ≥3 家才只用新鲜子集，否则回退全量共识并标 `stale`。旧逻辑是"有 1 家新鲜就丢掉其余 11 家"。
- 验证：`tests/test_consensus.py` 3 个用例 + 手工模拟 05:10 故障场景（12 家陈旧 + 1 家新鲜 → 保留 12 家共识，不再塌缩成 1）。

### P1-1 静态站 PWA 装不上（sw.js 404）
- 文件：`scripts/export_static.py`、`app/web/static.js`
- 症状：`app.js` 注册 `"sw.js"`，但静态站根目录没有这个文件（只在 `static/` 下），`https://.../nba-quant/sw.js` 404。
- 修复：导出时把 `sw.js` 复制到站点根目录。顺手修了静态模式 `/api/model-clv` 的映射（导出的 payload 没有 `report` 包装，导致"研究图表"的基准对照卡一直显示"暂无报告"）。
- 验证：本地起服务，`/sw.js` 200、SW 注册成功（scope=根）、研究页基准对照卡正常渲染（9.89/14.99 vs 11.92/15.31）。

### P1-2 演示注单污染收益页
- 操作：备份后删除 `bets` 表 `id=1`（演示单，ROI +106% 假头条）。现在收益页显示 `0 结算 · ROI —`。

### P1-3 CI 不跑测试
- workflow 加了 `pytest -q` 步骤（安装后、管线前）。

### P2-1 SQLite 移出 Git
- 文件：`.gitignore`、`.github/workflows/refresh.yml`、`README.md`
- `data/nba.db`（8.5MB）不再进 main：Actions 每次从独立 `data` 分支 restore，跑完 `wal_checkpoint` 后 force-push 单 commit 快照回去。主仓只提交 `site/` 和抓取状态。一天两次 8.5MB 提交的历史膨胀就此止住。

### P2-2 CLV 加入线移动
- 文件：`app/db.py`（`bets.clv_line` 列 + 迁移）、`app/jobs/settle.py`、`app/web/bets.js`、`tests/test_settle.py`
- 新增 `clv_line`（单位：分，正数=拿到了更好的线）：让分 `下注线−收盘线`、大球 `收盘线−下注线`、小球 `下注线−收盘线`；胜负无线不计。
- 收益页新增"平均线移动 / 线移动为正"两个统计卡、分市场列、台账列。

### P2-3 整数盘口连续性修正（走盘感知）
- 文件：`app/pricing/devig.py`、`tests/test_pricing.py`、`scripts/model_clv_history.py`
- 旧：整数线按 `floor(-line)+0.5` 半点处理，等于把走盘当输。新：`P(win)+0.5·P(push)`。让分/大小统一，与 settle 的 push 口径一致。
- `model_clv_history` 改用共享的 `cover_prob/over_prob/moneyline_prob/fair_two_way`，回测与生产同一套概率数学。重跑后 spread ROI 从 −7.02% 变为 −5.88%（口径差异如实反映）。

### P2-4 文档与 LICENSE
- `README.md`：新增**第 0 节"验证结论"**（841 场回测、177 场收盘基准、498 注 CLV、增量信息回归 β）、SBR 历史收盘线/多书共识/RAPM 现状、`NBAQ_EDGE` 默认 5%、CLV=价格+线移动、data 分支迁移步骤、已知限制重写。
- 新增 `LICENSE`（MIT，c) 2026 gem08166）。

### P5：blend 扫描固化 + 跨季 regime 修正（第二个可执行改进）

#### 5.1 MARKET_BLEND 扫描脚本（固化）
- 新文件：`scripts/blend_sweep.py`、`tests/test_blend_sweep.py`；报告 `data/blend_sweep_report.json`；workflow 每次刷新自动跑。
- 用途：每次改动模型/定价后重跑，确认选边质量没有变差。**验收发现（holdout 395 场，SBR 收盘线 49 场 98 注）**：blend=0（纯市场线选边）ROI **+3.14%**，blend≥0.15 全部为负（−5.3% ~ −9.5%），且平均 edge 随 blend 单调上升（+0.4% → +10.2%）——**模型权重伤害选边**，与 β≈0、顺线 30.6% 三证合一。MARKET_BLEND=0.55 保留为观察口径，不作为下注依据。
- 模型权重扫描测试用例验证"平均 edge 随 blend 单调上升"（模型自信度增加的机械结果）。

#### 5.2 跨季 regime 修正（先验收，后上线）
- 过程：先实现"上赛季尾月残差外推"→ holdout 场景验证发现 gap=1 天不触发分支 → 改为生产路径（逐日重训）重放 → **验收失败**（2026-10 开局 16 场：MAE 13.68→14.25、|bias| 5.36→7.65，方向反了——上赛季末得分上行但新赛季低开）→ **废弃**。
- 替代方案（**本赛季季前赛锚定**）：季前赛（season_type=1）被训练集刻意排除，但它是唯一直接测量新赛季得分环境的数据，且发生在 as_of 之前、无泄漏。实测开局 16 场：MAE 13.68→**13.39**、|bias| 5.36→**4.19**（carry=1.0 最优，默认 0.5 保守上线）。
- 实现：`ratings.fit` 跨季分支（offseason gap > 10 天时）查询下一个赛季标签的季前赛（`_next_season_label("2025-26")→"2026-27"`），计算"季前赛实际均分 − 模型预测均分"的 shift，按 `TOTAL_REGIME_SEASON_CARRY × shrink` 应用到总分 intercept；小样本（n=10→0.5，n≥30→1.0）收缩。`NBAQ_TOTAL_REGIME_SEASON_CARRY=0.5` 默认，0 可关闭。
- 验证：`tests/test_season_carry.py` 3 个用例（触发与方向、关闭等价旧行为、开局 bias 改善）；生产路径回归确认 11 月重训（gap=1）不受影响、holdout 数字完全不变（跨季分支只在真正的休赛期窗口生效）。
- 诚实说明：修正只在赛季开局 ~1 周内有意义（之后逐日重训自愈），且样本仅 16 场 1 个赛季，明年开季时值得复核。

### P3（本轮新增）：往"照信号下注"方向的四层闸门

#### 3.1 线移动方向检验（第 0 层验收，最重要的新仪器）
- 新文件：`scripts/linemove_analysis.py`、`tests/test_linemove.py`
- 逻辑：对每条历史信号取"信号时刻线 vs 开赛前最后一笔线"，按信号侧定向判定顺线/反线，二项检验 z 值。这是 beat-the-close 的必要条件检验，比 ROI 便宜（每条信号几小时后就出一个观察，不用等结算）。
- 入口：`python -m app.cli linemove`；报告 `data/linemove_report.json`；研究页新增"可下注闸门"面板；workflow 每次刷新自动跑。

#### 3.2 可下注（bettable）闸门：signal 与 bettable 分离
- 文件：`app/config.py`、`app/jobs/predict.py`、`app/web/app.js`、`tests/test_bettable.py`
- `signal` = 模型算出 EV；`bettable` = 价格真实可拿。三条闸门：
  1. 最优价来自软书/聚合源（1xbet/betway/smarkets/unibet/betrivers/hao-tiyu/bovada）→ 降级（MIN@HOU ML @4.01 即此类）；
  2. 共识书源数 < `NBAQ_BETTABLE_MIN_BOOKS`（默认 3）→ 降级（单书定价基准不可靠）；
  3. 同场 spread/ML 高度相关，同时触发只留 EV 最高的一条（`NBAQ_MUTUAL_EXCLUSIVE=1` 默认开）。
- 看板统计卡新增"可下注/可下注最佳 EV"，信号表格显示"可下/仅观察"pill（悬停显示原因），卡片副标题标注"仅观察"。

#### 3.3 Sharp 锚定共识（开关，默认关）
- `NBAQ_SHARP_ANCHOR=1` 开启：fair 概率改用 Pinnacle 类 sharp 书去水值，软书价格只回答"哪里拿得到价"。当前数据里 sharp 书源尚未接入实时管线，开启无效果，等 HoopsAPI 侧 Pinnacle 稳定后再启用。

#### 3.4 前端/导出/API 配套
- `app/api.py` 新增 `GET /api/linemove`；`static.js` 静态模式映射；`export_static.py` 把 `linemove_report.json`（裁剪 rows）并入 static.json；研究页 gate-card 面板同时显示线移动检验结果与今日 bettable 统计。

### P4：冻结 holdout 验证（70/30 按时间切分）
- 新文件：`scripts/holdout_eval.py`、`tests/test_holdout.py`；入口 `python -m app.cli holdout --split 0.7`；workflow 自动跑；研究页 gate-card 增加 holdout vs walk-forward 对照行。
- **为什么不用随机切分**：随机会让测试期比赛通过 Elo/球队强度泄漏进训练集（先看 3 月比赛再预测 1 月），指标虚高。本脚本严格按时间切（同日比赛不拆），测试期对模型完全冻结：模型参数、σ、Elo 终值都在 cutoff 前生成。
- **与 walk-forward 的分工**：walk-forward 每月重拟合（模拟真实使用，但 σ 下限/MARKET_BLEND/regime 修正等超参是看着全部数据定的，存在轻微调参污染）；holdout 是"冻结模型期末考"，量化超参过拟合。
- **首次结果（943 训 → 395 考）**：分差 MAE 12.13（常数基线 14.46）· 总分 15.57（基线 16.39）· Brier 0.1916 · ECE 0.077 · 方向 70.9%。
- **结论**：holdout（12.13/15.57）与 walk-forward（11.92/15.31）接近，超参过拟合不严重；模型稳定优于常数基线，但分差 bias −1.67 / 总分 bias −3.84 显示跨期 regime 偏移（与总分 regime 修正的方向一致，是已知特性不是 bug）。split=0.5/0.8 复核结论一致。

## 二、本地验证结果

| 项 | 结果 |
|---|---|
| `pytest -q` | **39 passed**（新增 7 个：线移动方向判定/合成 DB、bettable 闸门 ×3、互斥、sharp 默认关） |
| 线移动首验 | 61 信号 · 36 条线移动 · 顺线 **30.6%**（z=−2.33）→ **未通过**：信号不能预测线移动，反而显著反向 |
| bettable 实测 | 今日 7 信号 → 仅 **1 条可下注**（CLE ML +4.8%）；MIN ML +26.8%（1xbet）等全部降级 |
| 同场互斥 | CLE@OKC 让分(软书价)+ML 同时触发 → 只留 ML 可下注 |
| 线上/本地站 | gate-card 面板正常渲染（z=−2.33 未通过）、看板"可下注"统计卡正常、控制台 0 错误 |

### 首次验收的诚实结论

**线移动闸门未通过**（顺线 30.6%，显著 **低于** 50%，z=−2.33）。这比 ROI 检验更早地说明：当前信号不仅没有增量信息，方向上还是**反线**的——模型看好的一侧，市场随后反而往反方向走。这与"模型=乐观偏差"的解释完全一致。因此当前 bettable=1 的那条信号也只应视为观察。等实时快照积累 2-4 周后重跑 `python -m app.cli linemove`，若仍 z<0，"照信号下注"方向应正式关闭。

## 三、你需要做的 git 操作（本机没装 git，无法替你执行）

1. 安装 git（或用 GitHub Desktop），然后：

```powershell
cd D:\YYMODEL\nba-quant

# 一次性建 data 分支（先 checkpoint，避免 WAL 未落盘）
python -c "import sqlite3; c=sqlite3.connect('data/nba.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"

git init
git remote add origin git@github.com:gem08166/nba-quant.git
git fetch origin main

# 用远端 main 为基础，套上本地修改
git checkout -b main origin/main 2>$null
git add -A
git rm --cached data/nba.db          # 停止跟踪数据库（.gitignore 已改）
git commit -m "工程优化：同 run 部署 / 盘口时序与共识回退 / PWA SW / CI 测试 / DB 移出主仓 / 线移动 CLV / 整数线走盘修正 / LICENSE+文档"
git push origin main

# 建孤儿 data 分支，只放 nba.db
git checkout --orphan data
git rm -rf --cached .
git add -f data/nba.db
git commit -m "db snapshot: 2026-11-04"
git push -u origin data
git checkout main
```

2. GitHub 仓库设置：Settings → Pages → Source = **GitHub Actions**（如果还没设）。
3. Actions 页手动 Run 一次 "refresh-and-deploy"，确认：
   - refresh job 里 "Restore database from data branch" 成功；
   - "Run tests" 通过；
   - deploy 后线上 `static.json` 的 `generated_at` 与本 run 时间一致（不再慢一个 run）。

## 四、注意事项 / 后续建议

- 首次推送后如果 data 分支 restore 失败（分支还没建好），workflow 会直接报错退出——先完成上面第 1 步再启用 Actions。
- `model_clv_history` 现在用走盘感知概率，spread 的历史 ROI 数字会与旧报告略有差异（−7.0%→−5.9%），这是口径修正不是数据变化；报告里 by_market 数字已更新。
- 收益页的"线移动"统计从现在起才有数据（历史单子已删）。
- 下一阶段建议（README 第 11 节）：7 家 SBR 共识收盘线、bootstrap CI、回测复用生产 `build_market`。
