// 赛时看板：实时比分 + ESPN 胜率 + 模型剩余时间投影。30 秒自动刷新。
// 依赖 app.js 的 api()/esc()/fmt* 工具与全局 state.date。

let liveTimer = null;
let liveActive = false;

function wpBar(prob) {
  const pct = Math.round(prob * 100);
  return `
    <div class="wp-bar" title="主队胜率 ${pct}%">
      <span style="width:${pct}%" class="wp-home"></span>
    </div>
    <div class="wp-labels"><span>客 ${100 - pct}%</span><span>主 ${pct}%</span></div>`;
}

function stateBadge(game) {
  if (game.state === "in") {
    const period = game.period ?? "?";
    const label = typeof period === "number" && period > 4 ? `加时${period - 4}` : `第${period}节`;
    return `<span class="status live">进行中 · ${label} ${esc(game.display_clock || "")}</span>`;
  }
  if (game.state === "post") return `<span class="status final">已结束</span>`;
  return `<span class="status">${fmtDayTime(game.tipoff)} 开赛</span>`;
}

function momentumIcon(delta) {
  if (delta === null || delta === undefined) return "";
  if (delta > 0.03) return `<span class="mom up" title="近10个回合主队胜率 +${(delta * 100).toFixed(0)}%">▲</span>`;
  if (delta < -0.03) return `<span class="mom down" title="近10个回合主队胜率 ${(delta * 100).toFixed(0)}%">▼</span>`;
  return `<span class="mom">·</span>`;
}

function quarterScores(game) {
  // 每节得分（客 : 主）+ 合计；Q1-Q4 之后显示 OT
  const away = game.away_linescores || [];
  const home = game.home_linescores || [];
  const n = Math.max(away.length, home.length);
  if (!n) return "";
  const cols = [];
  for (let i = 0; i < n; i++) {
    const label = i < 4 ? `第${i + 1}节` : `加时${i - 3}`;
    const a = away[i] ?? "—";
    const h = home[i] ?? "—";
    cols.push(
      `<div class="qs-col"><div class="k">${label}</div><div class="v"><span class="q-away">${a}</span><span class="q-sep">:</span><span class="q-home">${h}</span></div></div>`
    );
  }
  cols.push(
    `<div class="qs-col total"><div class="k">合计</div><div class="v"><span class="q-away">${game.away_score}</span><span class="q-sep">:</span><span class="q-home">${game.home_score}</span></div></div>`
  );
  return `<div class="qs-row" title="每节得分（客 : 主）">${cols.join("")}</div>`;
}

function gameBetsOf(game) {
  // 后端聚合（含已结算）+ 静态模式本机 localStorage / 导出快照，按 id 去重
  const map = new Map();
  const add = (bet) => {
    const key = String(bet.id ?? `${bet.market}:${bet.side}:${bet.line}:${bet.price}`);
    if (!map.has(key)) map.set(key, bet);
  };
  if (Array.isArray(game.bets)) game.bets.forEach(add);
  try {
    if (window.LB?.read) {
      window.LB.read()
        .filter((b) => String(b.game_id) === String(game.game_id))
        .forEach(add);
    }
  } catch (_) {
    /* localStorage 不可用 */
  }
  ((window.STATIC_PAYLOAD?.bets || {}).bets || [])
    .filter((b) => String(b.game_id) === String(game.game_id))
    .forEach(add);
  return Array.from(map.values());
}

const RESULT_ZH = { won: "胜", lost: "负", push: "走", open: "未结算" };

function betChip(bet, game) {
  const market = bet.market === "spread" ? "让分" : bet.market === "total" ? "大小" : "胜负";
  const [awayAb, homeAb] = (game.matchup || "").split(" @ ");
  const side =
    bet.side === "home"
      ? teamShortZh(homeAb, game.home_name)
      : bet.side === "away"
        ? teamShortZh(awayAb, game.away_name)
        : bet.side === "over"
          ? "大"
          : "小";
  const line =
    bet.market === "spread"
      ? " " + fmtSpread(bet.line)
      : bet.market === "total"
        ? " " + fmtTotal(bet.line)
        : "";
  const price = bet.price != null ? ` @${fmtPrice(bet.price)}` : "";
  let result = "";
  if (bet.result && bet.result !== "open") {
    const zh = RESULT_ZH[bet.result] || bet.result;
    const cls = bet.result === "won" ? "pos" : bet.result === "lost" ? "neg" : "";
    const profit =
      bet.profit != null ? ` ${(bet.profit >= 0 ? "+" : "")}${Number(bet.profit).toFixed(2)}` : "";
    result = ` <span class="bet-result ${cls}">${zh}${profit}</span>`;
  }
  return `<span class="bet-chip" title="${esc(market + " " + side + line + price + result)}">${market} ${side}${line}${price}${result}</span>`;
}

