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

const RESULT_ZH = { won: "胜/命中", lost: "负/落空", push: "走", open: "未结算" };

const MARKET_ZH = { spread: "让分", total: "大小", moneyline: "胜负" };

function hm(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch (_) {
    return "";
  }
}

// 与后端 signal_backtest.settle_edge 相同的结算逻辑（静态模式/本机结算用）
function settleSignal(market, side, line, homeScore, awayScore) {
  const margin = homeScore - awayScore;
  const total = homeScore + awayScore;
  if (market === "moneyline") {
    if (margin === 0) return "push";
    return (margin > 0) === (side === "home") ? "won" : "lost";
  }
  if (market === "spread") {
    const covered = side === "home" ? margin + line : -margin + line;
    if (covered === 0) return "push";
    return covered > 0 ? "won" : "lost";
  }
  if (market === "total") {
    if (total === line) return "push";
    return (total > line) === (side === "over") ? "won" : "lost";
  }
  return "push";
}

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

// 赛后板块：该场出现过的信号（历史 prediction 去重），附命中/落空
const SIG_RESULT_ZH = { won: "命中", lost: "落空", push: "走" };

function settleSignalsOf(game) {
  // 后端已附 result（API 模式）；静态模式/本机快照在这里结算
  const hist = game.signals_history || [];
  return hist.map((sig) => {
    if (sig.result) return sig;
    const homeScore = Number(game.home_score);
    const awayScore = Number(game.away_score);
    if (!Number.isFinite(homeScore) || !Number.isFinite(awayScore) || homeScore + awayScore === 0) return sig;
    return { ...sig, result: settleSignal(sig.market, sig.side, sig.line, homeScore, awayScore) };
  });
}

