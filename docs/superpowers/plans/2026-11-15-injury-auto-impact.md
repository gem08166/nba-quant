# 伤停自动采纳 + 下注闸门（injury auto-impact）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ESPN 伤停 → RAPM 建议自动写入 `injuries.impact` → 预测自动反映伤停；bettable 信号按下注侧缺人合计分值降级为观察；`signal_log` 记录决策时刻伤停快照；A/B 验收决定自动采纳去留。

**Architecture:** `db.py` 加列迁移（照既有 PRAGMA+ALTER 模式）；新文件 `app/jobs/injury.py` 承载去重读取/自动采纳/闸门/快照四个函数；`predict.py` 编排接入（predict_date 开头批量采纳、predict_game 内 edges 就绪后闸门、log_signals 传快照）；333 skill 加查伤停步骤；A/B 脚本跑 12 天历史验收。

**Tech Stack:** Python 3.12 / sqlite3 / pytest；无新依赖。

**Spec:** `docs/superpowers/specs/2026-11-15-injury-auto-impact-design.md`

## Global Constraints

- `injuries` 唯一键含 status：同一球员状态变化会插新行、旧行残留（生产库已验证 8 组），消费方必须按 `(game_id, team_id, player)` 去重取最新 `captured_at` 行。
- impact 单位 = 净胜分点；`ratings.predict`：`margin -= injury_home; margin += injury_away; total -= 0.5*(injury_home+injury_away)`。
- `impact_source='manual'` 永不覆盖。
- 状态因子：out/doubtful→0.8，questionable/day-to-day→0.4，其他→0。
- config 旗标（前缀 NBAQ_，布尔 `"1"=="1"` 模式）：`INJURY_AUTO_APPLY=1`、`INJURY_GATE_ENABLE=1`、`INJURY_GATE_POINTS=3.0`（env 名 `NBAQ_INJURY_AUTO_APPLY` / `NBAQ_INJURY_GATE_ENABLE` / `NBAQ_INJURY_GATE_POINTS`）。
- pytest 281 例不得破坏；提交用 `git -c i18n.commitEncoding=utf-8 commit`。

## Review Focus

- 状态重复行：day-to-day+out 并存时取最新 captured_at 行，不得双重计入（Task 1 测试）。
- 球员复活：状态翻出因子表或建议缺失 → impact 归 0（Task 2 测试）。
- manual 否决权：manual 行保持手设值（Task 2 测试）。
- over/under 市场不触发闸门（Task 3 测试）。
- 闸门只看下注侧：away 缺人不影响 spread|home 信号（Task 3 测试）。

---

### Task 1: 伤停去重读取（app/jobs/injury.py）

**Files:**
- Create: `app/jobs/injury.py`
- Test: `tests/test_injury_auto.py`

**Interfaces:**
- Consumes: sqlite 连接（测试自建内存库，建表列 = 生产 injuries 表 + 3 新列）
- Produces:
  - `latest_by_player(rows: list[row]) -> dict[(team_id:int, player:str), row]` —— 按 (game_id, team_id, player) 分组取最新 captured_at（并列取 rowid 大者）
  - `game_injuries(conn, game_id: str) -> list[dict]` —— 去重后按 (team_id, player) 排序的 dict 列表
  - `_suggestions(conn, game_id: str, model: dict | None = None) -> list[dict]` —— 薄包装转调 `rapm.suggest_impacts`（测试 monkeypatch 点）
  - `snapshot(conn, game_id: str) -> list[dict]` —— game_injuries 行精简为 `{"team_id","player","status","impact","impact_source"}`

- [ ] **Step 1: 写失败测试**：内存库插同一球员两行（day-to-day@15:00 impact 0、out@18:00 impact 2.5）+ 另一队一人。断言 `game_injuries` 该球员只 1 行且 status='out'、impact=2.5；`snapshot` 返回精简键。
- [ ] **Step 2: 运行** `pytest tests/test_injury_auto.py -q` → FAIL（No module named app.jobs.injury）
- [ ] **Step 3: 实现** injury.py 的 `latest_by_player` / `game_injuries` / `_suggestions` / `snapshot`
- [ ] **Step 4: 运行** → PASS
- [ ] **Step 5: 提交** `feat(injury): 伤停快照按球员去重读取`

