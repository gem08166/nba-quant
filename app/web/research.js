// 研究图表页：原生 canvas，零依赖。
// 数据源：/api/backtest（月度指标+逐场行）、/api/rapm、/api/trends。
// 风格与看板一致（记分台冷调石墨）。仅在用户切到"研究图表"时加载。

// 图表颜色运行时读取主题 CSS 变量——主题切换后重绘自动跟随。
function chartColors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => s.getPropertyValue(name).trim() || fallback;
  return {
    text: v("--text", "#dfe3ea"),
    muted: v("--muted", "#8a94a3"),
    grid: v("--line-soft", "#222730"),
    green: v("--green", "#46b95c"),
    red: v("--red", "#e0574f"),
    blue: v("--blue", "#4b8fd6"),
    amber: v("--amber", "#e8a33d"),
  };
}
const COLORS = chartColors();
window.__refreshChartColors = () => Object.assign(COLORS, chartColors());

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
  ctx.font = '11px "Plex Mono", ui-monospace, monospace';
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
      ctx.strokeStyle = COLORS.grid;
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
      ctx.strokeStyle = COLORS.grid;
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

// 多序列累计曲线（真实 vs 模拟）：日期取并集，缺失日沿用上一个累计值
// 多序列累计曲线（真实 vs 模拟）：用 SVG 渲染，不用 canvas。
// 有的手机浏览器/WebView 会禁用或替换 canvas（表现为整块白屏 + 破图像占位），
// SVG 在所有手机浏览器都能画，而且高 DPI 下更清晰。
function drawMultiLineChart(host, seriesList, { unit = "" } = {}) {
  if (!host) return;
  const list = (seriesList || []).filter((s) => s && s.points && s.points.length);
  const width = Math.max(280, Math.round(host.clientWidth || 640));
  const height = Math.round(host.clientHeight || Number(host.dataset.height) || 240);
  if (!list.length) {
    host.innerHTML = '<p class="chart-empty">暂无已结算注单：结算后这里会出现累计收益曲线</p>';
    return;
  }

  const dates = Array.from(new Set(list.flatMap((s) => s.points.map((p) => p.date)))).sort();
  const aligned = list.map((s) => {
    const map = new Map(s.points.map((p) => [p.date, p.value]));
    let last = 0;
    let started = false;
    return {
      label: s.label,
      color: s.color || COLORS.green,
      values: dates.map((d) => {
        if (map.has(d)) {
          last = map.get(d);
          started = true;
        }
        return started ? last : 0;
      }),
    };
  });

  const all = aligned.flatMap((a) => a.values);
  const lo = Math.min(0, ...all);
  const hi = Math.max(0, ...all);
  const padV = (hi - lo) * 0.12 || 1;
  const yMin = lo - padV;
  const yMax = hi + padV;
  const pad = { l: 54, r: 14, t: 26, b: 34 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin)) * plotH;
  const decimals = hi - lo < 10 ? 2 : 1;
  const esc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const parts = [
    `<svg viewBox="0 0 ${width} ${height}" width="100%" height="100%" role="img" aria-label="累计收益曲线">`,
  ];

  // Y 轴刻度 + 网格
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    const yy = y(v).toFixed(1);
    if (i > 0) {
      parts.push(
        `<line x1="${pad.l}" y1="${yy}" x2="${width - pad.r}" y2="${yy}" stroke="${COLORS.grid}" stroke-width="1"/>`
      );
    }
    parts.push(
      `<text x="${pad.l - 6}" y="${(y(v) + 4).toFixed(1)}" fill="${COLORS.muted}" font-size="11" font-family="ui-monospace, monospace" text-anchor="end">${v.toFixed(decimals)}${unit}</text>`
    );
  }

  // 0 轴虚线
  if (yMin < 0 && yMax > 0) {
    parts.push(
      `<line x1="${pad.l}" y1="${y(0).toFixed(1)}" x2="${width - pad.r}" y2="${y(0).toFixed(1)}" stroke="rgba(232,163,61,0.45)" stroke-width="1" stroke-dasharray="4 4"/>`
    );
  }

  // 曲线 + 点
  const n = dates.length;
  const slot = plotW / Math.max(n - 1, 1);
  aligned.forEach((s) => {
    const pts = s.values
      .map((v, i) => `${(pad.l + slot * i).toFixed(1)},${y(v).toFixed(1)}`)
      .join(" ");
    parts.push(
      `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`
    );
    s.values.forEach((v, i) => {
      if (n > 24 && i % 2 === 1) return;
      parts.push(
        `<circle cx="${(pad.l + slot * i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.5" fill="${s.color}"/>`
      );
    });
  });

  // 图例
  let lx = pad.l;
  aligned.forEach((s) => {
    const text = esc(s.label);
    parts.push(`<rect x="${lx}" y="${pad.t - 15}" width="10" height="3" fill="${s.color}"/>`);
    parts.push(
      `<text x="${lx + 14}" y="${pad.t - 9}" fill="${COLORS.muted}" font-size="11" font-family="sans-serif">${text}</text>`
    );
    lx += 14 + text.length * 11 + 14;
  });

  // X 轴日期
  dates.forEach((d, i) => {
    if (dates.length > 9 && i % 2 === 1) return;
    parts.push(
      `<text x="${(pad.l + slot * i).toFixed(1)}" y="${height - pad.b + 16}" fill="${COLORS.muted}" font-size="11" font-family="ui-monospace, monospace" text-anchor="middle">${esc(String(d).slice(2))}</text>`
    );
  });

  parts.push("</svg>");
  host.innerHTML = parts.join("");
}
window.drawMultiLineChart = drawMultiLineChart;

