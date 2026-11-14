# 伤停自动纳入预测 + 下注闸门（injury auto-impact）设计

日期：2026-11-15 · 状态：待批准 · 方案：C（A 自动采纳 + B 闸门 + 快照落库）

## 背景与动机

2026-11-15 实例：当日唯一 bettable 信号（鹈鹕 ML @6.0）下注后复查伤停，发现鹈鹕缺 5 人
（含首发中锋 Missi、头号得分手 Trey Murphy III）；森林狼缺 Anthony Edwards（膝盖手术，
缺阵约 2 个月）+ DiVincenzo，模型仍给 +17.9。模型预测完全不知道伤停。

根因排查（代码现状）：

- ESPN 伤停抓取**已存在且自动运行**：`service.ingest_date(with_injuries=True)` →
  `espn.ingest_summary_injuries` → `injuries` 表。免费接口，11-03 起已积累 679 行 / 12 天。
- `injuries` 表有 `impact` 字段（REAL，默认 0），但**当前全部为 0.0**——只有网页
  「一键采用」按钮（`POST /injuries/{id}/apply-suggestion`）会写，机器流程里没人点。
- `predict_game`（app/jobs/predict.py:785-804）已把主客队 impact 求和传入
  `ratings.predict`；`ratings.predict`（app/models/ratings.py:373-391）已实现：
  `margin -= injury_home; margin += injury_away; total -= 0.5*(injury_home+injury_away)`。
  **impact 单位 = 净胜分点**。
- `rapm.suggest_impacts`（app/models/rapm.py:267-305）已实现建议值：
  `max(0, 球员RAPM评分) × 状态因子`，因子 out/doubtful=0.8、questionable/day-to-day=0.4，
  其他状态（如 suspension）不产生建议；球员名匹配不到 PBP id 则跳过。

结论：管线 80% 已建好，缺的是「自动把建议写进 impact」和「下注前的伤停闸门」。

## 意图与目标

1. 预测自动反映伤停：ingest → apply → predict 全链路无人值守。
2. bettable 信号加伤停闸门：下注方向缺主力（按 RAPM 量化）时降级为观察并说明原因。
3. `signal_log` 记录信号时刻的伤停快照，为将来校准 0.8/0.4 系数积累数据。
4. ntfy 推送与 333 skill 下注授权流程能看到伤停拦截原因。

## 非目标 / 反目标

- 不重训模型、不改 RAPM 拟合本身、不改 MARKET_BLEND。
- 不做历史伤停回填（ESPN 只留当前快照，历史不可得）。
- 不破坏手动能力：UI 手动设置的 impact 仍最权威，自动逻辑永不覆盖。
- 反目标：伤停闸门把信号全部拦光（阈值过严）；或自动 impact 把预测修得远离收盘线
  （验收不达标就关掉自动采纳，只保留闸门）。

## 数据与规则

- 状态因子（沿用现有 `_STATUS_FACTOR`）：out/doubtful → 0.8；questionable/day-to-day → 0.4；
  其他 → 无建议（impact 归 0）。
- `suggested_impact = max(0, player_rapm_rating) × factor`（点）。
- 自动采纳策略（每次 predict 前对 slate 内未完赛游戏执行）：
  - `impact_source='manual'` 的行**永不覆盖**；
  - 其余行：status 与 `applied_status` 不同，或 impact 为空/0 且有建议 → 写入
    `impact = suggested_impact`（无建议/匹配不到球员 → 0），并记录 `applied_status`、`applied_at`；
  - status 翻转（out → day-to-day → 不在名单）自然跟随，因为每次 predict 前重算。
- 伤停闸门：edge `bettable=True` 且**下注侧**（side h→home 队，a→away 队；ov/ud 不适用）
  的 out/doubtful 球员建议 impact 之和 ≥ `INJURY_GATE_POINTS`（默认 3.0）→ 降级 observe，
  `bettable_note = "伤停闸门：<队名> 缺 <合计> 分（Ant 4.2, DiVincenzo 1.1）"`。
- 新配置（config.py 既有模式，环境变量可覆盖）：
  `INJURY_AUTO_APPLY=True`、`INJURY_GATE_ENABLE=True`、`INJURY_GATE_POINTS=3.0`。

