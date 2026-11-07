// 收益标签页：纸面注单的盈亏汇总、累计收益曲线、分市场拆分、注单台账。
// 依赖 app.js 的 api()/esc()/fmt* 与 research.js 暴露的 window.drawLineChart。

let betsLoaded = false;
let betsChartData = null;

// 累计收益曲线口径：unit=每注 1 单位（排除仓位大小），raw=实际金额
function profitMode() {
  return localStorage.getItem("nbaq:profit-mode") === "raw" ? "raw" : "unit";
}

function syncProfitModeButtons() {
  const box = document.getElementById("profit-mode");
  if (!box) return;
  const mode = profitMode();
  box.querySelectorAll("button").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.profitMode === mode);
  });
}

function renderProfitChart() {
  const canvas = document.getElementById("chart-profit");
  if (!canvas) return;
  const data = betsChartData;
  const mode = profitMode();
  const normalized = mode === "unit";
  const curves = (normalized ? data?.curves_unit : data?.curves) || data?.curves || {};
  const mine = curves.mine || [];
  const sim = curves.sim || [];
  const unit = normalized ? "u" : "";
  const note = document.getElementById("profit-mode-note");
  if (note) {
    note.textContent = normalized
      ? "归一化：每笔盈亏 ÷ 注额，按每注 1 单位累计，排除注额大小的影响。"
      : "金额：按实际注额累计的盈亏。";
  }

  if (mine.length || sim.length) {
    window.drawMultiLineChart?.(canvas, [
      {
        label: "真实",
        color: getComputedStyle(document.documentElement).getPropertyValue("--green").trim() || "#46b95c",
        points: mine.map((p) => ({ date: p.date, value: p.cum_profit })),
      },
      {
        label: "模拟",
        color: getComputedStyle(document.documentElement).getPropertyValue("--blue").trim() || "#4b8fd6",
        points: sim.map((p) => ({ date: p.date, value: p.cum_profit })),
      },
    ], { unit });
    return;
  }

  if (!normalized && data?.curve && data.curve.length) {
    window.drawLineChart?.(
      canvas,
      data.curve.map((p) => p.date.slice(2)),
      data.curve.map((p) => p.cum_profit),
      { unit }
    );
    return;
  }

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#79828f";
  ctx.font = '12px "Plex Mono", ui-monospace, monospace';
  ctx.textAlign = "center";
  ctx.fillText("暂无已结算注单：结算后这里会出现累计收益曲线", canvas.width / 2, 120);
}

const marketLabel = (market) =>
  market === "spread" ? "让分" : market === "total" ? "大小分" : market === "moneyline" ? "胜负" : market;
const sideLabel = (bet) =>
  bet.side === "home" ? "主" : bet.side === "away" ? "客" : bet.side === "over" ? "大" : bet.side === "under" ? "小" : bet.side;
const resultLabel = (result) =>
  ({ won: "胜", lost: "负", push: "走", open: "未结算" }[result] || result);

function resultBadge(result) {
  const cls = result === "won" ? "pos" : result === "lost" ? "neg" : "";
  return `<span class="pill ${result === "won" ? "signal" : ""} ${cls}">${resultLabel(result)}</span>`;
}

const fmtLineMove = (value) =>
  value == null ? "—" : `${value > 0 ? "+" : ""}${Number(value).toFixed(1)} 分`;