function drawRapmChart(canvas, players) {  const { ctx, width, height } = setupCanvas(canvas);
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
    ctx.font = '11px "Plex Mono", ui-monospace, monospace';
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
    ctx.font = '10px "Plex Mono", ui-monospace, monospace';
    ctx.textAlign = "center";
    ctx.fillText(`n=${b.n}`, x(b.mid), y(b.winRate) - 10);
    ctx.font = '11px "Plex Mono", ui-monospace, monospace';
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

// 赛中胜率模型验收（研究模块）：只报告校准质量，不产生任何下注建议
async function loadLiveWpCard() {
  const box = document.getElementById("livewp-card");
  if (!box) return;
  try {
    const data = await api("/api/live-wp");
    const rep = data.report;
    if (!rep) {
      box.innerHTML = `<h2>赛中胜率模型 · 校准验收</h2>
        <p style="color:var(--muted);font-size:12.5px;margin:4px 0 0">
        暂无标定报告（运行 <code>python -m scripts.live_wp_fit</code> 生成）。</p>`;
      return;
    }
    const p = data.params || rep.params || {};
    const hold = rep.holdout;
    const m = (hold && hold.metrics) || rep.in_sample || {};
    const cal = (hold && hold.calibration) || rep.calibration || [];
    const rows = cal
      .map(
        (r) =>
          `<tr><td>${r.bin}</td><td>${r.n}</td><td>${(r.pred * 100).toFixed(1)}%</td>` +
          `<td>${(r.actual * 100).toFixed(1)}%</td>` +
          `<td class="${Math.abs(r.gap) <= 0.03 ? "pos" : ""}">${(r.gap * 100 >= 0 ? "+" : "")}${(r.gap * 100).toFixed(1)}pp</td></tr>`
      )
      .join("");
    const maxGap = cal.length ? Math.max(...cal.map((r) => Math.abs(r.gap))) : null;
    box.innerHTML = `
      <h2>赛中胜率模型 · 校准验收</h2>
      <div class="table-wrap" style="margin-top:6px"><table>
        <thead><tr><th>指标</th><th>样本外（${hold ? hold.split_date + " 之后" : "—"}）</th><th>样本内</th></tr></thead>
        <tbody>
          <tr><td>LogLoss</td><td class="pos">${m.logloss ?? "—"}</td><td>${rep.in_sample?.logloss ?? "—"}</td></tr>
          <tr><td>Brier</td><td class="pos">${m.brier ?? "—"}</td><td>${rep.in_sample?.brier ?? "—"}</td></tr>
          <tr><td>基线 LogLoss（恒定 50%）</td><td colspan="2">${m["baseline_logloss_0.5"] ?? "—"}</td></tr>
        </tbody>
      </table></div>
      <div class="table-wrap" style="margin-top:8px"><table>
        <thead><tr><th>预测区间</th><th>样本</th><th>平均预测</th><th>实际胜率</th><th>偏差</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <p class="notice" style="border:none;margin-top:8px;padding-top:0">
        模型：<code>μ = 分差 + 赛前预期·剩余时间占比</code>，
        <code>σ = √(σ_full²·(t/T)^2γ + σ_floor²)</code>，<code>P = Φ(μ/σ)</code>。
        参数 σ_full=${p.sigma_full} · γ=${p.gamma} · σ_floor=${p.sigma_floor} · 赛前权重=${p.prior_weight}。
        样本 ${rep.sample?.plays ?? "—"} 个回合 / ${rep.sample?.games ?? "—"} 场。
        ${maxGap != null ? `<br>样本外最大分箱偏差 <b>${(maxGap * 100).toFixed(1)}pp</b>。` : ""}
        ${rep.caveat ? `<br>${rep.caveat}` : ""}
        <br><b>此模块只做记录与校准研究，不产生滚球信号、不构成下注建议。</b>
      </p>`;
  } catch (e) {
    box.textContent = "赛中胜率报告加载失败：" + e.message;
  }
}
window.loadLiveWpCard = loadLiveWpCard;

// 标签页切换（research.js/live.js 各自暴露 window.loadResearch / window.loadLive，
// 首次切入时才加载数据）
document.getElementById("tabs").addEventListener("click", (event) => {
  const button = event.target.closest("[data-tab]");
  if (!button) return;
  const target = button.dataset.tab;
  document.querySelectorAll("#tabs .tab").forEach((tab) => {
    const on = tab === button;
    tab.classList.toggle("active", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  });
  ["slate", "live", "post", "bets", "research"].forEach((name) => {
    const panel = document.getElementById(`tab-${name}`);
    if (panel) panel.hidden = name !== target;
  });
  if (target === "research") { window.loadResearch?.(); window.loadBaselineCard?.(); window.loadGateCard?.(); window.loadLiveWpCard?.(); }
  if (target === "live") window.loadLive?.();
  if (target === "post") window.loadPost?.();
  if (target === "bets") window.loadBets?.();
});