### Task 2: 迁移 + config + 自动采纳 apply_impacts

**Files:**
- Modify: `app/config.py`（尾部追加 3 旗标）
- Modify: `app/db.py`（SCHEMA injuries 建表补 3 列 + `_migrate` 加 3 列）
- Modify: `app/jobs/injury.py`（apply_impacts）
- Test: `tests/test_injury_auto.py` + 迁移测试（真库副本）

**Interfaces:**
- Consumes: Task 1 全部
- Produces: `apply_impacts(conn, game_ids: list[str], model: dict | None = None, force: bool = False) -> dict`，返回 `{"applied":n,"updated":n,"skipped":n,"zeroed":n}`；对每行写 `impact / impact_source='auto' / applied_status / applied_at`（now 用 `datetime.now(timezone.utc).isoformat(timespec="seconds")`，与库内既有 captured_at 格式一致）

- [ ] **Step 1: 写失败测试**（monkeypatch `_suggestions` 固定返回）：①out 球员建议 3.2 → apply 后 impact=3.2、applied_status='out'、impact_source='auto'；②把该行 impact_source 改 'manual'、impact=1.0 → 再 apply 仍 1.0 且计入 skipped；③建议列表为空（无因子/匹配不到）→ impact=0、计入 zeroed
- [ ] **Step 2: 运行** → FAIL
- [ ] **Step 3: 实现** db 迁移（`PRAGMA table_info(injuries)` 缺列则 ALTER ADD；signal_log 的 injuries 列留 Task 4）+ config 尾部 3 旗标 + `apply_impacts`
- [ ] **Step 3b: 迁移测试**：复制 `data/nba.db` 到临时目录 → `db.connect(path)` 打开 → 断言 injuries 新 3 列存在、旧 281 测试不炸
- [ ] **Step 4: 运行** `pytest tests/test_injury_auto.py -q` PASS；`pytest -q` 全绿
- [ ] **Step 5: 提交** `feat(injury): RAPM 建议自动写入 injuries.impact + 迁移`

### Task 3: 伤停闸门 apply_injury_gate + 编排接入

**Files:**
- Modify: `app/jobs/injury.py`（side_of、apply_injury_gate）
- Modify: `app/jobs/predict.py`（predict_date 开头批量采纳；predict_game 内 edges 就绪后调闸门）
- Test: `tests/test_injury_auto.py`

**Interfaces:**
- Consumes: Task 1/2 全部；`build_market` 产出的 edges（含 market/side/bettable/bettable_note/gated）
- Produces:
  - `side_of(edge: dict) -> str | None`（side=="h"→"home"，"a"→"away"，其余 None）
  - `apply_injury_gate(conn, game_id: str, edges: list[dict]) -> list[dict]`（in-place 改写 bettable=False、bettable_note、gated；返回同一列表）
  - predict_date 顺序：games 就绪后 `apply_impacts(conn, [g["id"]...], model=model)`（INJURY_AUTO_APPLY 时）→ 再逐场 predict_game

- [ ] **Step 1: 写失败测试**（monkeypatch `_suggestions`）：①主队 out 合计 3.2 ≥ 3.0 → moneyline|home bettable 翻 False、note 含「伤停闸门」与球员名；②away 缺人不影响 spread|home；③total|over 不触发；④monkeypatch `config.INJURY_GATE_ENABLE=False` → 不降级
- [ ] **Step 2: 运行** → FAIL
- [ ] **Step 3: 实现** `side_of` / `apply_injury_gate`（注意：先按 3.0 阈值筛出下注侧缺人合计；note 格式 `伤停闸门：主队缺人合计 3.2 分（Evan Mobley 3.2）`）
- [ ] **Step 3b: predict.py 接入**：predict_date 在 games 列表就绪后、循环前调 apply_impacts（打印一行统计）；predict_game 在 `book, market = build_market(...)` 之后、`details` 组装之前调 `injury.apply_injury_gate(conn, game["id"], market["edges"])`——log_signals 与 notify 用同一 edges 引用，天然同步
- [ ] **Step 3c: 既有测试防回归**：`tests/test_bettable.py` 等若直接调 build_market 不经 predict_game 则不受影响；若有测试走 predict_game，monkeypatch `_suggestions` 返回 [] 保持旧行为
- [ ] **Step 4: 运行** `pytest tests/test_injury_auto.py -q` PASS；`pytest -q` 全绿
- [ ] **Step 5: 提交** `feat(injury): bettable 伤停闸门 + predict 编排接入`