function renderBetStats(s) {
  const box = document.getElementById("bets-stats");
  const by = s.by_source || null;
  const fmtMoney = (v) => (v > 0 ? "+" : "") + Number(v || 0).toFixed(2);

  const group = (title, d, tone) => {
    if (!d) return "";
    const cards = [
      ["已结算", `${d.settled} (胜${d.wins}/负${d.losses}/走${d.pushes})`],
      ["盈亏", fmtMoney(d.profit), d.profit > 0 ? "green" : ""],
      ["ROI", d.roi == null ? "—" : fmtSignedPct(d.roi), d.roi > 0 ? "green" : ""],
      ["命中率", d.hit_rate == null ? "—" : fmtPct(d.hit_rate)],
    ];
    if (d.clv_n) {
      cards.push(["平均 CLV", fmtSignedPct(d.avg_clv), d.avg_clv > 0 ? "green" : ""]);
      cards.push(["CLV 为正", fmtPct(d.clv_positive_rate)]);
    }
    if (d.clv_line_n) {
      cards.push(["平均线移动", fmtLineMove(d.avg_clv_line), d.avg_clv_line > 0 ? "green" : ""]);
    }
    cards.push(["未结算", String(d.open ?? 0)]);
    return `
      <div class="stats-group">
        <div class="stats-head ${tone}">${esc(title)}</div>
        <div class="stats">
          ${cards
            .map(
              ([k, v, cls]) =>
                `<div class="stat"><div class="k">${k}</div><div class="v ${cls || ""}" style="font-size:16px">${esc(v)}</div></div>`
            )
            .join("")}
        </div>
      </div>`;
  };

  if (by) {
    box.classList.add("split");
    box.innerHTML =
      group("我的注单（真实）", by.mine, "mine") + group("信号模拟（历史回填）", by.sim, "sim");
    return;
  }

  // 旧数据兜底（没有 by_source）
  box.classList.remove("split");
  const stats = [
    ["已结算", `${s.settled} (胜${s.wins}/负${s.losses}/走${s.pushes})`],
    ["盈亏", fmtMoney(s.profit), s.profit > 0 ? "green" : ""],
    ["ROI", fmtSignedPct(s.roi), s.roi > 0 ? "green" : ""],
    ["命中率", fmtPct(s.hit_rate)],
    ["平均 CLV", fmtSignedPct(s.avg_clv), s.avg_clv > 0 ? "green" : ""],
    ["CLV 为正", fmtPct(s.clv_positive_rate)],
    ["平均线移动", fmtLineMove(s.avg_clv_line), s.avg_clv_line > 0 ? "green" : ""],
    ["线移动为正", fmtPct(s.clv_line_positive_rate)],
    ["未结算", String(s.open)],
  ];
  box.innerHTML = stats
    .map(([k, v, cls]) => `<div class="stat"><div class="k">${k}</div><div class="v ${cls || ""}" style="font-size:16px">${esc(v)}</div></div>`)
    .join("");
}

// 真实 vs 模拟：模拟 = 历史回填，真实 = 我自己记录的（跟信号/手动/滚球）
function renderSourceStats(bySource, byKind) {
  const box = document.getElementById("bets-kinds");
  if (!box) return;
  const src = bySource || {};
  const kinds = byKind || {};
  const mine = src.mine || null;
  const sim = src.sim || null;

  const row = (label, s, cls) => {
    if (!s) return "";
    const roi = s.roi != null ? fmtSignedPct(s.roi) : "—";
    const clv = s.avg_clv != null ? fmtSignedPct(s.avg_clv) : "—";
    const hit = s.hit_rate != null ? fmtPct(s.hit_rate) : "—";
    const profitCls = s.profit > 0 ? "pos" : s.profit < 0 ? "neg" : "";
    const any = (s.settled || 0) + (s.open || 0) > 0;
    return `<tr class="${cls || ""}">
      <td>${label}</td>
      <td>${s.settled}</td>
      <td>${s.open}</td>
      <td class="${profitCls}">${any ? (s.profit >= 0 ? "+" : "") + s.profit.toFixed(2) : "—"}</td>
      <td class="${s.roi > 0 ? "pos" : ""}">${roi}</td>
      <td>${hit}</td>
      <td>${clv}</td>
    </tr>`;
  };

  const sub = (label, s) => row(`　· ${label}`, s, "sub");

  box.innerHTML = `
    <div class="table-wrap" style="margin-bottom:16px"><table>
      <thead><tr><th>来源</th><th>已结算</th><th>未结算</th><th>盈亏</th><th>ROI</th><th>命中率</th><th>平均 CLV</th></tr></thead>
      <tbody>
        ${row("我的注单（真实）", mine, "main-row")}
        ${sub("赛前 · 跟信号 / 手动", kinds.pregame)}
        ${sub("滚球", kinds.live)}
        ${row("信号模拟（历史回填）", sim, "main-row")}
      </tbody>
    </table></div>
    <p class="notice" style="border:none;margin:-6px 0 16px;padding:0;font-size:11.5px;line-height:1.6">
      <b>我的注单</b>是你真实记录过的（包括跟信号记一笔、手动录入、滚球）；
      <b>信号模拟</b>是脚本用历史信号补的回顾性注单，用来扩大样本，<b>不是成绩</b>。
      两者口径不同，请分开看。
    </p>`;
}