## 改动清单

1. **db.py 迁移**（照 `bets` 表加列的既有模式）：
   - `injuries` +3 列：`impact_source TEXT NOT NULL DEFAULT 'auto'`、
     `applied_status TEXT`、`applied_at TEXT`；
   - `signal_log` +1 列：`injuries TEXT`（JSON 快照）。
2. **app/models/rapm.py**：`suggest_impacts` 返回项增加 `player_id`（写回与展示用）；
   新增 `apply_impacts(conn, game_ids, force=False) -> dict`（统计 applied/updated/skipped）。
   放 models 层但只做纯计算 + 单条 UPDATE，或整体放 predict.py —— 实现者定，倾向后者。
3. **app/jobs/predict.py**：
   - `predict_date` 开头：对 slate 内未完赛游戏执行 `apply_impacts`（`INJURY_AUTO_APPLY` 开时）；
   - `predict_game`：`build_market` 返回后对 edges 应用伤停闸门（injuries 已在作用域，
     785-790 行；`build_market` 签名不动），降级时改写 `bettable/bettable_note/gated`；
   - `log_signals`：signal_log 新行写入 `injuries` JSON 快照（game 级：队、球员、状态、impact）。
4. **app/jobs/notify.py**：确认拦截原因（bettable_note）随信号流展示；闸门降级本身不推送
   （推送只发 bettable），但看板 reason 已可见——实现者核对 collect_signal_items 路径即可。
5. **app/api.py**：`/games/{id}` 已返回 injuries + injury_suggestions，确认新增列透出
   （impact_source 等），前端无需改。
6. **333 skill**（`C:\Users\Administrator\.config\opencode\skills\333-place-bets\SKILL.md`）：
   前置条件与授权步骤之间加「Step 1.5 查伤停」：调本地 `/api/games/{id}` 看 injuries 与
   bettable_note，凡带「伤停闸门」字样的信号停止授权流程并说明。
7. **CONTEXT.md**：新增小节记录本改动与验收结果。

## 验收（既有标准 + 新增 A/B）

- pytest 全绿（现有 281 例 + 新增）。
- 新增测试：
  - `apply_impacts`：自动写入建议值；manual 行不覆盖；status 变化后重算；匹配不到球员 → 0；
  - 伤停闸门：下注侧 impact 和 ≥ 阈值 → bettable 降级 + note 文本；对侧缺人不影响；
    ov/ud 市场不触发；
  - signal_log 快照列写入；迁移后旧库可开（幂等）。
- **模型改动验收**（用户既定标准）：用 11-03→11-14 的 679 行伤停（12 天 ~100 场），
  每场取赛前最后一次快照，跑「impact=0 vs auto-impact」两版预测对比：
  - 净胜分 MAE；
  - 收盘线回归 β（标准：显著 > 0）；
  - signal_log 中 bettable 信号按收盘价 ROI（标准：不为负）。
- 结果写入 CONTEXT.md；若 β 变差或收盘价 ROI 转负 → `INJURY_AUTO_APPLY=False`（闸门保留）。

## 风险与对策

| 风险 | 对策 |
|---|---|
| 0.8/0.4 系数粗糙，角色球员缺阵也被扣分 | 闸门阈值 3.0 兜底；signal_log 积累后回看校准 |
| ESPN 球员名匹配不到 PBP id | impact 归 0，不恶化现状；建议列表里可见未匹配项 |
| 状态频繁翻转导致预测抖动 | 每次 predict 前重算（幂等），applied_status 记录依据 |
| 手动 impact 被自动覆盖 | impact_source='manual' 永不覆盖 |
| 验收样本只有 12 天 | 定位为方向性验证；闸门独立于预测，验收失败只关 A 不关 B |

## 留给实现者的决定

- `apply_impacts` 放 rapm.py 还是 predict.py（倾向 predict.py，models 层不写库）；
- 闸门在 build_market 内还是 predict_game 后处理（倾向后者，不动 build_market 签名）；
- ntfy 文案细节；
- 迁移函数命名与放置（跟随 db.py 既有模式）。
