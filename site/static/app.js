const $ = (sel) => document.querySelector(sel);
const state = { date: isoToday(), games: [], detail: null };

function isoToday() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ---- 球队中文名（缩写 / 英文全名 → 中文） ----
const TEAM_ZH_SHORT = {
  ATL: "老鹰", BOS: "凯尔特人", BKN: "篮网", CHA: "黄蜂", CHI: "公牛",
  CLE: "骑士", DAL: "独行侠", DEN: "掘金", DET: "活塞",
  GS: "勇士", GSW: "勇士", HOU: "火箭", IND: "步行者",
  LAC: "快船", LAL: "湖人", MEM: "灰熊", MIA: "热火", MIL: "雄鹿",
  MIN: "森林狼", NO: "鹈鹕", NOP: "鹈鹕", NY: "尼克斯", NYK: "尼克斯",
  OKC: "雷霆", ORL: "魔术", PHI: "76人", PHX: "太阳", POR: "开拓者",
  SAC: "国王", SA: "马刺", SAS: "马刺", TOR: "猛龙",
  UTAH: "爵士", UTA: "爵士", WSH: "奇才", WAS: "奇才",
};
const TEAM_ZH_FULL = {
  "Atlanta Hawks": "亚特兰大老鹰", "Boston Celtics": "波士顿凯尔特人",
  "Brooklyn Nets": "布鲁克林篮网", "Charlotte Hornets": "夏洛特黄蜂",
  "Chicago Bulls": "芝加哥公牛", "Cleveland Cavaliers": "克利夫兰骑士",
  "Dallas Mavericks": "达拉斯独行侠", "Denver Nuggets": "丹佛掘金",
  "Detroit Pistons": "底特律活塞", "Golden State Warriors": "金州勇士",
  "Houston Rockets": "休斯顿火箭", "Indiana Pacers": "印第安纳步行者",
  "LA Clippers": "洛杉矶快船", "Los Angeles Clippers": "洛杉矶快船",
  "Los Angeles Lakers": "洛杉矶湖人", "Memphis Grizzlies": "孟菲斯灰熊",
  "Miami Heat": "迈阿密热火", "Milwaukee Bucks": "密尔沃基雄鹿",
  "Minnesota Timberwolves": "明尼苏达森林狼", "New Orleans Pelicans": "新奥尔良鹈鹕",
  "New York Knicks": "纽约尼克斯", "Oklahoma City Thunder": "俄克拉荷马城雷霆",
  "Orlando Magic": "奥兰多魔术", "Philadelphia 76ers": "费城76人",
  "Phoenix Suns": "菲尼克斯太阳", "Portland Trail Blazers": "波特兰开拓者",
  "Sacramento Kings": "萨克拉门托国王", "San Antonio Spurs": "圣安东尼奥马刺",
  "Toronto Raptors": "多伦多猛龙", "Utah Jazz": "犹他爵士",
  "Washington Wizards": "华盛顿奇才",
};
function teamShortZh(abbrev, name) {
  const key = String(abbrev || "").toUpperCase();
  return TEAM_ZH_SHORT[key] || TEAM_ZH_FULL[name] || abbrev || name || "";
}
function teamNameZh(name, abbrev) {
  return TEAM_ZH_FULL[name] || TEAM_ZH_SHORT[String(abbrev || "").toUpperCase()] || name || abbrev || "";
}
function matchupZh(matchup) {
  if (!matchup || !String(matchup).includes(" @ ")) return matchup || "";
  const [away, home] = String(matchup).split(" @ ");
  return `${teamShortZh(away)} @ ${teamShortZh(home)}`;
}

async function api(path, options = {}) {
  await detectMode();
  if (window.APP_MODE === "static") {
    if (options.method && options.method !== "GET") {
      // 静态模式：纸面注单可写（本机 localStorage）；盘口/伤停等仍只读
      if (window.LB && /^\/api\/bets(\/|$)/.test(path)) {
        if (options.method === "POST") return window.LB.add(JSON.parse(options.body || "{}"));
        if (options.method === "DELETE") {
          window.LB.remove(path.split("/").pop());
          return { ok: true };
        }
      }
      throw new Error("静态模式为只读（数据由 GitHub Actions 定时更新）");
    }
    return staticApi(path);
  }
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

const fmtSpread = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
};
const fmtTotal = (value) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(1);
const fmtPct = (value) =>
  value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
const fmtSignedPct = (value) =>
  value === null || value === undefined
    ? "—"
    : `${Number(value) >= 0 ? "+" : ""}${(Number(value) * 100).toFixed(1)}%`;
const fmtPrice = (value) =>
  value === null || value === undefined ? "—" : Number(value).toFixed(2);