function renderMarkets(byMarket) {
  const entries = Object.entries(byMarket || {});
  const box = document.getElementById("bets-markets");
  if (!entries.length) {
    box.innerHTML = `<div class="inj" style="color:var(--muted)">还没有已结算注单。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>市场</th><th>n</th><th>投入</th><th>盈亏</th><th>ROI</th><th>平均 CLV</th><th>平均线移动</th></tr></thead>
      <tbody>
        ${entries
          .map(([market, s]) => {
            const roi = s.roi != null ? fmtSignedPct(s.roi) : "—";
            const clv = s.avg_clv != null ? fmtSignedPct(s.avg_clv) : "—";
            const clvLine = fmtLineMove(s.avg_clv_line);
            return `<tr>
              <td>${marketLabel(market)}</td>
              <td>${s.n}</td>
              <td>${s.stake.toFixed(2)}</td>
              <td class="${s.profit >= 0 ? "pos" : "neg"}">${s.profit >= 0 ? "+" : ""}${s.profit.toFixed(2)}</td>
              <td class="${s.roi >= 0 ? "pos" : "neg"}">${roi}</td>
              <td>${clv}</td>
              <td>${clvLine}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

function liveBadgeFor(bet) {
  // 未结算注单的实时状态：API 模式用 /api/bets/analysis 带出的 game_status/实时比分；
  // 静态模式（手机）回退到看板导出的 games（兼容旧快照缺字段的情况）。
  if (bet.result !== "open") return "";
  let status = bet.game_status;
  let hs = bet.live_home_score;
  let as = bet.live_away_score;
  if ((status == null || hs == null || as == null) && window.APP_MODE === "static") {
    const game = ((window.STATIC_PAYLOAD?.board || {}).games || []).find(
      (x) => String(x.id) === String(bet.game_id)
    );
    if (game) {
      status = status ?? game.status;
      hs = hs ?? game.live_home_score ?? game.home_score;
      as = as ?? game.live_away_score ?? game.away_score;
    }
  }
  const inProgress = status === "STATUS_IN_PROGRESS" || status === "STATUS_HALFTIME";
  if (!inProgress || hs == null || as == null) return "";
  const margin = hs - as;
  const total = hs + as;
  let text = "";
  if (bet.market === "moneyline") {
    text = (bet.side === "home") === margin > 0 ? "目前领先" : "目前落后";
  } else if (bet.market === "spread") {
    const homeLine = bet.side === "home" ? Number(bet.line ?? 0) : -Number(bet.line ?? 0);
    const diff = margin + homeLine;
    if (Math.abs(diff) < 1e-9) text = "贴盘";
    else text = (bet.side === "home") === diff > 0 ? "目前赢盘" : "目前输盘";
  } else if (bet.market === "total") {
    const line = Number(bet.line ?? 0);
    if (Math.abs(total - line) < 1e-9) text = "贴线";
    else text = (bet.side === "over") === total > line ? "目前赢盘" : "目前输盘";
  }
  return text ? `<span class="pill signal" title="实时比分 ${as} : ${hs}">${text}</span>` : "";
}

function ledgerKind(bet) {
  // API 模式带 kind 字段；静态模式从备注推断（与后端 settle.bet_kind 同口径）
  if (bet.kind) return bet.kind;
  const note = String(bet.note || "");
  if (note.includes("滚球")) return "live";
  if (note.includes("回填")) return "backfill";
  return "pregame";
}

const KIND_BADGE = {
  live: '<span class="pill pill-live" title="赛时面板下的滚球注单（真实记录）">真实·滚球</span>',
  backfill: '<span class="pill pill-backfill" title="脚本用历史信号补的回顾性注单，不是你下的">模拟</span>',
  pregame: '<span style="color:var(--muted)" title="跟信号或手动录入的真实注单">真实·赛前</span>',
};

function renderLedger(bets) {
  const box = document.getElementById("bets-ledger");
  if (!bets.length) {
    box.innerHTML = `<div class="inj" style="color:var(--muted)">还没有注单。在看板卡片上点「记一笔」，或用收益页的「自定义记一笔」。</div>`;
    return;
  }

  const mine = bets.filter((b) => ledgerKind(b) !== "backfill");
  const sim = bets.filter((b) => ledgerKind(b) === "backfill");

  const section = (title, rows, hint) => {
    const settled = rows.filter((b) => b.result && b.result !== "open");
    const stake = settled.reduce((s, b) => s + Number(b.stake || 0), 0);
    const profit = settled.reduce((s, b) => s + Number(b.profit || 0), 0);
    const roi = stake > 0 ? profit / stake : null;
    const money = settled.length
      ? ` · 盈亏 <b class="${profit >= 0 ? "pos" : "neg"}">${profit >= 0 ? "+" : ""}${profit.toFixed(2)}</b>` +
        (roi != null ? ` · ROI ${roi >= 0 ? "+" : ""}${(roi * 100).toFixed(1)}%` : "")
      : "";
    return `
      <h3 class="ledger-section">${title}
        <span class="ledger-count">${rows.length} 条（结算 ${settled.length}）${money}</span>
      </h3>
      ${hint ? `<p class="ledger-hint">${hint}</p>` : ""}
      ${rows.length ? ledgerTable(rows) : `<div class="inj" style="color:var(--muted)">暂无</div>`}
    `;
  };

  box.innerHTML =
    section("我的注单（真实）", mine, "") +
    section(
      "信号模拟（历史回填）",
      sim,
      "脚本用历史信号补的回顾性注单，不是当时的真实决策；只用于扩大样本。"
    );
}

function ledgerTable(bets) {
  return `
    <div class="table-wrap"><table>
      <thead><tr><th>比赛日</th><th>比赛</th><th>来源</th><th>市场</th><th>方向</th><th>盘口</th><th>赔率</th><th>注</th><th>模型 EV</th><th>结果</th><th>盈亏</th><th>CLV</th><th>线移动</th></tr></thead>
      <tbody>
        ${bets
          .slice()
          .reverse()
          .map((bet) => {
            const line =
              bet.line == null ? "—" : bet.market === "spread" ? fmtSpread(bet.line) : fmtTotal(bet.line);
            const clv = bet.clv != null ? fmtSignedPct(bet.clv) : "—";
            const clvLine = fmtLineMove(bet.clv_line);
            const ev = bet.ev != null ? fmtSignedPct(bet.ev) : "—";
            const profit =
              bet.result === "open" || bet.profit == null
                ? "—"
                : `<span class="${bet.profit >= 0 ? "pos" : "neg"}">${bet.profit >= 0 ? "+" : ""}${bet.profit.toFixed(2)}</span>`;
            return `<tr>
              <td>${esc(bet.slate_date || "—")}</td>
              <td><button class="linklike" data-goto-game="${esc(bet.game_id)}"
                data-goto-date="${esc(bet.slate_date || "")}"
                data-goto-status="${esc(bet.game_status || "")}"
                title="跳转到该场比赛">${esc(bet.matchup_zh || matchupZh(bet.matchup))}</button></td>
              <td>${KIND_BADGE[ledgerKind(bet)] || KIND_BADGE.pregame}</td>
              <td>${marketLabel(bet.market)}</td>
              <td>${sideLabel(bet)}</td>
              <td>${line}</td>
              <td>${fmtPrice(bet.price)}</td>
              <td>${Number(bet.stake).toFixed(2)}</td>
              <td>${ev}</td>
              <td>${resultBadge(bet.result)}${liveBadgeFor(bet)}</td>
              <td>${profit}</td>
              <td class="${bet.clv != null && bet.clv >= 0 ? "pos" : "neg"}">${clv}</td>
              <td class="${bet.clv_line != null && bet.clv_line >= 0 ? "pos" : "neg"}">${clvLine}</td>
            </tr>`;
          })
          .join("")}
      </tbody>
    </table></div>`;
}

// ---- 自定义记一笔：任选比赛/市场/盘口/赔率（不依赖信号）----
async function openCustomBetPanel() {
  document.getElementById("custom-bet-panel")?.remove();
  let games = [];
  try {
    if (window.APP_MODE === "static") {
      games = (window.STATIC_PAYLOAD?.board?.games || []).slice();
    } else {
      const data = await api(`/api/games?date=${encodeURIComponent(state.date)}`);
      games = data.games || [];
    }
  } catch (error) {
    toast(`加载比赛失败：${error.message}`);
    return;
  }
  if (!games.length) {
    toast(`${state.date} 没有比赛，请先在上方切换日期`);
    return;
  }

  const MZ = { spread: "让分", total: "大小", moneyline: "胜负" };
  const panel = document.createElement("div");
  panel.className = "lm-panel";
  panel.id = "custom-bet-panel";
  panel.innerHTML = `
    <div class="lm-panel-head">
      <b>自定义记一笔</b>
      <span>${esc(state.date)} · 共 ${games.length} 场</span>
    </div>
    <div class="lm-panel-grid" style="grid-template-columns:1fr">
      <label>比赛<select id="cb-game">
        ${games
          .map(
            (g) =>
              `<option value="${esc(g.id)}">${esc(teamShortZh(g.away.abbrev, g.away.name))} @ ${esc(
                teamShortZh(g.home.abbrev, g.home.name)
              )}${g.status === "STATUS_FINAL" ? "（已结束）" : ""}</option>`
          )
          .join("")}
      </select></label>
    </div>
    <div class="lm-panel-grid" style="grid-template-columns:1fr 1fr;margin-top:8px">
      <label>市场<select id="cb-market">
        <option value="spread">让分</option>
        <option value="total">大小分</option>
        <option value="moneyline">胜负</option>
      </select></label>
      <label>方向<select id="cb-side"></select></label>
    </div>
    <div class="lm-panel-grid" style="grid-template-columns:repeat(3,1fr);margin-top:8px">
      <label>盘口<input id="cb-line" type="number" step="0.5"></label>
      <label>赔率<input id="cb-price" type="number" step="0.01" min="1.01" value="1.91"></label>
      <label>注额<input id="cb-stake" type="number" step="0.5" min="0.5" value="1"></label>
    </div>
    <div class="lm-panel-meta" id="cb-meta"></div>
    <div class="lm-panel-actions">
      <button class="btn small primary" id="cb-confirm">确认记录</button>
      <button class="btn small" id="cb-cancel">取消</button>
    </div>
    <div class="lm-panel-note">自定义注单用该场赛前预测估算概率（无预测按 50%）；备注以「自定义」开头，与信号注单区分。仅纸面跟踪。</div>`;
  document.body.appendChild(panel);

  const els = {
    game: panel.querySelector("#cb-game"),
    market: panel.querySelector("#cb-market"),
    side: panel.querySelector("#cb-side"),
    line: panel.querySelector("#cb-line"),
    price: panel.querySelector("#cb-price"),
    stake: panel.querySelector("#cb-stake"),
    meta: panel.querySelector("#cb-meta"),
  };

  const currentGame = () => games.find((g) => String(g.id) === String(els.game.value));

  function applyMarket() {
    const market = els.market.value;
    els.side.innerHTML = MANUAL_SIDES[market].map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
    if (market === "moneyline") {
      els.line.value = "";
      els.line.disabled = true;
    } else {
      els.line.disabled = false;
      const lines = currentGame()?.prediction?.lines || {};
      const def = market === "spread" ? lines.spread : lines.total;
      els.line.value = def != null ? Number(def).toFixed(1) : "";
    }
  }

  function renderMeta() {
    const game = currentGame();
    const market = els.market.value;
    const line = market === "moneyline" ? null : Number(els.line.value);
    const prob = game ? manualModelProb(game, market, els.side.value, line) : null;
    const used = prob == null ? 0.5 : prob;
    const ev = used * Number(els.price.value || 0) - 1;
    els.meta.innerHTML =
      `模型概率 <b>${fmtPct(used)}</b> · EV <b class="${ev >= 0 ? "pos" : "neg"}">${fmtSignedPct(ev)}</b>` +
      (prob == null ? "（无预测，按 50% 记录）" : "");
  }

  applyMarket();
  renderMeta();
  els.game.addEventListener("change", () => {
    applyMarket();
    renderMeta();
  });
  els.market.addEventListener("change", () => {
    applyMarket();
    renderMeta();
  });
  els.side.addEventListener("change", renderMeta);
  els.line.addEventListener("input", renderMeta);
  els.price.addEventListener("input", renderMeta);
  els.stake.focus();
  els.stake.select();

  panel.querySelector("#cb-cancel").addEventListener("click", () => panel.remove());
  panel.querySelector("#cb-confirm").addEventListener("click", async () => {
    const game = currentGame();
    if (!game) return toast("请选择比赛");
    const market = els.market.value;
    const side = els.side.value;
    const price = Number(els.price.value);
    const stake = Number(els.stake.value);
    const line = market === "moneyline" ? null : Number(els.line.value);
    if (!Number.isFinite(price) || price <= 1) return toast("赔率必须大于 1");
    if (!Number.isFinite(stake) || stake <= 0) return toast("注额无效");
    if (market !== "moneyline" && !Number.isFinite(line)) return toast("请输入盘口");
    const prob = manualModelProb(game, market, side, line);
    const used = prob == null ? 0.5 : prob;
    const sideZh = MANUAL_SIDES[market].find(([v]) => v === side)?.[1] || side;
    const lineText = line == null ? "" : market === "spread" ? " " + fmtSpread(line) : " " + fmtTotal(line);
    try {
      await api("/api/bets", {
        method: "POST",
        body: JSON.stringify({
          game_id: game.id,
          slate_date: game.slate_date || state.date,
          matchup: `${teamShortZh(game.away.abbrev, game.away.name)}@${teamShortZh(game.home.abbrev, game.home.name)}`,
          market,
          side,
          line,
          price,
          stake,
          model_prob: used,
          ev: used * price - 1,
          note: `自定义 ${MZ[market]} ${sideZh}${lineText} @${price.toFixed(2)} · ${game.slate_date || state.date}`,
        }),
      });
      panel.remove();
      toast("已记录纸面注单");
      await loadBets();
    } catch (error) {
      toast(`记录失败：${error.message}`);
    }
  });
}

function renderBetsToolbar() {
  const box = document.getElementById("bets-toolbar");
  if (!box) return;
  const staticMode = window.APP_MODE === "static";
  const apk = window.DAV?.isApk();
  box.innerHTML = `
    <div class="bets-toolbar">
      <button class="btn small primary" id="bets-custom" title="任选比赛、市场、盘口、赔率，手动记一条">＋ 自定义记一笔</button>
      ${
        staticMode
          ? `<button class="btn small" id="bets-export">导出台账</button>
      <button class="btn small" id="bets-import">导入台账</button>
      ${apk ? `<button class="btn small" id="bets-dav-sync">立即同步</button>
      <button class="btn small" id="bets-dav-settings">同步设置</button>` : ""}
      <input type="file" id="bets-import-file" accept=".json,application/json" hidden>
      ${apk ? `<span class="hint">坚果云自动同步已启用</span>` : `<span class="hint">手机：导出 → 坚果云 app 上传到 nbaq/inbox/；PC 端 30 分钟内自动合并</span>`}`
          : `<span class="hint">自定义记一笔：任选比赛、市场、盘口、赔率（不依赖信号）</span>`
      }
    </div>`;
  document.getElementById("bets-custom").addEventListener("click", openCustomBetPanel);

  if (!staticMode) return;
  document.getElementById("bets-export").addEventListener("click", () => {
    const rows = window.LB.read();
    const blob = new Blob([JSON.stringify(rows, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `nbaq-bets-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  const fileInput = document.getElementById("bets-import-file");
  document.getElementById("bets-import").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    try {
      const rows = JSON.parse(await file.text());
      const result = window.LB.importRows(rows);
      toast(`导入完成：新增/更新 ${result.added} 条，共 ${result.total} 条`);
      await loadBets();
    } catch (error) {
      toast(`导入失败：${error.message}`);
    } finally {
      fileInput.value = "";
    }
  });
  if (apk) {
    document.getElementById("bets-dav-sync").addEventListener("click", async () => {
      try {
        const result = await window.LB.davSync();
        toast(`同步完成：拉入 ${result.mergedIn ?? 0} 条${result.pushed ? "，已上传" : ""}`);
        await loadBets();
      } catch (error) {
        toast(`同步失败：${error.message}`);
      }
    });
    document.getElementById("bets-dav-settings").addEventListener("click", openSyncSettings);
  }
}

function openSyncSettings() {
  const cfg = window.DAV.davCfg() || { url: "https://dav.jianguoyun.com/dav/", user: "", pass: "" };
  const oddsKey = localStorage.getItem("nbaq:oddsapi-key") || "";
  const overlay = document.createElement("div");
  overlay.className = "modal";
  overlay.style.zIndex = "60";
  overlay.innerHTML = `
    <div class="modal-card" role="dialog" aria-modal="true">
      <button class="close" aria-label="关闭">×</button>
      <h2>同步设置（仅存本机）</h2>
      <div class="form-grid">
        <div style="grid-column: span 2"><label>WebDAV 地址</label>
          <input id="dav-url" type="text" value="${esc(cfg.url || "https://dav.jianguoyun.com/dav/")}"></div>
        <div><label>坚果云账号（邮箱）</label>
          <input id="dav-user" type="text" value="${esc(cfg.user || "")}" autocomplete="off"></div>
        <div><label>应用密码（非登录密码）</label>
          <input id="dav-pass" type="password" value="${esc(cfg.pass || "")}" autocomplete="off"></div>
        <div style="grid-column: span 2"><label>The Odds API key（可选，抓多书快照用）</label>
          <input id="odds-key" type="text" value="${esc(oddsKey)}" autocomplete="off"></div>
        <div style="grid-column: span 2"><button class="btn primary" id="dav-save">保存</button></div>
      </div>
      <p class="notice" style="border:none;margin-top:10px;padding-top:0">
        应用密码在坚果云网页：账户信息 → 安全选项 → 第三方应用管理 生成。
      </p>
    </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector(".close").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#dav-save").addEventListener("click", () => {
    window.DAV.davSave({
      url: overlay.querySelector("#dav-url").value.trim() || "https://dav.jianguoyun.com/dav/",
      user: overlay.querySelector("#dav-user").value.trim(),
      pass: overlay.querySelector("#dav-pass").value,
    });
    const odds = overlay.querySelector("#odds-key").value.trim();
    if (odds) localStorage.setItem("nbaq:oddsapi-key", odds);
    else localStorage.removeItem("nbaq:oddsapi-key");
    toast("已保存。同步在下次打开看板/收益页时自动执行");
    overlay.remove();
    loadBets();
  });
}

async function loadBets() {
  try {
    renderBetsToolbar();
    const data = await api("/api/bets/analysis");
    const s = data.summary;
    renderBetStats(s);
    renderSourceStats(s.by_source, s.by_kind);
    renderMarkets(s.by_market);
    renderLedger(data.bets);

    betsChartData = data;
    syncProfitModeButtons();
    renderProfitChart();
    betsLoaded = true;
    ensureBetsAutoRefresh();
    document.getElementById("bets-mode-note").textContent =
      window.APP_MODE === "static"
        ? "（静态模式：注单存在本机浏览器 localStorage，换设备或清缓存会丢失；结算在看板更新后自动完成）"
        : "";
  } catch (error) {
    document.getElementById("bets-stats").innerHTML =
      `<div class="empty" style="grid-column:1/-1">收益数据加载失败：${esc(error.message)}</div>`;
  }
}

// 实时台账：收益页可见时每 30 秒静默刷新一次（未结算注单会跟着实时比分
// 显示「目前赢盘/输盘/贴盘」，完赛结算后自动变成胜/负/走）。
let betsAutoTimer = null;
function ensureBetsAutoRefresh() {
  if (betsAutoTimer) return;
  betsAutoTimer = setInterval(() => {
    const panel = document.getElementById("tab-bets");
    if (panel && !panel.hidden && document.visibilityState === "visible") {
      loadBets();
    }
  }, 30000);
  document.addEventListener("visibilitychange", () => {
    const panel = document.getElementById("tab-bets");
    if (document.visibilityState === "visible" && panel && !panel.hidden) loadBets();
  });
}

// 点击台账里的比赛名 → 跳到赛时页（进行中）或赛前看板对应日期并高亮该场
document.getElementById("bets-ledger").addEventListener("click", (event) => {
  const button = event.target.closest("[data-goto-game]");
  if (!button) return;
  window.jumpToGame?.(
    button.dataset.gotoGame,
    button.dataset.gotoDate,
    button.dataset.gotoStatus
  );
});

// 复用统一 tab 分发：切入收益页时加载一次；之后由 30 秒定时器保持新鲜
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button?.dataset.tab === "bets") loadBets();
});
window.loadBets = loadBets;

// 累计收益口径切换：归一化（每注 1 单位）/ 金额
document.getElementById("profit-mode")?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-profit-mode]");
  if (!btn) return;
  localStorage.setItem("nbaq:profit-mode", btn.dataset.profitMode);
  syncProfitModeButtons();
  renderProfitChart();
});
