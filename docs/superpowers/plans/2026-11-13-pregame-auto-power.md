# 赛前自动开关机（方案A）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每场比赛前 2 小时自动从休眠唤醒，跑 `nbaq refresh`，跑完自动休眠/关机，无人值守循环。

**Architecture:** 纯软件路线——Task Scheduler 唤醒计时器（WakeToRun）从休眠（S4）唤醒，绕过 BIOS RTC（本机固件不支持 S5 唤醒，已实测两轮确认）。调度脚本读 nba.db 下一场比赛 tipoff，动态注册唤醒任务；runner 跑完 refresh 后为下一场重新注册并休眠。每日 23:00 兜底任务保证循环自愈。

**Tech Stack:** PowerShell 5.1（Windows 内置）、schtasks/Register-ScheduledTask、sqlite3（经项目 venv python）、`shutdown /h` 休眠。

**Spec:** 方案A 已获批准（本文件 Approaches 节即设计）。

## Global Constraints

- 唤醒时刻 = 最早 tipoff − 2h，转北京时间（tipoff 存 UTC ISO `2026-11-14T03:30Z`，解析必须 RoundtripKind → ToLocalTime）
- 无比赛日（未来 48h 无 SCHEDULED 比赛）→ 注册无唤醒，`shutdown /s` 真关机
- 两个计划任务：`NBAQ-PregameWake`（动态 Once 触发，每轮重注册 -Force）、`NBAQ-PregameSchedule`（每日 23:00 兜底调度）
- runner 动作：venv python 跑 `nbaq refresh <slate_date>`（绝对路径 `F:\量化\nba-quant-20261103\.venv\Scripts\nbaq.exe`）
- 用户活跃检测：GetLastInputInfo 近 15 分钟有输入 → 跳过休眠（机器在用）
- 休眠已启用（powercfg /a 确认 休眠 available）；唤醒计时器策略已开（RTCWAKE=1 AC/DC）
- 计划任务以 SYSTEM 运行（唤醒后无需用户登录即可跑 runner）；venv 绝对路径 + .env 在项目根，SYSTEM 可读
- 项目 repo：`F:\量化\nba-quant-20261103`（git repo，脚本入库）

## Review Focus

1. **UTC→本地时区**：tipoff 解析必须 RoundtripKind→ToLocalTime（差 8 小时即调度全错）— Task 1 -WhatIf 验证 wake == tipoff−2h 本地时间
2. **唤醒后无用户登录**：wake task 以 SYSTEM 运行（Task 3 验证 principal）— nbaq 以 venv 绝对路径跑，不依赖用户 profile
3. **tipoff 延期**：runner 每次跑完 refresh 重新调 scheduler（读下一场 tipoff 重新注册）— 天然自愈，Task 2 验证
4. **无比赛日**：scheduler 注册无唤醒 → shutdown /s（Task 1 -WhatIf 验证：无比赛时打印"真关机"）
5. **电池模式**：两个任务均设 AllowStartIfOnBatteries + DontStopIfGoingOnBatteries（Task 3 验证 Settings）

---

### Task 1: `scripts/pregame_scheduler.ps1` — 调度脚本

**Files:**
- Create: `scripts/pregame_scheduler.ps1`

**Interfaces:**
- Produces: 注册/更新计划任务 `NBAQ-PregameWake`（Once 触发 = wake_time，action = runner.ps1 -SlateDate <slate_date>，SYSTEM，WakeToRun）；随后休眠（除非 -NoSleep 或近 15 分钟有用户输入）
- Params: `-SlateDate <yyyy-MM-dd>`（可选，默认查 nba.db 下一场）、`-WhatIf`（只打印计算结果，不注册不休眠）、`-NoSleep`（跳过休眠）
- 查询：venv python + sqlite3，`SELECT MIN(tipoff), slate_date FROM games WHERE tipoff > :now AND status='STATUS_SCHEDULED' ORDER BY tipoff LIMIT 1` → JSON；无结果 → 打印"无比赛，真关机"并 shutdown /s（除非 -WhatIf/-NoSleep）

- [ ] Step 1: 写脚本（查询 → wake = tipoff_local − 2h → 注册 NBAQ-PregameWake → 休眠/关机）
- [ ] Step 2: `-WhatIf` 运行，验证打印的 wake 时刻 == tipoff−2h 本地时间
- [ ] Step 3: 真跑一次，`Get-ScheduledTask NBAQ-PregameWake` 触发时刻 == 计算值
- [ ] Step 4: `git add scripts/pregame_scheduler.ps1 && git commit -m "feat: pregame scheduler (next game → wake task → hibernate)"`

### Task 2: `scripts/pregame_runner.ps1` — 唤醒后 runner

**Files:**
- Create: `scripts/pregame_runner.ps1`

**Interfaces:**
- Consumes: `-SlateDate <yyyy-MM-dd>`（唤醒任务 action 烘焙的参数）
- Produces: venv nbaq refresh <SlateDate> → 重调 pregame_scheduler.ps1（注册下一场唤醒）→ 休眠（除非近 15 分钟用户输入）

- [ ] Step 1: 写脚本（venv nbaq refresh → 重调 scheduler → GetLastInputInfo <15min 则跳过休眠）
- [ ] Step 2: `-WhatIf` 模式运行，验证打印的动作序列正确
- [ ] Step 3: `git add scripts/pregame_runner.ps1 && git commit -m "feat: pregame runner (refresh → reschedule → hibernate)"`

### Task 3: 注册两个计划任务

**Files:**
- Create: `scripts/register_pregame_tasks.ps1`

**Interfaces:**
- Produces: `NBAQ-PregameWake`（Once 触发 = 调度脚本算出的 wake 时刻，每轮 -Force 重注册；action = runner.ps1 -SlateDate）+ `NBAQ-PregameSchedule`（每日 23:00 兜底，跑 scheduler）— 均 SYSTEM / WakeToRun / AllowStartIfOnBatteries / DontStopIfGoingOnBatteries / StartWhenAvailable

- [ ] Step 1: 写注册脚本（两个任务，Register-ScheduledTask -Force）
- [ ] Step 2: 运行，`Get-ScheduledTask NBAQ-Pregame*` 显示两个任务
- [ ] Step 3: `git add scripts/register_pregame_tasks.ps1 && git commit -m "feat: register pregame wake + daily bootstrap tasks"`

### Task 4: 实测一轮完整循环

**Files:**
- 无新文件（运行验证）

- [ ] Step 1: 临时把唤醒时刻设为 2 分钟后（真实唤醒测试），休眠
- [ ] Step 2: 机器自动唤醒 → runner 真跑 `nbaq refresh <slate_date>`（幂等 ingest）
- [ ] Step 3: 验证：runner 日志有唤醒时间戳 + nba.db predictions 更新 + 机器跑完重新休眠
- [ ] Step 4: 若循环通过，为下一场真比赛重新注册正式唤醒任务