function fmtTime(iso) {
  if (!iso) return "TBD";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDayTime(iso) {
  if (!iso) return "时间待定";
  const d = new Date(iso);
  const day = d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  const week = d.toLocaleDateString("zh-CN", { weekday: "short" });
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${day} ${week} ${time}`;
}

function fmtSignalTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const day = d.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time}`;
}

function statusInfo(game) {
  if (game.status === "STATUS_FINAL") return { text: "已结束", cls: "final" };
  if (game.status === "STATUS_IN_PROGRESS" || game.status === "STATUS_HALFTIME") {
    return { text: "进行中", cls: "live" };
  }
  return { text: "未开赛", cls: "" };
}

function edgeText(edge, game) {
  if (!edge) return "暂无正 EV 信号";
  const side =
    edge.side === "home"
      ? teamShortZh(game.home.abbrev, game.home.name)
      : edge.side === "away"
        ? teamShortZh(game.away.abbrev, game.away.name)
        : edge.side === "over"
          ? "大分"
          : "小分";
  const line =
    edge.market === "spread"
      ? ` ${fmtSpread(edge.line)}`
      : edge.market === "total"
        ? ` ${fmtTotal(edge.line)}`
        : "";
  const label = edge.market === "total" ? "大小分" : edge.market === "spread" ? "让分" : "胜负";
  const tag = edge.bettable ? "" : " · 仅观察";
  return `${label} · ${side}${line} @ ${fmtPrice(edge.price)}${tag}`;
}

function slateBetChip(bet, game) {
  const market =
    bet.market === "spread" ? "让分" : bet.market === "total" ? "大小" : "胜负";
  const side =
    bet.side === "home"
      ? teamShortZh(game.home.abbrev, game.home.name)
      : bet.side === "away"
        ? teamShortZh(game.away.abbrev, game.away.name)
        : bet.side === "over"
          ? "大"
          : "小";
  const line =
    bet.market === "spread"
      ? ` ${fmtSpread(bet.line)}`
      : bet.market === "total"
        ? ` ${fmtTotal(bet.line)}`
        : "";
  let result = "";
  if (bet.result && bet.result !== "open") {
    const zh = { won: "胜", lost: "负", push: "走" }[bet.result] || bet.result;
    const cls = bet.result === "won" ? "pos" : bet.result === "lost" ? "neg" : "";
    const profit =
      bet.profit != null ? ` ${(bet.profit >= 0 ? "+" : "")}${Number(bet.profit).toFixed(2)}` : "";
    result = ` <span class="bet-result ${cls}">${zh}${profit}</span>`;
  }
  return `<span class="bet-chip" title="注 ${Number(bet.stake || 0).toFixed(1)} @ ${fmtPrice(bet.price)}">${market} ${esc(side)}${line} @${fmtPrice(bet.price)}${result}</span>`;
}

function noOdds(game) {
  return !game.prediction?.lines?.spread && !game.prediction?.lines?.total;
}

function renderStats(data) {
  const gamesWithEdges = data.games.filter((game) => (game.edges || []).some((e) => e.signal));
  const allEdges = data.games.flatMap((game) => game.edges || []);
  const signals = allEdges.filter((edge) => edge.signal);
  const bettable = signals.filter((edge) => edge.bettable);
  const best = signals.sort((a, b) => b.ev - a.ev)[0];
  const bestBettable = bettable.sort((a, b) => b.ev - a.ev)[0];
  const noOdds = data.games.filter((game) => !game.prediction?.lines?.spread && !game.prediction?.lines?.total).length;
  const predTime = data.games.find((g) => g.prediction?.created_at)?.prediction?.created_at;
  $("#stats").innerHTML = `
    <div class="stat"><div class="k">比赛</div><div class="v">${data.count}</div></div>
    <div class="stat"><div class="k">信号条数</div><div class="v">${signals.length}</div></div>
    <div class="stat ${bettable.length ? "highlight" : ""}"><div class="k">可下注</div><div class="v ${bettable.length ? "green" : ""}" title="通过软书/共识深度/同场互斥闸门的信号">${bettable.length}</div></div>
    <div class="stat"><div class="k">最佳 EV</div><div class="v ${best ? "green" : ""}">${best ? fmtSignedPct(best.ev) : "—"}</div></div>
    <div class="stat"><div class="k">可下注最佳 EV</div><div class="v ${bestBettable ? "green" : ""}">${bestBettable ? fmtSignedPct(bestBettable.ev) : "—"}</div></div>
    <div class="stat"><div class="k">无盘口场次</div><div class="v" style="color:${noOdds ? "var(--muted)" : "inherit"}">${noOdds}</div></div>
    <div class="stat"><div class="k">日期</div><div class="v" style="font-size:14px">${esc(data.date)}</div></div>
    ${predTime ? `<div class="stat"><div class="k">预测时间</div><div class="v" style="font-size:13px">${fmtDayTime(predTime)}</div></div>` : ""}
  `;
}

function bettableEdgesOf(game) {
  return (game.edges || [])
    .filter((edge) => edge.signal && edge.bettable)
    .sort((a, b) => b.ev - a.ev);
}

function renderBettablePanel(games) {
  const box = $("#bettable-panel");
  if (!box) return;
  const rows = [];
  for (const game of games) {
    for (const edge of bettableEdgesOf(game)) rows.push({ game, edge });
  }
  rows.sort((a, b) => b.edge.ev - a.edge.ev);
  if (!rows.length) {
    box.innerHTML = `<div class="bp-empty">今日暂无可下注信号——其余高 EV 项因软书价/共识过浅/同场相关被降级为「仅观察」。</div>`;
    return;
  }
  box.innerHTML = `
    <div class="bp-head">
      <span class="bp-title">今日可下注</span>
      <span class="bp-count">${rows.length} 条</span>
      <span class="bp-hint">已通过软书价 / 共识深度 / 同场互斥闸门</span>
    </div>
    <div class="bp-list">
      ${rows
        .map(({ game, edge }) => {
          const side = edgeSideLabel(edge, game);
          const edgeIndex = (game.edges || []).indexOf(edge);
          const line =
            edge.market === "spread"
              ? " " + fmtSpread(edge.line)
              : edge.market === "total"
                ? " " + fmtTotal(edge.line)
                : "";
          return `<div class="bp-row" data-game="${esc(game.id)}">
            <span class="bp-match">${esc(teamShortZh(game.away.abbrev, game.away.name))} @ ${esc(teamShortZh(game.home.abbrev, game.home.name))}</span>
            <span class="bp-pick">${edgeMarketLabel(edge.market)} · ${esc(side)}${line}</span>
            <span class="bp-price">@${fmtPrice(edge.price)}</span>
            <span class="bp-book">${esc(edge.book || "")}</span>
            <span class="bp-ev">${fmtSignedPct(edge.ev)}</span>
            <button class="btn small" data-quick-bet="${edgeIndex}" data-game-id="${esc(game.id)}">记一笔</button>
          </div>`;
        })
        .join("")}
    </div>`;
}

function renderGames(games, betsByGame = {}) {
  const box = $("#games");
  if (!games.length) {
    box.innerHTML = `<div class="empty">当天没有比赛。换一天，或先运行 <code>python -m app.cli ingest --date ${state.date}</code> 拉取数据。</div>`;
    return;
  }
  box.innerHTML = games
    .map((game) => {
      const info = statusInfo(game);
      const pred = game.prediction;
      const best = game.best_edge;
      const bettable = bettableEdgesOf(game)[0] || null;
      const shown = bettable || best;
      const bets = betsByGame[game.id] || [];
      const modelText = pred
        ? `模型 ${teamShortZh(game.home.abbrev, game.home.name)} ${fmtSpread(-pred.pred_margin)} · 总分 ${fmtTotal(pred.pred_total)}`
        : "尚未生成预测";
      const marketText = pred && pred.lines
        ? `盘口 ${fmtSpread(pred.lines.spread)} / ${fmtTotal(pred.lines.total)}`
        : "盘口待录入";
      const score =
        game.home_score !== null && game.away_score !== null
          ? `<span class="score final-score">${game.away_score} : ${game.home_score}</span>`
          : game.live_home_score != null && game.live_away_score != null && info.cls === "live"
            ? `<span class="score live-score" title="实时比分">${game.live_away_score} : ${game.live_home_score}</span>`
            : "";
      const shownIndex = shown ? (game.edges || []).indexOf(shown) : -1;
      const expired = shown && shown.expired;
      const badge = expired
        ? `<div class="edge-badge muted"><span>信号已过期 · ${esc(shown.expired_reason || "比赛已开始或超时")}</span><span>—</span></div>`
        : shown
        ? `<div class="edge-badge ${bettable ? "bettable" : ""}">
             ${bettable ? `<span class="chip-bettable">可下注</span>` : ""}
             <span class="label">${edgeText(shown, game)}</span>
             <span class="ev">${fmtSignedPct(shown.ev)}${shown.created_at ? `<span class="edge-time" title="信号生成时间">${fmtSignalTime(shown.created_at)}</span>` : ""}</span>
             ${shown.signal && shownIndex >= 0 ? `<button class="btn small quick-bet" data-quick-bet="${shownIndex}" data-game-id="${esc(game.id)}">记一笔</button>` : ""}
           </div>`
        : noOdds(game)
          ? `<div class="edge-badge muted"><span>暂无盘口报价</span><span>—</span></div>`
          : `<div class="edge-badge muted"><span>暂无正 EV 信号</span><span>—</span></div>`;
      return `
        <article class="card ${best && best.signal ? "signal" : ""} ${bettable ? "bettable" : ""} ${bets.length ? "has-bet" : ""}" data-game="${esc(game.id)}">
          ${bettable ? `<div class="bettable-strip">✓ 可下注</div>` : ""}
          <div class="card-top">
            <span>${fmtDayTime(game.tipoff)} · ${esc(teamNameZh(game.home.name, game.home.abbrev))}</span>
            <span style="display:flex;gap:6px;align-items:center">
              ${bets.length ? `<span class="bet-tag" title="本场有 ${bets.length} 条未结算纸面注单">注单 ${bets.length}</span>` : ""}
              <span class="status ${info.cls}">${info.text}</span>
            </span>
          </div>
          <div class="matchup">
            ${esc(teamShortZh(game.away.abbrev, game.away.name))}<span class="at">@</span>${esc(teamShortZh(game.home.abbrev, game.home.name))}${score}
          </div>
          <div class="meta">
            <span>${modelText}</span>
            <span>${marketText}</span>
          </div>
          ${bets.length ? `<div class="bet-row">${bets.map((bet) => slateBetChip(bet, game)).join("")}</div>` : ""}
          ${badge}
        </article>
      `;
    })
    .join("");
}

async function load({ repredict = false, silent = false } = {}) {
  const button = $("#refresh");
  const prevText = button.textContent;
  button.disabled = true;
  if (!silent) button.textContent = repredict ? "重算中…" : "加载中…";
  try {
    if (repredict && window.APP_MODE === "api") {
      await api("/api/predict", { method: "POST", body: JSON.stringify({ date: state.date }) });
    }
    if (window.APP_MODE === "static") {
      // 静态模式：重新拉数据通道（坚果云 → Pages 兜底），有新快照才重渲染
      const source = await window.refreshStaticPayload();
      if (source.endsWith("same")) {
        button.disabled = false;
        if (!silent) {
          button.textContent = "刷新";
          toast("数据已是最新（" + (window.STATIC_PAYLOAD?.generated_at || "").replace("T", " ").slice(0, 16) + "）");
        }
        return;
      }
    }
    const data = await api(`/api/games?date=${encodeURIComponent(state.date)}`);
    // 静态模式下后端只导出某一个比赛日：以 payload 的日期为准，
    // 否则赛时页会去拉一个不在导出范围内的日期（显示 0 场/英文兜底）。
    if (data.date && data.date !== state.date) {
      state.date = data.date;
      const input = document.getElementById("date");
      if (input) input.value = data.date;
    }
    if (!data.count) {
      const jumped = await autoAdjustDate();
      if (jumped) {
        button.disabled = false;
        button.textContent = "刷新";
        return load();
      }
    }
    state.games = data.games;
    // 纸面注单（含已结算）：按比赛聚合，看板卡片显示投注内容与结果
    let betsByGame = {};
    try {
      const betData = await api("/api/bets");
      for (const bet of betData.bets || []) {
        (betsByGame[bet.game_id] ||= []).push(bet);
      }
    } catch (_) {
      /* 注单接口失败不影响看板 */
    }
    renderStats(data);
    renderBettablePanel(data.games);
    renderGames(data.games, betsByGame);
    // APK：看板打开时顺手抓一份快照（ESPN 免费 + 可选 Odds API），失败静默
    if (window.DAV?.davEnabled() && window.SnapCollector) {
      window.SnapCollector.collectAndPush(state.date).catch(() => {});
      $("#snapshot").hidden = false;
    } else {
      $("#snapshot").hidden = true;
    }
    loadBankroll();
  } catch (error) {
    $("#games").innerHTML = `<div class="empty">加载失败：${esc(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = prevText;
  }
}

// 静态模式（手机）看板自动刷新：比赛进行中状态/信号每分钟自动跟进，
// 只在页面可见时跑；无新数据时静默（不弹提示）。
setInterval(() => {
  if (document.hidden) return;
  if (window.APP_MODE !== "static") return;
  const slateTab = document.getElementById("tab-slate");
  if (!slateTab || slateTab.hidden) return;
  load({ silent: true });
}, 60000);

// 当日无比赛时，自动跳到最近有比赛的日期（优先之后的赛程，没有则往回找）。
// 返回是否发生了跳转。
async function autoAdjustDate() {
  try {
    const data = await api("/api/dates");
    const dates = (data.dates || []).map((item) => item.date);
    if (!dates.length) return false;
    const current = state.date;
    const target = dates.find((d) => d >= current) || [...dates].reverse().find((d) => d < current);
    if (!target || target === current) return false;
    state.date = target;
    $("#date").value = target;
    toast(`当日没有比赛，已跳到最近有比赛的日期：${target}`);
    return true;
  } catch (_) {
    return false;
  }
}

// 从注单台账跳转到对应比赛：
// - 进行中/中场 → 切到「赛时」并滚动到该场卡片（实时比分）
// - 未开赛/已完赛 → 切到「赛前看板」、切到该比赛日并滚动到卡片
function focusGameCard(container, gameId) {
  if (!container) return false;
  const card = container.querySelector(`[data-game="${gameId}"]`);
  if (!card) return false;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1800);
  return true;
}

window.jumpToGame = async function (gameId, slateDate, status) {
  if (!gameId) return;

  // 目标卡片出现前轮询等待（live/看板数据加载可能 3-15 秒），找到即滚动+高亮
  function waitForCard(containerId, maxTries = 60, onMiss = null) {
    let tries = 0;
    const tick = () => {
      if (focusGameCard(document.getElementById(containerId), gameId)) return;
      if (++tries < maxTries) {
        setTimeout(tick, 300);
        return;
      }
      if (onMiss) onMiss();
      else toast("未找到该场比赛：可能已结束或不在当前日期");
    };
    tick();
  }

  const live = status === "STATUS_IN_PROGRESS" || status === "STATUS_HALFTIME";
  if (live) {
    toast("正在加载实时比分…");
    document.querySelector('#tabs [data-tab="live"]')?.click();
    // 赛时页没找到（静态模式 ESPN 只返回部分场次）→ 回退到赛前看板该场
    waitForCard("live-games", 45, () => {
      document.querySelector('#tabs [data-tab="slate"]')?.click();
      waitForCard("games", 15);
    });
    return;
  }
  if (slateDate && slateDate !== state.date) {
    state.date = slateDate;
    const input = document.getElementById("date");
    if (input) input.value = slateDate;
    document.querySelector('#tabs [data-tab="slate"]')?.click();
    await load({ silent: true });
  } else {
    document.querySelector('#tabs [data-tab="slate"]')?.click();
  }
  waitForCard("games", 20);
};

async function loadBankroll() {
  try {
    const summary = await api("/api/bets/summary");
    document.querySelector("[data-bankroll]")?.remove();
    const node = document.createElement("div");
    node.className = "stat";
    node.setAttribute("data-bankroll", "1");
    const roi = summary.roi != null ? fmtSignedPct(summary.roi) : "—";
    const clv = summary.avg_clv != null ? fmtSignedPct(summary.avg_clv) : "—";
    node.innerHTML = `<div class="k">纸面注单</div><div class="v" style="font-size:14px">${summary.settled} 结算 · ROI ${roi} · CLV ${clv}</div>`;
    $("#stats").appendChild(node);
  } catch (_) {
    /* bankroll stats are optional */
  }
}

function sideOptions(market) {
  if (market === "total") return [["over", "大分"], ["under", "小分"]];
  return [["home", "主队"], ["away", "客队"]];
}

function defaultLine(market) {
  const pred = state.detail?.game?.prediction;
  if (!pred || !pred.lines) return "";
  if (market === "spread") return pred.lines.spread ?? "";
  if (market === "total") return pred.lines.total ?? "";
  return "";
}

function edgeMarketLabel(market) {
  return market === "spread" ? "让分" : market === "total" ? "大小分" : "胜负";
}

function edgeSideLabel(edge, game) {
  if (edge.side === "home") return teamShortZh(game.home.abbrev, game.home.name);
  if (edge.side === "away") return teamShortZh(game.away.abbrev, game.away.name);
  return edge.side === "over" ? "大分" : "小分";
}

function betLiveState(bet, game) {
  // 进行中比赛的注单实时状态（赢盘/输盘/走盘线）；完赛/未开赛返回 null
  const hs = game.live_home_score ?? game.home_score;
  const as = game.live_away_score ?? game.away_score;
  if (hs == null || as == null) return null;
  if (game.status !== "STATUS_IN_PROGRESS") return null;
  const margin = hs - as;
  const total = hs + as;
  if (bet.market === "moneyline") {
    return (bet.side === "home") === margin > 0 ? "目前领先" : "目前落后";
  }
  if (bet.market === "spread") {
    const homeLine = bet.side === "home" ? Number(bet.line ?? 0) : -Number(bet.line ?? 0);
    const diff = margin + homeLine;
    if (Math.abs(diff) < 1e-9) return "贴盘";
    return (bet.side === "home") === diff > 0 ? "目前赢盘" : "目前输盘";
  }
  if (bet.market === "total") {
    const line = Number(bet.line ?? 0);
    if (Math.abs(total - line) < 1e-9) return "贴线";
    return (bet.side === "over") === total > line ? "目前赢盘" : "目前输盘";
  }
  return null;
}

function renderModal(data) {
  const game = data.game;
  const pred = game.prediction;
  const info = statusInfo(game);
  const edges = game.edges || [];
  const marketMargin = pred?.lines?.spread != null ? -pred.lines.spread : null;
  const divergence =
    pred && marketMargin != null ? Math.abs(pred.pred_margin - marketMargin) : 0;
  const injuries = data.injuries || [];
  const suggestions = data.injury_suggestions || [];
  const suggestionFor = (injury) => suggestions.find((s) => s.player === injury.player);
  const gameBets = data.bets || [];
  const openBets = gameBets.filter((bet) => bet.result === "open");
  const bettableCount = edges.filter((edge) => edge.signal && edge.bettable).length;
  const edgeRows = edges
    .map(
      (edge, index) => {
        const existing = betForKey(openBets, edge);
        const action = existing
          ? `<span data-bet-cell="${index}"><span class="pill signal" title="注 ${Number(existing.stake).toFixed(1)} @ ${fmtPrice(existing.price)}">已记录</span></span>`
          : `<span data-bet-cell="${index}"><button class="btn small" data-bet="${index}">记一笔</button></span>`;
        return `
      <tr class="${edge.signal && edge.bettable ? "row-bettable" : ""}">
        <td>${edgeMarketLabel(edge.market)}</td>
        <td>${esc(edgeSideLabel(edge, game))}
          ${edge.market === "spread" ? " " + fmtSpread(edge.line) : edge.market === "total" ? " " + fmtTotal(edge.line) : ""}</td>
        <td>${fmtPrice(edge.price)}</td>
        <td>${fmtPct(edge.model_prob)}</td>
        <td>${fmtPct(edge.fair_prob)}</td>
        <td class="${edge.edge >= 0 ? "pos" : "neg"}">${fmtSignedPct(edge.edge)}</td>
        <td class="${edge.ev >= 0 ? "pos" : "neg"}">${fmtSignedPct(edge.ev)}</td>
        <td style="color:var(--muted)">${edge.created_at ? fmtSignalTime(edge.created_at) : "—"}</td>
        <td>${edge.expired
          ? `<span class="pill" title="${esc(edge.expired_reason || "信号已过期")}" style="color:var(--muted)">已过期</span>`
          : edge.signal
            ? `<span class="pill signal">信号</span>`
            : `<span class="pill">观察</span>`}
          ${edge.signal && edge.bettable ? `<span class="pill signal pos" title="通过可下注闸门">可下注</span>` : ""}
          ${edge.signal && !edge.bettable && edge.bettable_note ? `<span class="pill" title="${esc(edge.bettable_note)}" style="color:var(--muted)">仅观察</span>` : ""}</td>
        <td>${edge.signal ? action : "—"}</td>
      </tr>`;
      }
    )
    .join("");

  const betRows = openBets.length
    ? openBets
        .map((bet) => {
          const lineText =
            bet.line != null ? (bet.market === "spread" ? " " + fmtSpread(bet.line) : " " + fmtTotal(bet.line)) : "";
          const side = bet.side === "home" ? "主" : bet.side === "away" ? "客" : bet.side === "over" ? "大" : "小";
          const market = edgeMarketLabel(bet.market);
          return `
        <div class="inj">
          <span>${market} · ${side}${lineText} @ ${fmtPrice(bet.price)} · 注 ${Number(bet.stake).toFixed(1)}${betLiveState(bet, game) ? ` · <span class="pill signal">${betLiveState(bet, game)}</span>` : ""}</span>
          <button class="btn small" data-del-bet="${bet.id}" title="撤销未结算注单">撤销</button>
        </div>`;
        })
        .join("")
    : `<div class="inj" style="color:var(--muted)">本场还没有未结算的纸面注单。</div>`;
  const injuryRows = injuries.length
    ? injuries
        .map((injury) => {
          const team =
    injury.team_id === game.home.id
      ? teamShortZh(game.home.abbrev, game.home.name)
      : teamShortZh(game.away.abbrev, game.away.name);
          const suggestion = suggestionFor(injury);
          const applyButton = suggestion
            ? `<button class="btn small" data-apply-impact="${injury.id}" title="RAPM ${suggestion.rapm_rating >= 0 ? "+" : ""}${suggestion.rapm_rating} → 建议 ${suggestion.suggested_impact}">采用 ${suggestion.suggested_impact}</button>`
            : "";
          const displayName = injury.player_zh || injury.player;
          return `
            <div class="inj">
              <span title="${esc(injury.player)}">${esc(team)} · ${esc(displayName)} <span class="s">${esc(injury.status || "")}</span>
                <span style="color:var(--muted)">${esc(injury.detail || "")}</span></span>
              <span style="display:flex;gap:6px;align-items:center">
                ${applyButton}
                <input type="number" step="0.5" value="${Number(injury.impact).toFixed(1)}"
                  data-impact="${injury.id}" style="width:70px;padding:4px 6px" title="对分差的影响（分）">
                <button class="btn small" data-save-impact="${injury.id}">保存</button>
              </span>
            </div>`;
        })
        .join("")
    : `<div class="inj" style="color:var(--muted)">暂无伤停数据（赛前刷新会从 ESPN 摘要抓取）</div>`;

  const odds = data.odds || [];
  const cons = pred?.consensus || {};

  function groupOdds(rows) {
    const spread = {}, total = {}, moneyline = {};
    for (const r of rows) {
      const target =
        r.market === "spread" ? spread : r.market === "total" ? total : r.market === "moneyline" ? moneyline : null;
      if (!target) continue;
      const t = target[r.book] || (target[r.book] = { book: r.book, captured: "" });
      if (r.market === "spread") {
        if (r.side === "home") { t.line = r.line; t.home_price = r.price; }
        else { t.away_line = r.line; t.away_price = r.price; }
      } else if (r.market === "total") {
        if (r.side === "over") { t.line = r.line; t.over_price = r.price; }
        else { t.under_price = r.price; }
      } else {
        if (r.side === "home") t.home_price = r.price; else t.away_price = r.price;
      }
      if ((r.captured_at || "") > t.captured) t.captured = r.captured_at;
    }
    return {
      spread: Object.values(spread).sort((a, b) => (a.book > b.book ? 1 : -1)),
      total: Object.values(total).sort((a, b) => (a.book > b.book ? 1 : -1)),
      moneyline: Object.values(moneyline).sort((a, b) => (a.book > b.book ? 1 : -1)),
    };
  }
  const grouped = groupOdds(odds);
  const fmtT = (v) => (v ? esc(v.replace("T", " ").slice(5, 16)) : "—");

  function oddsTable(head, rowsHtml) {
    return `<div class="table-wrap odds-table"><table>
      <thead><tr>${head}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`;
  }

  let oddsBlocks = "";
  if (grouped.spread.length) {
    const bestH = Math.max(...grouped.spread.map((r) => r.home_price || 0));
    const bestA = Math.max(...grouped.spread.map((r) => r.away_price || 0));
    oddsBlocks += `<h4>让分（共识线 ${fmtSpread(cons.spread_line ?? grouped.spread[0]?.line)} · ${cons.n_books_spread || grouped.spread.length} 家）</h4>` +
      oddsTable("<th>书源</th><th>主线</th><th>主价</th><th>客价</th><th>更新</th>",
        grouped.spread.map((r) => `
          <tr>
            <td>${esc(r.book)}</td>
            <td>${fmtSpread(r.line)}</td>
            <td class="${r.home_price === bestH ? "pos" : ""}">${fmtPrice(r.home_price)}</td>
            <td class="${r.away_price === bestA ? "pos" : ""}">${fmtPrice(r.away_price)}</td>
            <td style="color:var(--muted)">${fmtT(r.captured)}</td>
          </tr>`).join(""));
  }
  if (grouped.total.length) {
    const bestO = Math.max(...grouped.total.map((r) => r.over_price || 0));
    const bestU = Math.max(...grouped.total.map((r) => r.under_price || 0));
    oddsBlocks += `<h4>大小分（共识线 ${fmtTotal(cons.total_line ?? grouped.total[0]?.line)} · ${cons.n_books_total || grouped.total.length} 家）</h4>` +
      oddsTable("<th>书源</th><th>线</th><th>大价</th><th>小价</th><th>更新</th>",
        grouped.total.map((r) => `
          <tr>
            <td>${esc(r.book)}</td>
            <td>${fmtTotal(r.line)}</td>
            <td class="${r.over_price === bestO ? "pos" : ""}">${fmtPrice(r.over_price)}</td>
            <td class="${r.under_price === bestU ? "pos" : ""}">${fmtPrice(r.under_price)}</td>
            <td style="color:var(--muted)">${fmtT(r.captured)}</td>
          </tr>`).join(""));
  }
  if (grouped.moneyline.length) {
    const bestH = Math.max(...grouped.moneyline.map((r) => r.home_price || 0));
    const bestA = Math.max(...grouped.moneyline.map((r) => r.away_price || 0));
    oddsBlocks += `<h4>独赢（${cons.n_books_ml || grouped.moneyline.length} 家）</h4>` +
      oddsTable("<th>书源</th><th>主价</th><th>客价</th><th>更新</th>",
        grouped.moneyline.map((r) => `
          <tr>
            <td>${esc(r.book)}</td>
            <td class="${r.home_price === bestH ? "pos" : ""}">${fmtPrice(r.home_price)}</td>
            <td class="${r.away_price === bestA ? "pos" : ""}">${fmtPrice(r.away_price)}</td>
            <td style="color:var(--muted)">${fmtT(r.captured)}</td>
          </tr>`).join(""));
  }

  $("#modal-body").innerHTML = `
    <h2>${esc(teamNameZh(game.away.name, game.away.abbrev))} <span style="color:var(--muted)">@</span> ${esc(teamNameZh(game.home.name, game.home.abbrev))}</h2>
    <div class="sub">${fmtTime(game.tipoff)} · ${info.text} · ${esc(game.season || "")} ${game.venue ? "· " + esc(game.venue) : ""}</div>

    ${
      pred
        ? `<div class="model-grid">
            <div class="model-cell"><div class="k">预测分差（主队）</div><div class="v">${fmtSpread(pred.pred_margin)}</div></div>
            <div class="model-cell"><div class="k">预测总分</div><div class="v">${fmtTotal(pred.pred_total)}</div></div>
            <div class="model-cell"><div class="k">主胜概率</div><div class="v">${fmtPct(pred.home_win_prob)}</div></div>
            <div class="model-cell"><div class="k">不确定性 σ</div><div class="v" style="font-size:14px">${fmtTotal(pred.sigma_margin)} / ${fmtTotal(pred.sigma_total)}</div></div>
          </div>`
        : `<div class="empty" style="margin:12px 0">尚未生成预测。先运行 <code>python -m app.cli predict --date ${state.date}</code>。</div>`
    }

    ${
      pred?.pricing
        ? `<div class="sub">定价融合：模型 ${Math.round(pred.pricing.market_blend * 100)}% + 市场 ${Math.round((1 - pred.pricing.market_blend) * 100)}% · 融合分差 ${fmtSpread(pred.pricing.margin)} / 总分 ${fmtTotal(pred.pricing.total)}</div>`
        : ""
    }
    ${
      divergence > 5
        ? `<div class="warn">模型与市场分差相差 ${divergence.toFixed(1)} 分。先检查伤停、轮换、数据同步，再考虑信号。</div>`
        : ""
    }

    ${
      bettableCount
        ? `<div class="bettable-banner">本场有 <b>${bettableCount}</b> 条<b>可下注</b>信号（已通过软书价 / 共识深度 / 同场互斥闸门）。仍属纸面跟踪，不构成投注建议。</div>`
        : ""
    }
    <h3>模型 vs 盘口</h3>
    ${
      edgeRows
        ? `<div class="table-wrap"><table>
            <thead><tr><th>市场</th><th>方向</th><th>赔率</th><th>模型</th><th>公允</th><th>Edge</th><th>EV</th><th>时间</th><th>状态</th><th></th></tr></thead>
            <tbody>${edgeRows}</tbody>
          </table></div>`
        : `<div class="inj" style="color:var(--muted)">没有可用盘口。可以在下方手动录入你看到的盘口。</div>`
    }

    <h3>本场纸面注单（未结算）${window.APP_MODE === "static" ? `<span class="bets-scope"></span>` : ""}</h3>
    <div class="inj-list">${betRows}</div>

    <h3>伤停 / 轮换（impact 为对分差的影响，正数表示该队损失）</h3>
    <div class="inj-list">${injuryRows}</div>

    <h3>手动录入盘口</h3>
    <div class="form-grid">
      <div><label>市场</label>
        <select id="m-market">
          <option value="spread">让分</option>
          <option value="total">大小分</option>
          <option value="moneyline">胜负</option>
        </select>
      </div>
      <div><label>方向</label><select id="m-side"></select></div>
      <div><label>盘口</label><input id="m-line" type="number" step="0.5" value=""></div>
      <div><label>小数赔率</label><input id="m-price" type="number" step="0.01" value="1.91"></div>
      <div><label>来源</label><input id="m-book" type="text" value="manual"></div>
      <div style="grid-column: span 2"><button class="btn primary" id="m-submit">保存盘口并重算</button></div>
    </div>

    <h3>盘口快照（按市场分组 · 最优价高亮）</h3>
    ${oddsBlocks || `<div class="inj" style="color:var(--muted)">暂无盘口快照。</div>`}
  `;

  const marketSelect = $("#m-market");
  const sideSelect = $("#m-side");
  const lineInput = $("#m-line");

  function refreshSideOptions() {
    const market = marketSelect.value;
    sideSelect.innerHTML = sideOptions(market)
      .map(([value, label]) => `<option value="${value}">${label}</option>`)
      .join("");
    lineInput.disabled = market === "moneyline";
    lineInput.value = defaultLine(market);
  }
  marketSelect.addEventListener("change", refreshSideOptions);
  refreshSideOptions();

  $("#m-submit").addEventListener("click", async () => {
    const market = marketSelect.value;
    const price = Number($("#m-price").value);
    const line = market === "moneyline" ? null : Number(lineInput.value);
    if (!Number.isFinite(price) || price <= 1) return toast("赔率必须大于 1");
    if (market !== "moneyline" && !Number.isFinite(line)) return toast("请输入盘口");
    try {
      await api("/api/odds", {
        method: "POST",
        body: JSON.stringify({
          game_id: game.id,
          market,
          side: sideSelect.value,
          line,
          price,
          book: $("#m-book").value.trim() || "manual",
        }),
      });
      await api("/api/predict", { method: "POST", body: JSON.stringify({ date: state.date }) });
      toast("盘口已保存，预测已重算");
      await openGame(game.id);
    } catch (error) {
      toast(`保存失败：${error.message}`);
    }
  });
}

async function openGame(gameId) {
  try {
    const [data, betsData] = await Promise.all([
      api(`/api/games/${encodeURIComponent(gameId)}`),
      api(`/api/bets?game_id=${encodeURIComponent(gameId)}`).catch(() => ({ bets: [] })),
    ]);
    data.bets = betsData.bets || [];
    state.detail = data;
    renderModal(data);
    $("#modal").hidden = false;
  } catch (error) {
    toast(`加载比赛失败：${error.message}`);
  }
}

function openBetKey(bet) {
  return `${bet.market}|${bet.side}|${bet.line ?? ""}|`;
}

function betForKey(bets, edge) {
  return (bets || []).find(
    (bet) =>
      bet.result === "open" &&
      bet.market === edge.market &&
      bet.side === edge.side &&
      (bet.line ?? null) === (edge.line ?? null)
  );
}

function placeBet(index) {
  const edge = state.detail?.game?.edges?.[index];
  const cell = document.querySelector(`[data-bet-cell="${index}"]`);
  if (!edge || !cell) return;
  // 内联表单：注额 + 实际成交价 + 盘口（可改，默认取信号值）
  const lineInput =
    edge.market === "moneyline"
      ? ""
      : `<input type="number" step="0.5" value="${edge.line ?? ""}" data-bet-line="${index}"
           style="width:70px;padding:4px 6px" title="盘口（可改成你实际看到的）">`;
  cell.innerHTML = `
    <span style="display:inline-flex;gap:4px;align-items:center;white-space:nowrap">
      <input type="number" step="0.5" min="0.5" value="1" data-stake="${index}"
        style="width:56px;padding:4px 6px" title="注额（单位），建议仓位 ${(edge.stake_pct * 100).toFixed(1)}%">
      ${lineInput}
      <input type="number" step="0.01" min="1.01" value="${Number(edge.price).toFixed(2)}" data-bet-price="${index}"
        style="width:64px;padding:4px 6px" title="成交赔率（可改成你实际拿到的）">
      <button class="btn small primary" data-confirm-bet="${index}">确认</button>
    </span>`;
  cell.querySelector("input")?.focus();
}

async function confirmBet(index) {
  const edge = state.detail?.game?.edges?.[index];
  const stakeInput = document.querySelector(`input[data-stake="${index}"]`);
  if (!edge || !stakeInput) return;
  const stake = Number(stakeInput.value);
  if (!Number.isFinite(stake) || stake <= 0) return toast("注额无效");
  const lineInput = document.querySelector(`input[data-bet-line="${index}"]`);
  const priceInput = document.querySelector(`input[data-bet-price="${index}"]`);
  const line = lineInput ? Number(lineInput.value) : null;
  const price = priceInput ? Number(priceInput.value) : Number(edge.price);
  if (edge.market !== "moneyline" && !Number.isFinite(line)) return toast("请输入盘口");
  if (!Number.isFinite(price) || price <= 1) return toast("赔率必须大于 1");
  try {
    await api("/api/bets", {
      method: "POST",
      body: JSON.stringify({
        game_id: state.detail.game.id,
        slate_date: state.date,
        matchup: `${teamShortZh(state.detail.game.away.abbrev, state.detail.game.away.name)}@${teamShortZh(state.detail.game.home.abbrev, state.detail.game.home.name)}`,
        market: edge.market,
        side: edge.side,
        line,
        price,
        stake,
        model_prob: edge.model_prob,
        ev: edge.ev,
        note: `${edge.book} / model ${fmtPct(edge.model_prob)} / fair ${fmtPct(edge.fair_prob)} / edge ${fmtSignedPct(edge.edge)}`,
      }),
    });
    toast("已记录纸面注单");
    await openGame(state.detail.game.id);
    loadBankroll();
  } catch (error) {
    toast(`记录失败：${error.message}`);
  }
}

async function deleteBet(betId) {
  try {
    await api(`/api/bets/${betId}`, { method: "DELETE" });
    toast("已撤销注单");
    await openGame(state.detail.game.id);
    loadBankroll();
  } catch (error) {
    toast(`撤销失败：${error.message}`);
  }
}

async function saveImpact(injuryId) {
  const input = document.querySelector(`input[data-impact="${injuryId}"]`);
  if (!input) return;
  const impact = Number(input.value);
  if (!Number.isFinite(impact)) return toast("impact 无效");
  try {
    await api(`/api/injuries/${injuryId}/impact`, {
      method: "POST",
      body: JSON.stringify({ impact }),
    });
    await api("/api/predict", { method: "POST", body: JSON.stringify({ date: state.date }) });
    toast("伤停影响已更新，预测已重算");
    await openGame(state.detail.game.id);
  } catch (error) {
    toast(`保存失败：${error.message}`);
  }
}

async function applySuggestion(injuryId) {
  try {
    const result = await api(`/api/injuries/${injuryId}/apply-suggestion`, { method: "POST" });
    toast(`已采用 RAPM 建议 impact=${result.impact}（评分 ${result.rapm_rating}），预测已重算`);
    await api("/api/predict", { method: "POST", body: JSON.stringify({ date: state.date }) });
    await openGame(state.detail.game.id);
  } catch (error) {
    toast(`采用失败：${error.message}`);
  }
}

let toastTimer = null;
function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2400);
}

$("#date").value = state.date;
$("#date").addEventListener("change", (event) => {
  state.date = event.target.value || isoToday();
  load();
  window.loadLive?.();
});
$("#refresh").addEventListener("click", () => load({ repredict: true }));
$("#close").addEventListener("click", () => {
  $("#modal").hidden = true;
});
$("#snapshot").addEventListener("click", async () => {
  const button = $("#snapshot");
  button.disabled = true;
  button.textContent = "抓取中…";
  try {
    const result = await window.SnapCollector.collectAndPush(state.date);
    toast(`快照已上传：${result.events} 场 / ${result.books} 家书源`);
  } catch (error) {
    toast(`快照失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "抓快照";
  }
});
$("#modal").addEventListener("click", (event) => {
  if (event.target.id === "modal") $("#modal").hidden = true;
});
$("#games").addEventListener("click", async (event) => {
  const quick = event.target.closest("[data-quick-bet]");
  const card = event.target.closest("[data-game]");
  if (!card) return;
  if (quick) {
    event.stopPropagation();
    await openGame(quick.dataset.gameId || card.dataset.game);
    placeBet(Number(quick.dataset.quickBet));
    return;
  }
  openGame(card.dataset.game);
});
$("#bettable-panel").addEventListener("click", async (event) => {
  const quick = event.target.closest("[data-quick-bet]");
  if (quick) {
    event.stopPropagation();
    await openGame(quick.dataset.gameId);
    placeBet(Number(quick.dataset.quickBet));
    return;
  }
  const row = event.target.closest(".bp-row[data-game]");
  if (row) openGame(row.dataset.game);
});
$("#modal-body").addEventListener("click", (event) => {
  const betButton = event.target.closest("[data-bet]");
  if (betButton) placeBet(Number(betButton.dataset.bet));
  const confirmButton = event.target.closest("[data-confirm-bet]");
  if (confirmButton) confirmBet(Number(confirmButton.dataset.confirmBet));
  const deleteButton = event.target.closest("[data-del-bet]");
  if (deleteButton) deleteBet(Number(deleteButton.dataset.delBet));
  const applyButton = event.target.closest("[data-apply-impact]");
  if (applyButton) applySuggestion(Number(applyButton.dataset.applyImpact));
  const impactButton = event.target.closest("[data-save-impact]");
  if (impactButton) saveImpact(Number(impactButton.dataset.saveImpact));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("#modal").hidden = true;
});

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}

load();
