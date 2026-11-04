// 手机采集端（阶段 2/3）：ESPN（免费）+ The Odds API（可选，key 存本机）。
// 输出统一成 The Odds API 的 events 形状（home_team/away_team/bookmakers），
// PC 端 theodds.ingest_events 直接复用同一套匹配+入库逻辑。
// 价格统一十进制（ESPN 的美式赔率在本地换算），与 PC 入库口径一致。

const SNAP_TAKER = "phone";

function americanToDecimalJs(value) {
  if (value === null || value === undefined || value === "") return null;
  const american = Number(String(value).replace("+", ""));
  if (!Number.isFinite(american) || american === 0) return null;
  return american > 0 ? 1 + american / 100 : 1 + 100 / Math.abs(american);
}

function espnEventToOddsApiShape(event) {
  const comp = (event.competitions || [{}])[0];
  const competitors = comp.competitors || [];
  const home = competitors.find((c) => c.homeAway === "home");
  const away = competitors.find((c) => c.homeAway === "away");
  if (!home || !away) return null;
  const homeName = (home.team || {}).displayName || "";
  const awayName = (away.team || {}).displayName || "";

  const bookmakers = [];
  for (const odds of comp.odds || []) {
    const title = ((odds.provider || {}).name || "unknown").trim();
    const markets = [];

    const spreadOutcomes = [];
    const ps = odds.pointSpread || {};
    const spreadHome = ((ps.home || {}).close || {});
    const spreadAway = ((ps.away || {}).close || {});
    for (const [side, name, close] of [
      ["home", homeName, spreadHome],
      ["away", awayName, spreadAway],
    ]) {
      let point = close.line != null ? Number(close.line) : null;
      if (point == null && odds.spread != null) {
        point = side === "home" ? Number(odds.spread) : -Number(odds.spread);
      }
      const price = americanToDecimalJs(close.odds);
      if (point != null && price != null) spreadOutcomes.push({ name, price, point });
    }
    if (spreadOutcomes.length) markets.push({ key: "spreads", outcomes: spreadOutcomes });

    const totalOutcomes = [];
    const total = odds.total || {};
    let totalLine = odds.overUnder != null ? Number(odds.overUnder) : null;
    if (totalLine == null) totalLine = ((total.over || {}).close || {}).line ?? ((total.under || {}).close || {}).line;
    const overPrice = americanToDecimalJs(((total.over || {}).close || {}).odds);
    const underPrice = americanToDecimalJs(((total.under || {}).close || {}).odds);
    if (totalLine != null && overPrice != null) totalOutcomes.push({ name: "Over", price: overPrice, point: Number(totalLine) });
    if (totalLine != null && underPrice != null) totalOutcomes.push({ name: "Under", price: underPrice, point: Number(totalLine) });
    if (totalOutcomes.length) markets.push({ key: "totals", outcomes: totalOutcomes });

    const h2h = [];
    const ml = odds.moneyline || {};
    for (const [side, name] of [
      ["home", homeName],
      ["away", awayName],
    ]) {
      const price = americanToDecimalJs(((ml[side] || {}).close || {}).odds);
      if (price != null) h2h.push({ name, price });
    }
    if (h2h.length) markets.push({ key: "h2h", outcomes: h2h });

    if (markets.length) bookmakers.push({ title, markets });
  }

  if (!bookmakers.length) return null;
  return {
    id: String(event.id || ""),
    home_team: homeName,
    away_team: awayName,
    commence_time: event.date,
    bookmakers,
  };
}

async function collectSnapshots(date) {
  const ymd = date.replace(/-/g, "");
  const events = [];

  // 1) ESPN（免费，必抓）
  try {
    const resp = await fetch(
      `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=${ymd}`
    );
    if (resp.ok) {
      const sb = await resp.json();
      for (const event of sb.events || []) {
        const shaped = espnEventToOddsApiShape(event);
        if (shaped) events.push(shaped);
      }
    }
  } catch (_) {
    /* ESPN 拉不到就只用 Odds API */
  }

  // 2) The Odds API（可选：设置里填 key；注意消耗额度）
  const oddsKey = localStorage.getItem("nbaq:oddsapi-key");
  if (oddsKey) {
    try {
      const resp = await fetch(
        `https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey=${encodeURIComponent(
          oddsKey
        )}&regions=us,eu&markets=h2h,spreads,totals&oddsFormat=decimal`
      );
      if (resp.ok) {
        const apiEvents = await resp.json();
        const byTeams = new Map(events.map((e) => [e.home_team + "|" + e.away_team, e]));
        for (const ev of apiEvents) {
          const target = byTeams.get(ev.home_team + "|" + ev.away_team);
          if (target) {
            const seen = new Set(target.bookmakers.map((b) => b.title));
            for (const bookmaker of ev.bookmakers || []) {
              if (!seen.has(bookmaker.title)) target.bookmakers.push(bookmaker);
            }
          } else {
            events.push(ev); // ESPN 没有的比赛（比如就一两家报价）直接补进
          }
        }
      }
    } catch (_) {
      /*额度/网络问题不致命*/
    }
  }

  return {
    captured_at: new Date().toISOString(),
    taker: SNAP_TAKER,
    slate_date: date,
    events,
  };
}

async function collectAndPush(date) {
  if (!window.DAV?.davEnabled()) throw new Error("坚果云未配置（同步设置里填账号和应用密码）");
  const payload = await collectSnapshots(date);
  const books = payload.events.reduce((sum, e) => sum + e.bookmakers.length, 0);
  await window.DAV.davPutText(
    `nbaq/snapshots/inbox/snap-${Date.now()}.json`,
    JSON.stringify(payload)
  );
  return { events: payload.events.length, books, at: payload.captured_at };
}

window.SnapCollector = { collect: collectSnapshots, collectAndPush, espnEventToOddsApiShape };