function liveCard(game) {
  const pred = game.pregame;
  const live = game.live;
  const score =
    game.state === "pre"
      ? ""
      : `<span class="score live-score">${game.away_score} : ${game.home_score}</span>`;

  let liveBlock = "";
  if (game.state === "in" && live) {
    const espnProb = game.espn_home_win_prob;
    const spreadLine = game.lines?.spread;
    const totalLine = game.lines?.total;
    const cover = game.live_cover || {};
    const coverHome = cover.spread_home;
    const coverAway = coverHome !== null && coverHome !== undefined ? 1 - coverHome : null;
    const overP = cover.over;
    const underP = overP !== null && overP !== undefined ? 1 - overP : null;
    const cell = (label, value) =>
      `<div class="model-cell"><div class="k">${label}</div><div class="v" style="font-size:15px">${value}</div></div>`;
    liveBlock = `
      <div class="wp-wrap">
        ${espnProb != null ? wpBar(espnProb) + momentumIcon(game.espn_wp_delta) + `<span class="wp-src">ESPN 实时胜率</span>` : `<span class="wp-src">胜率曲线不可用</span>`}
      </div>
      <div class="model-grid" style="grid-template-columns:repeat(4,1fr);margin:10px 0">
        ${cell("投影最终分差", fmtSpread(live.proj_margin))}
        ${cell("投影最终总分", fmtTotal(live.proj_total))}
        ${cell("模型主胜", fmtPct(live.home_win_prob))}
        ${cell("剩余时间", `${Math.round(live.remaining_factor * 100)}%`)}
      </div>
      ${
        spreadLine != null && coverHome != null
          ? `<div class="meta"><span>赛前让分 <b>${fmtSpread(spreadLine)}</b> · 主队还剩 <b>${fmtPct(coverHome)}</b> / 客队 <b>${fmtPct(coverAway)}</b> 概率覆盖</span></div>`
          : ""
      }
      ${
        totalLine != null && overP != null
          ? `<div class="meta"><span>赛前大小 <b>${fmtTotal(totalLine)}</b> · 大分 <b>${fmtPct(overP)}</b> / 小分 <b>${fmtPct(underP)}</b></span></div>`
          : ""
      }
      ${
        game.recent_plays && game.recent_plays.length
          ? `<div class="plays">${game.recent_plays
              .slice()
              .reverse()
              .map(
                (p) =>
                  `<div class="play"><span class="pc">${p.period ? `第${p.period}节` : ""} ${esc(p.clock || "")}</span> ${esc(p.text_zh || p.text || "")}</div>`
              )
              .join("")}</div>`
          : ""
      }`;
  } else if (game.state === "in" && !pred) {
    liveBlock = `<div class="inj" style="color:var(--muted)">没有赛前预测（该场未生成 prediction），无法做投影。</div>`;
  } else if (game.state === "pre" && pred) {
    liveBlock = `<div class="meta"><span>赛前模型 ${fmtSpread(pred.pred_margin)} / 总分 ${fmtTotal(pred.pred_total)} · 主胜 ${fmtPct(pred.home_win_prob)}</span></div>`;
  } else if (game.state === "post") {
    const predM = pred ? pred.pred_margin : null;
    const actualM = game.home_score - game.away_score;
    const note =
      predM != null
        ? `<div class="meta"><span>赛前预测 ${fmtSpread(predM)} / 总分 ${fmtTotal(pred.pred_total)} · 实际 ${actualM > 0 ? "+" : ""}${actualM} / ${game.home_score + game.away_score}</span></div>`
        : "";
    liveBlock = note;
  }

  const bets = gameBetsOf(game);

  return `
    <article class="card ${game.state === "in" ? "live-card" : ""} ${bets.length ? "has-bet" : ""}" data-game="${esc(game.game_id)}">
      <div class="card-top">
        <span>${esc(game.home_name_zh || teamNameZh(game.home_name, (game.matchup || '').split(' @ ')[1]))}</span>
        <span style="display:flex;gap:6px;align-items:center">
          ${(() => { const openN = bets.filter((b) => b.result === "open").length; return openN ? `<span class="bet-tag" title="本场有 ${openN} 条未结算纸面注单">注单 ${openN}</span>` : ""; })()}
          ${stateBadge(game)}
        </span>
      </div>
      <div class="matchup">
        ${esc(game.matchup_zh || matchupZh(game.matchup)).replace(" @ ", `<span class="at">@</span>`)}${score}
      </div>
      ${game.state === "pre" ? "" : quarterScores(game)}
      ${bets.length ? `<div class="bet-row">${bets.map((b) => betChip(b, game)).join("")}</div>` : ""}
      ${liveBlock}
    </article>`;
}

async function loadLive(force = false) {
  if (!liveActive && !force) return;
  const dot = document.getElementById("live-dot");
  const status = document.getElementById("live-status");
  dot.classList.add("on");
  try {
    await detectMode();
    const data =
      window.APP_MODE === "static"
        ? await espnLive(state.date)
        : await api(`/api/live?date=${encodeURIComponent(state.date)}`);
    const box = document.getElementById("live-games");
    const inPlay = data.games.filter((g) => g.state === "in").length;
    if (!data.games.length) {
      box.innerHTML = `<div class="empty">${esc(data.date)} 没有比赛。换一天，或先 ingest 该日期赛程。</div>`;
    } else {
      box.innerHTML = data.games.map(liveCard).join("");
    }
    status.textContent = `更新于 ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} · ${inPlay} 场进行中 / 共 ${data.count} 场`;
  } catch (error) {
    status.textContent = `加载失败：${error.message}`;
  } finally {
    dot.classList.remove("on");
  }
}

function setLiveActive(active) {
  liveActive = active;
  if (active && !liveTimer) {
    loadLive(true);
    liveTimer = setInterval(() => loadLive(true), 30000);
  }
  if (!active && liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
}

// tab 切换由 app.js 统一分发；这里只在切入赛时页时启动轮询
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (button) setLiveActive(button.dataset.tab === "live");
});
document.addEventListener("visibilitychange", () => setLiveActive(liveActive && !document.hidden));
window.loadLive = loadLive;