### Task 4: signal_log 伤停快照列

**Files:**
- Modify: `app/db.py`（SCHEMA signal_log 建表加 `injuries TEXT`；_migrate 加列）
- Modify: `app/jobs/predict.py`（log_signals 加可选参数；predict_game 生成快照传入）
- Test: `tests/test_injury_auto.py`

**Interfaces:**
- Consumes: Task 1 `snapshot`
- Produces: `log_signals(conn, game_id, slate_date, edges, captured_at, injury_snapshot: list | None = None)`（默认 None，`scripts/backfill_signal_log.py` 的 4 参调用点不受影响）；signal_log.injuries = JSON（ensure_ascii=False）

- [ ] **Step 1: 写失败测试**：调 log_signals 带 injury_snapshot → 行内 injuries 列 JSON 可解析、含 player/status/impact；不传 → 列 NULL
- [ ] **Step 2: 运行** → FAIL
- [ ] **Step 3: 实现** 迁移 + log_signals + predict_game 接线（`inj_snap = injury.snapshot(conn, game["id"])`）
- [ ] **Step 4: 运行** `pytest -q` 全绿
- [ ] **Step 5: 提交** `feat(injury): signal_log 记录决策时刻伤停快照`

### Task 5: A/B 验收 + 333 skill + 文档 + 生产验证

**Files:**
- Create: `scripts/injury_ab_test.py`
- Modify: `C:\Users\Administrator\.config\opencode\skills\333-place-bets\SKILL.md`（前置条件后加 Step 1.5）
- Modify: `CONTEXT.md`（10.88 节）
- Test: 脚本一次性，stdout + `data/injury_ab_report.json`

**Interfaces:**
- Consumes: Task 2 的采纳逻辑（纯内存版，不写库）；当前最新模型
- Produces: `python -m scripts.injury_ab_test` → 对比表 stdout + JSON 报告

- [ ] **Step 1: 写脚本**：对 11-03→11-14 每场未完赛历史比赛：取该场**赛前最后一次**伤停快照（injuries 表按 captured_at 分组）→ 纯内存算 injury_home/away（manual 行取手设值）→ 当前模型重算两版 margin/total（impact=0 vs auto）→ 与最终比分比 MAE；收盘回归 β 用各场收盘线（odds_snapshots 临近开赛快照或 signal_log.price_closing）；两版 bettable 信号按收盘价 ROI；样本 < 30 场 → 打印「样本不足，仅参考」退出 0。**全程只读，不 UPDATE predictions**
- [ ] **Step 2: 运行** → 把 MAE/β/ROI 对比与判定写进 CONTEXT.md 10.88
- [ ] **Step 3: 判定与启用**：β 变差或收盘价 ROI 转负 → 本机设 `NBAQ_INJURY_AUTO_APPLY=0`（系统环境变量，闸门保留）；通过 → 保持默认
- [ ] **Step 4: 333 skill**：SKILL.md 前置条件 3 之后加「Step 1.5 查伤停」：`GET /api/games/{id}` 看 injuries 与 edges[].bettable_note；带「伤停闸门」字样 → 停止授权流程并告知用户
- [ ] **Step 5: 最终验证**：`pytest -q` 全绿 → 提交 → push（失败循环重试）→ `git rev-list --left-right --count main...origin/main` = 0 0
- [ ] **Step 6: 生产验证**：重启 watch（`python -m scripts.boot_start`，用 procutil 确认新 pid）→ 手动 `predict_date('2026-11-14')` → 验证今日 5 场 impact 非 0、components.injury_home/away 非零、看板 bettable_note 出现闸门字样（如触发）；已下注单不追溯