function postCard(game) {
  const pred = game.pregame;
  const m = game.home_score - game.away_score;
  const t = game.home_score + game.away_score;
  const sig = settleSignalsOf(game);
  const bets = gameBetsOf(game);
  const settled = bets.filter((b) => b.result && b.result !== "open");
  const sigWon = sig.filter((s) => s.result === "won").length;
  const sigLost = sig.filter((s) => s.result === "lost").length;

  let statsLine = "";
  if (pred) {
    const errM = Math.abs(pred.pred_margin - m);
    const errT = Math.abs(pred.pred_total - t);
    const mlOk = (pred.pred_margin > 0) === (m > 0);
    const cls = mlOk ? "pos" : "neg";
    statsLine = `<div class="meta">赛前预测 ${fmtSpread(pred.pred_margin)} / 总分 ${fmtTotal(pred.pred_total)} · 实际 ${m > 0 ? "+" : ""}${m} / ${t} · 分差误差 ${errM.toFixed(1)} · 总分误差 ${errT.toFixed(1)} · 主胜方向 <b class="bet-result ${cls}">${mlOk ? "✓ 对" : "✗ 错"}</b></div>`;
  }
  let betsLine = "";
  if (settled.length) {
    const profit = settled.reduce((s, b) => s + (Number(b.profit) || 0), 0);
    const stake = settled.reduce((s, b) => s + (Number(b.stake) || 0), 0);
    const roi = stake ? ` · ROI ${profit >= 0 ? "+" : ""}${((profit / stake) * 100).toFixed(1)}%` : "";
    const openN = bets.length - settled.length;
    const cls = profit >= 0 ? "pos" : "neg";
    betsLine = `<div class="meta">纸面注单 ${settled.length} 条结算 · 盈亏 <b class="bet-result ${cls}">${profit >= 0 ? "+" : ""}${profit.toFixed(2)}</b>${roi}${openN ? `（另 ${openN} 条未结算）` : ""}</div>`;
  }
  const sigHtml = sig.length
    ? `<div class="post-signals">${sig
        .map((s) => {
          const market = MARKET_ZH[s.market] || s.market;
          const side =
            s.side === "home" ? "主" : s.side === "away" ? "客" : s.side === "over" ? "大" : "小";
          const line =
            s.line != null
              ? s.market === "spread"
                ? " " + fmtSpread(s.line)
                : " " + fmtTotal(s.line)
              : "";
          const price = s.price_best != null ? ` @${fmtPrice(s.price_best)}` : "";
          const ev = `EV ${s.ev_max >= 0 ? "+" : ""}${Math.round(s.ev_max * 100)}%`;
          const flag = s.bettable_ever ? "可下注" : s.gated_ever ? "观察" : "";
          let res = "";
          if (s.result) {
            const zh = SIG_RESULT_ZH[s.result] || s.result;
            const cls = s.result === "won" ? "pos" : s.result === "lost" ? "neg" : "";
            res = ` <span class="bet-result ${cls}">${esc(zh)}</span>`;
          }
          const title = `出现 ${s.n_seen} 次 · ${s.book || ""} · 首见 ${hm(s.first_seen)} · 最近 ${hm(s.last_seen)}`;
          return `<span class="bet-chip sig" title="${esc(title)}">${esc(market)} ${esc(side)}${esc(line)}${esc(price)} ${esc(ev)}${flag ? ` · ${esc(flag)}` : ""}${res}</span>`;
        })
        .join("")}</div>`
    : "";

  const [awayAb, homeAb] = (game.matchup || "").split(" @ ");
  return `
    <article class="card post-card" data-game="${esc(game.game_id)}">
      <div class="card-top">
        <span>${esc(game.home_name_zh || teamNameZh(game.home_name, homeAb))}</span>
        <span style="display:flex;gap:6px;align-items:center">
          ${sig.length ? `<span class="bet-tag" title="该场历史信号 ${sigWon} 命中 / ${sigLost} 落空">信号 ${sigWon}/${sig.length}</span>` : ""}
          <span class="status final">已结束</span>
        </span>
      </div>
      <div class="matchup">
        ${esc(game.matchup_zh || matchupZh(game.matchup)).replace(" @ ", `<span class="at">@</span>`)}
        <span class="score live-score">${game.away_score} : ${game.home_score}</span>
      </div>
      ${quarterScores(game)}
      ${bets.length ? `<div class="bet-row">${bets.map((b) => betChip(b, game)).join("")}</div>` : ""}
      <div class="post-block">
        <div class="post-title">赛后复盘</div>
        ${statsLine}
        ${betsLine}
        ${sigHtml ? `<div class="post-sig-title">该场出现过的信号（历史去重 · ${sigWon} 命中 / ${sigLost} 落空）</div>${sigHtml}` : ""}
      </div>
    </article>`;
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
    liveBlock = "";  // 赛后信息在独立「赛后复盘」tab 里
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

// ---------- 赛后复盘（独立板块）----------

async function loadPost(force = false) {
  const dot = document.getElementById("post-dot");
  const status = document.getElementById("post-status");
  const box = document.getElementById("post-games");
  const stats = document.getElementById("post-stats");
  if (dot) dot.classList.add("on");
  try {
    await detectMode();
    const data =
      window.APP_MODE === "static"
        ? await espnLive(state.date)
        : await api(`/api/live?date=${encodeURIComponent(state.date)}`);
    const done = (data.games || []).filter((g) => g.state === "post");
    if (!done.length) {
      box.innerHTML = `<div class="empty">${esc(data.date)} 没有已结束的比赛。赛后复盘会在比赛收官后出现——可点右上角「跳到最近完赛日」。</div>`;
      if (stats) stats.innerHTML = "";
      status.textContent = `${esc(data.date)} · 0 场已结束`;
      return;
    }
    box.innerHTML = done.map(postCard).join("");

    // 汇总统计
    let errM = 0, errT = 0, nPred = 0, mlHit = 0;
    let sigTotal = 0, sigWon = 0, sigLost = 0;
    let stake = 0, profit = 0, nBets = 0;
    for (const g of done) {
      const pred = g.pregame;
      if (pred) {
        const m = g.home_score - g.away_score;
        const t = g.home_score + g.away_score;
        errM += Math.abs(pred.pred_margin - m);
        errT += Math.abs(pred.pred_total - t);
        nPred += 1;
        if ((pred.pred_margin > 0) === (m > 0)) mlHit += 1;
      }
      for (const s of settleSignalsOf(g)) {
        sigTotal += 1;
        if (s.result === "won") sigWon += 1;
        else if (s.result === "lost") sigLost += 1;
      }
      for (const b of gameBetsOf(g)) {
        if (b.result && b.result !== "open") {
          stake += Number(b.stake) || 0;
          profit += Number(b.profit) || 0;
          nBets += 1;
        }
      }
    }
    const cell = (k, v, cls) =>
      `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${v}</div></div>`;
    if (stats) {
      stats.innerHTML = [
        cell("已结束比赛", `${done.length}`),
        cell("分差 MAE", nPred ? (errM / nPred).toFixed(2) : "—"),
        cell("总分 MAE", nPred ? (errT / nPred).toFixed(2) : "—"),
        cell("主胜方向", nPred ? `${mlHit}/${nPred}` : "—"),
        cell("历史信号", sigTotal ? `${sigTotal} 条` : "0 条"),
        cell(
          "信号命中",
          sigTotal ? `${sigWon}/${sigTotal} · ${Math.round((sigWon / sigTotal) * 100)}%` : "—",
          sigTotal ? (sigWon / sigTotal >= 0.5 ? "green" : "") : ""
        ),
        cell("结算注单", nBets ? `${nBets} 条` : "0 条"),
        cell(
          "注单盈亏",
          nBets ? `${profit >= 0 ? "+" : ""}${profit.toFixed(2)}` : "—",
          nBets && profit >= 0 ? "green" : ""
        ),
      ].join("");
    }
    status.textContent = `${esc(data.date)} · ${done.length} 场已结束 · 更新于 ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    status.textContent = `加载失败：${error.message}`;
  } finally {
    if (dot) dot.classList.remove("on");
  }
}

// 跳到最近一个有已结束比赛的日期（看赛后复盘）
async function jumpToLastFinished() {
  const btn = document.getElementById("post-jump");
  try {
    const dates = await api("/api/dates");
    const list = (dates.dates || []).map((d) => d.date);
    for (const day of [...list].reverse()) {
      if (day >= state.date && day !== state.date) continue;
      const data = await api(`/api/live?date=${encodeURIComponent(day)}`);
      const done = (data.games || []).filter((g) => g.state === "post").length;
      if (done > 0) {
        state.date = day;
        const el = document.getElementById("date");
        if (el) el.value = day;
        await loadPost(true);
        toast(`已跳到 ${day}（${done} 场已结束）`);
        return;
      }
    }
    toast("没有已结束的比赛日期");
  } catch (error) {
    toast(`跳转失败：${error.message}`);
  } finally {
    if (btn) btn.disabled = false;
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
document.getElementById("post-jump")?.addEventListener("click", jumpToLastFinished);
window.loadLive = loadLive;
window.loadPost = loadPost;
window.jumpToLastFinished = jumpToLastFinished;
