// 研究图表页：原生 canvas，零依赖。
// 数据源：/api/backtest（月度指标+逐场行）、/api/rapm、/api/trends。
// 风格与看板一致（深色，绿色主色）。仅在用户切到"研究图表"时加载。

const COLORS = {
  text: "#e8eef7",
  muted: "#8fa1b9",
  grid: "#243247",
  green: "#4ade80",
  red: "#f87171",
  blue: "#60a5fa",
  amber: "#fbbf24",
};

let researchLoaded = false;

function setupCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.parentElement.clientWidth - 32; // padding
  const height = Number(canvas.getAttribute("height")) || 240;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.font = "11px sans-serif";
  return { ctx, width, height };
}

function drawFrame(ctx, width, height, pad) {
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  ctx.strokeRect(pad.l, pad.t, width - pad.l - pad.r, height - pad.t - pad.b);
}

function yScale(height, pad, min, max) {
  const span = max - min || 1;
  return (value) => pad.t + (1 - (value - min) / span) * (height - pad.t - pad.b);
}

// 通用柱状图（可含零基线、第二序列折线）
function drawBars(canvas, labels, values, { line = null, lineColor = COLORS.blue, unit = "", zeroBase = false } = {}) {
  const { ctx, width, height } = setupCanvas(canvas);
  const pad = { l: 46, r: 12, t: 14, b: 30 };
  const lo = zeroBase ? Math.min(0, ...values) : Math.min(...values, ...(line || [Infinity]));
  const hi = zeroBase ? Math.max(0, ...values) : Math.max(...values, ...(line || [-Infinity]));
  const margin = (hi - lo) * 0.12 || 1;
  const yMin = lo - (zeroBase ? 0 : margin);
  const yMax = hi + margin;
  const y = yScale(height, pad, yMin, yMax);
  drawFrame(ctx, width, height, pad);

  // 网格与刻度
  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    ctx.fillText(v.toFixed(Math.abs(v) < 10 ? 1 : 0) + unit, pad.l - 6, y(v) + 4);
    if (i > 0) {
      ctx.strokeStyle = "#1b2636";
      ctx.beginPath();
      ctx.moveTo(pad.l, y(v));
      ctx.lineTo(width - pad.r, y(v));
      ctx.stroke();
    }
  }
  // 零线
  if (yMin < 0 && yMax > 0) {
    ctx.strokeStyle = COLORS.muted;
    ctx.beginPath();
    ctx.moveTo(pad.l, y(0));
    ctx.lineTo(width - pad.r, y(0));
    ctx.stroke();
  }

  const n = labels.length || 1;
  const slot = (width - pad.l - pad.r) / n;
  const barWidth = Math.min(slot * 0.6, 46);
  values.forEach((value, i) => {
    const cx = pad.l + slot * (i + 0.5);
    ctx.fillStyle = value >= 0 ? COLORS.green : COLORS.red;
    const top = Math.min(y(value), y(zeroBase ? 0 : yMin));
    const bottom = Math.max(y(value), y(zeroBase ? 0 : yMin));
    ctx.fillRect(cx - barWidth / 2, top, barWidth, Math.max(bottom - top, 2));
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "center";
    ctx.fillText(labels[i], cx, height - pad.b + 16);
    ctx.fillStyle = COLORS.text;
    ctx.fillText(value.toFixed(Math.abs(value) < 10 ? 2 : 1), cx, top - 5);
  });

  if (line && line.length === values.length) {
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    line.forEach((v, i) => {
      const cx = pad.l + slot * (i + 0.5);
      if (i === 0) ctx.moveTo(cx, y(v));
      else ctx.lineTo(cx, y(v));
    });
    ctx.stroke();
    ctx.fillStyle = lineColor;
    line.forEach((v, i) => {
      const cx = pad.l + slot * (i + 0.5);
      ctx.beginPath();
      ctx.arc(cx, y(v), 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }
}

function drawLineChart(canvas, labels, series, { min, max, unit = "" } = {}) {
  const { ctx, width, height } = setupCanvas(canvas);
  const pad = { l: 46, r: 12, t: 14, b: 30 };
  const yMin = min ?? Math.min(...series) - 2;
  const yMax = max ?? Math.max(...series) + 2;
  const y = yScale(height, pad, yMin, yMax);
  drawFrame(ctx, width, height, pad);

  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    ctx.fillText(v.toFixed(0) + unit, pad.l - 6, y(v) + 4);
    if (i > 0) {
      ctx.strokeStyle = "#1b2636";
      ctx.beginPath();
      ctx.moveTo(pad.l, y(v));
      ctx.lineTo(width - pad.r, y(v));
      ctx.stroke();
    }
  }

  const n = labels.length || 1;
  const slot = (width - pad.l - pad.r) / Math.max(n - 1, 1);
  ctx.strokeStyle = COLORS.green;
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((v, i) => {
    const cx = pad.l + slot * i;
    if (i === 0) ctx.moveTo(cx, y(v));
    else ctx.lineTo(cx, y(v));
  });
  ctx.stroke();
  ctx.fillStyle = COLORS.green;
  series.forEach((v, i) => {
    const cx = pad.l + slot * i;
    ctx.beginPath();
    ctx.arc(cx, y(v), 3, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "center";
  labels.forEach((label, i) => {
    if (labels.length > 9 && i % 2 === 1) return;
    ctx.fillText(label, pad.l + slot * i, height - pad.b + 16);
  });
}

function drawRapmChart(canvas, players) {
  const { ctx, width, height } = setupCanvas(canvas);
  const rows = players.slice(0, 24); // top12 + bottom12
  const pad = { l: 150, r: 40, t: 10, b: 10 };
  const values = rows.map((p) => p.rating);
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const x = (v) => pad.l + ((v + maxAbs) / (2 * maxAbs)) * (width - pad.l - pad.r);
  const rowH = (height - pad.t - pad.b) / rows.length;

  // 零线
  ctx.strokeStyle = COLORS.muted;
  ctx.beginPath();
  ctx.moveTo(x(0), pad.t);
  ctx.lineTo(x(0), height - pad.b);
  ctx.stroke();

  rows.forEach((p, i) => {
    const cy = pad.t + rowH * (i + 0.5);
    ctx.fillStyle = p.rating >= 0 ? COLORS.green : COLORS.red;
    const x0 = x(0);
    const x1 = x(p.rating);
    ctx.fillRect(Math.min(x0, x1), cy - rowH * 0.32, Math.abs(x1 - x0) || 2, rowH * 0.64);
    ctx.fillStyle = COLORS.text;
    ctx.textAlign = "right";
    ctx.font = "11px sans-serif";
    const label = p.name_zh || p.name;
    ctx.fillText(label.length > 12 ? label.slice(0, 11) + "…" : label, pad.l - 8, cy + 4);
    ctx.textAlign = p.rating >= 0 ? "left" : "right";
    // negative ratings: label on the right of the zero line so it doesn't
    // collide with the name column
    if (p.rating >= 0) ctx.fillText(p.rating.toFixed(1), x1 + 4, cy + 4);
    else ctx.fillText(p.rating.toFixed(1), x(0) + 4, cy + 4);
  });
}

function drawScatter(canvas, rows) {
  const { ctx, width, height } = setupCanvas(canvas);
  const pad = { l: 46, r: 12, t: 14, b: 34 };
  const lo = Math.min(...rows.map((r) => Math.min(r.pred, r.actual))) - 4;
  const hi = Math.max(...rows.map((r) => Math.max(r.pred, r.actual))) + 4;
  const sx = (v) => pad.l + ((v - lo) / (hi - lo)) * (width - pad.l - pad.r);
  const sy = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (height - pad.t - pad.b);

  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4;
    ctx.fillText(v.toFixed(0), pad.l - 6, sy(v) + 4);
    ctx.textAlign = "center";
    ctx.fillText(v.toFixed(0), sx(v), height - pad.b + 16);
    ctx.textAlign = "right";
  }
  // 对角线
  ctx.strokeStyle = COLORS.muted;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(sx(lo), sy(lo));
  ctx.lineTo(sx(hi), sy(hi));
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = "rgba(96, 165, 250, 0.55)";
  rows.forEach((r) => {
    ctx.beginPath();
    ctx.arc(sx(r.pred), sy(r.actual), 2.6, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "left";
  ctx.fillText("x=预测  y=实际", pad.l + 6, pad.t + 12);
}

function drawCalibration(canvas, buckets) {
  const { ctx, width, height } = setupCanvas(canvas);
  const pad = { l: 46, r: 12, t: 14, b: 34 };
  const y = yScale(height, pad, 0, 1);
  const x = (v) => pad.l + v * (width - pad.l - pad.r);

  ctx.fillStyle = COLORS.muted;
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const v = i / 4;
    ctx.fillText(`${Math.round(v * 100)}%`, pad.l - 6, y(v) + 4);
    ctx.textAlign = "center";
    ctx.fillText(`${Math.round(v * 100)}%`, x(v), height - pad.b + 16);
    ctx.textAlign = "right";
  }
  // 对角线（完美校准）
  ctx.strokeStyle = COLORS.muted;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x(0), y(0));
  ctx.lineTo(x(1), y(1));
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.strokeStyle = COLORS.amber;
  ctx.lineWidth = 2;
  ctx.beginPath();
  buckets.forEach((b, i) => {
    const cx = x(b.mid);
    const cy = y(b.winRate);
    if (i === 0) ctx.moveTo(cx, cy);
    else ctx.lineTo(cx, cy);
  });
  ctx.stroke();
  buckets.forEach((b) => {
    ctx.fillStyle = COLORS.amber;
    ctx.beginPath();
    ctx.arc(x(b.mid), y(b.winRate), 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = COLORS.muted;
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`n=${b.n}`, x(b.mid), y(b.winRate) - 10);
    ctx.font = "11px sans-serif";
  });
}

async function loadResearch() {
  const note = document.getElementById("research-note");
  try {
    const [backtest, rapm, trends] = await Promise.all([
      api("/api/backtest"),
      api("/api/rapm?limit=800").catch(() => null),
      api("/api/trends"),
    ]);

    const report = backtest.report;
    if (report && report.monthly && Object.keys(report.monthly).length) {
      const months = Object.keys(report.monthly);
      drawBars(
        document.getElementById("chart-bias"),
        months,
        months.map((m) => report.monthly[m].total_bias),
        { zeroBase: true, unit: "" }
      );
      drawBars(
        document.getElementById("chart-mae"),
        months,
        months.map((m) => report.monthly[m].total_mae),
        { line: months.map((m) => report.monthly[m].margin_mae), unit: "" }
      );

      const rows = (report.rows || []).map((r) => ({ pred: r.pred_total, actual: r.actual_total }));
      if (rows.length) drawScatter(document.getElementById("chart-scatter"), rows);

      // 校准：home_win_prob 分桶 vs 实际主胜率
      const edgesBuckets = [0, 0.4, 0.5, 0.6, 0.7, 1.0];
      const buckets = [];
      for (let i = 0; i < edgesBuckets.length - 1; i++) {
        const lo = edgesBuckets[i];
        const hi = edgesBuckets[i + 1];
        const inBucket = (report.rows || []).filter(
          (r) => r.home_win_prob >= lo && (i === edgesBuckets.length - 2 ? r.home_win_prob <= hi : r.home_win_prob < hi)
        );
        if (inBucket.length >= 5) {
          buckets.push({
            mid: (lo + hi) / 2,
            winRate: inBucket.reduce((s, r) => s + r.home_win, 0) / inBucket.length,
            n: inBucket.length,
          });
        }
      }
      if (buckets.length) drawCalibration(document.getElementById("chart-calib"), buckets);

      note.textContent = `回测样本 ${report.overall.n} 场（${report.test_start} 起）· bias ${report.overall.total_bias >= 0 ? "+" : ""}${report.overall.total_bias.toFixed(2)} · Brier ${report.overall.brier.toFixed(4)}`;
    } else {
      note.textContent = "暂无回测报告：先运行 python -m app.cli backtest --test-start 2026-01-01";
    }

    if (rapm && rapm.players && rapm.players.length) {
      const top = rapm.players.slice(0, 12);
      const bottom = rapm.players.slice(-12).reverse();
      drawRapmChart(document.getElementById("chart-rapm"), [...top, ...bottom]);
    } else {
      const { ctx, width, height } = setupCanvas(document.getElementById("chart-rapm"));
      ctx.fillStyle = COLORS.muted;
      ctx.textAlign = "center";
      ctx.fillText("暂无 RAPM：先运行 python -m app.cli rapm-train", width / 2, height / 2);
    }

    if (trends && trends.months && trends.months.length) {
      drawLineChart(
        document.getElementById("chart-trend"),
        trends.months.map((m) => m.slice(2)),
        trends.avg_total,
        { unit: "" }
      );
    }
    researchLoaded = true;
  } catch (error) {
    note.textContent = `研究数据加载失败：${error.message}`;
  }
}

// 通用折线图暴露给其他标签页复用（收益页的累计收益曲线）
// research.js 末尾：可下注闸门验收面板 + 路线 A 基准对照面板
async function loadGateCard() {
  const box = document.getElementById("gate-card");
  if (!box) return;
  try {
    const data = await api("/api/linemove");
    const o = data.overall;
    const lm = o && o.n_signals
      ? `
        <div class="table-wrap"><table>
          <thead><tr><th>检验</th><th>信号数</th><th>线有移动</th><th>顺线 / 反线</th><th>顺线比例</th><th>z 值</th><th>判定</th></tr></thead>
          <tbody>
            <tr>
              <td>信号→线移动方向</td>
              <td>${o.n_signals}</td><td>${o.n_with_move}</td>
              <td>${o.moved_toward} / ${o.moved_against}</td>
              <td>${o.toward_rate != null ? (o.toward_rate * 100).toFixed(1) + "%" : "—"}</td>
              <td>${o.z_stat != null ? o.z_stat : "—"}</td>
              <td>${o.z_stat != null && o.z_stat > 2 ? '<span class="pill signal pos">通过</span>' : '<span class="pill">未通过</span>'}</td>
            </tr>
          </tbody>
        </table></div>
        <p class="notice" style="border:none;margin-top:8px;padding-top:0">
          判定标准：顺线比例显著 &gt;50%（z&gt;2）说明模型信号包含市场尚未定价的信息，
          是"beat the close"的必要条件。<b>未通过 = 当前信号继续只做观察，不升级为下注。</b>
          实时盘口快照积累越多，本检验越可信。
        </p>`
      : `<p class="notice" style="border:none;margin:0">线移动检验还没有观察样本：需要实时盘口快照积累（watch / Actions 每日刷新）。
          SBR 收盘回填只有单时点，无法做线移动对照。仪器已就绪（<code>python -m app.cli linemove</code>）。</p>`;

    // 冻结 holdout（前 70% 训练 → 后 30% 一次性考试）
    let holdoutHtml = "";
    try {
      const ho = await api("/api/holdout");
      if (ho && ho.test && ho.test.n) {
        // API 模式没有 STATIC_PAYLOAD：直接取回测报告，避免对照行永远是 "—"
        let bt = window.STATIC_PAYLOAD?.backtest?.report?.overall || null;
        if (!bt) {
          try {
            const btr = await api("/api/backtest");
            bt = btr?.report?.overall || null;
          } catch (_) {
            bt = null;
          }
        }
        const t = ho.test, b = ho.baseline || {};
        holdoutHtml = `
          <div class="table-wrap" style="margin-top:12px"><table>
            <thead><tr><th>冻结 holdout（${ho.split ? Math.round(ho.split * 100) : 70}/${ho.split ? 100 - Math.round(ho.split * 100) : 30}）</th><th>分差 MAE</th><th>总分 MAE</th><th>Brier</th><th>ECE</th></tr></thead>
            <tbody>
              <tr><td>本模型（${ho.test_games} 场考试）</td><td>${t.margin_mae}</td><td>${t.total_mae}</td><td>${t.brier}</td><td>${t.ece}</td></tr>
              <tr><td>常数基线（训练期均值）</td><td>${b.constant_margin_mae ?? "—"}</td><td>${b.constant_total_mae ?? "—"}</td><td>${b.constant_brier ?? "—"}</td><td>—</td></tr>
              <tr><td>walk-forward（对照）</td><td>${bt?.margin_mae?.toFixed?.(2) ?? "—"}</td><td>${bt?.total_mae?.toFixed?.(2) ?? "—"}</td><td>${bt?.brier?.toFixed?.(4) ?? "—"}</td><td>—</td></tr>
            </tbody>
          </table></div>
          <p class="notice" style="border:none;margin:8px 0 0;padding-top:0">
            holdout 与 walk-forward 明显背离 = 超参对历史过拟合的信号。测试期 bias（分差 ${t.margin_bias >= 0 ? "+" : ""}${t.margin_bias} / 总分 ${t.total_bias >= 0 ? "+" : ""}${t.total_bias}）反映跨期 regime 偏移。
          </p>`;
      }
    } catch (_) { /* holdout 报告缺失时不显示 */ }

    const gateCount = (window.STATIC_PAYLOAD?.board?.games || [])
      .flatMap((g) => g.edges || [])
      .filter((e) => e.signal).length;
    const bettableCount = (window.STATIC_PAYLOAD?.board?.games || [])
      .flatMap((g) => g.edges || [])
      .filter((e) => e.signal && e.bettable).length;
    const bettableRow = window.STATIC_PAYLOAD
      ? `<p class="notice" style="border:none;margin:8px 0 0;padding-top:0">
          今日看板：信号 <b>${gateCount}</b> 条 · 通过可下注闸门（软书/共识深度/同场互斥）<b>${bettableCount}</b> 条。
          闸门标准：最优价非软书源、共识 ≥3 家、同场让分/胜负不叠加。
        </p>`
      : "";
    box.innerHTML = lm + holdoutHtml + bettableRow;
  } catch (e) {
    box.innerHTML = `<p class="notice" style="border:none;margin:0">闸门数据加载失败：${esc(e.message)}</p>`;
  }
}
window.loadGateCard = loadGateCard;

async function loadBaselineCard() {
  const box = document.getElementById("baseline-card");
  if (!box) return;
  try {
    const data = await api("/api/model-clv");
    const report = data.report;
    if (report && report.overall && report.overall.n) {
      const o = report.overall;
      const mb = report.market_baseline || {};
      let bt = (window.STATIC_PAYLOAD?.backtest?.report?.overall) || {};
      if (bt.margin_mae == null) {
        // 本地 API 模式没有 STATIC_PAYLOAD：直接取回测报告，避免这一格永远是 "—"
        try {
          const bd = await api("/api/backtest");
          bt = bd.report?.overall || {};
        } catch (_) { /* backtest 报告缺失时保持空 */ }
      }
      box.innerHTML = `
        <div class="table-wrap"><table>
          <thead><tr><th>基准</th><th>分差 MAE</th><th>总分 MAE</th></tr></thead>
          <tbody>
            <tr><td>收盘线（SBR/FanDuel）</td><td class="pos">${mb.closing_margin_mae ?? "—"}</td><td class="pos">${mb.closing_total_mae ?? "—"}</td></tr>
            <tr><td>本模型（walk-forward）</td><td>${bt.margin_mae != null ? Number(bt.margin_mae).toFixed(2) : "—"}</td><td>${bt.total_mae != null ? Number(bt.total_mae).toFixed(2) : "—"}</td></tr>
          </tbody>
        </table></div>
        <p class="notice" style="border:none;margin-top:8px;padding-top:0">
          分歧处样本 ${o.n} · 模型融合概率比收盘公允平均高 ${(o.mean_edge_vs_closing * 100).toFixed(1)}pp
          · 但收盘价下注 ROI ${(o.roi_at_closing * 100).toFixed(1)}%
          （对照：市场热门侧 ${(mb.favorite_bet_roi_at_closing * 100).toFixed(1)}% ≈ 纯抽水）
          <br>增量信息回归 β 的 CI 含 0 → 模型相对市场<b>无增量信息</b>；
          当前信号定位为「模型 vs 市场分歧观察」，不构成下注依据。
        </p>`;
    } else {
      box.textContent = "暂无收盘线检验报告（先运行 scripts/backfill_closing 与 model_clv_history）";
    }
  } catch (e) {
    box.textContent = "基准数据加载失败：" + e.message;
  }
}
window.loadBaselineCard = loadBaselineCard;

// 标签页切换（research.js/live.js 各自暴露 window.loadResearch / window.loadLive，
// 首次切入时才加载数据）
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (!button) return;
  const target = button.dataset.tab;
  document.querySelectorAll("#tabs .tab").forEach((tab) => tab.classList.toggle("active", tab === button));
  ["slate", "live", "bets", "research"].forEach((name) => {
    const panel = document.getElementById(`tab-${name}`);
    if (panel) panel.hidden = name !== target;
  });
  if (target === "research") { window.loadResearch?.(); window.loadBaselineCard?.(); window.loadGateCard?.(); }
  if (target === "live") window.loadLive?.();
  if (target === "bets") window.loadBets?.();
});
