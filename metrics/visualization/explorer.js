"use strict";
// Filters the answered runs of each explorer by the checked criteria and redraws its charts.
// Statistics follow pandas and numpy, so the numbers match the rest of the report: quantiles
// interpolate linearly, and the last histogram bin includes its right edge.

function quantile(sorted, q) {
  const position = (sorted.length - 1) * q;
  const low = Math.floor(position);
  const high = Math.min(low + 1, sorted.length - 1);
  return sorted[low] + (position - low) * (sorted[high] - sorted[low]);
}

function binOf(edges, value) {
  const last = edges.length - 2;
  const index = edges.findIndex((edge, i) => i > 0 && value < edge);
  return index === -1 ? last : Math.min(index - 1, last);
}

function summarize(data, choice) {
  const runs = data.runs;
  const every = runs.total_ms.map((_, i) => i);
  const picked = every.filter((i) =>
    Object.entries(choice).every(
      ([key, value]) => value === "all" || runs[key][i] === (value === "yes"),
    ),
  );
  const counts = (indexes) => {
    const bins = new Array(data.edges.length - 1).fill(0);
    indexes.forEach((i) => {
      bins[binOf(data.edges, runs.total_ms[i] / 1000)] += 1;
    });
    return bins;
  };
  const means = (indexes) =>
    data.kinds.map(
      (_, k) => indexes.reduce((sum, i) => sum + runs.spent_ms[i][k], 0) / indexes.length,
    );
  const sorted = picked.map((i) => runs.total_ms[i]).sort((a, b) => a - b);
  const found = picked.length > 0;
  return {
    n: picked.length,
    total: every.length,
    median: found ? quantile(sorted, 0.5) : null,
    p95: found ? quantile(sorted, 0.95) : null,
    counts: counts(picked),
    allCounts: counts(every),
    means: found ? means(picked) : null,
    allMeans: means(every),
  };
}

function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => String(values[key]));
}

// Same rules as stats.fmt_ms and stats.note.
function formats(lang) {
  const fixed = (digits) =>
    new Intl.NumberFormat(lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
      useGrouping: false,
    });
  const one = fixed(1);
  const none = fixed(0);
  return {
    ms: (ms) => (Math.round(ms) >= 1000 ? `${one.format(ms / 1000)} s` : `${none.format(ms)} ms`),
    seconds: (s) => one.format(s),
    // Tick labels carry only the decimals their step needs.
    tick: (value, step) => fixed(Math.max(0, -Math.floor(Math.log10(step)))).format(value),
  };
}

// Round axis steps (1, 2 or 5 times a power of ten) giving about `wanted` ticks.
function ticks(low, high, wanted) {
  const raw = (high - low) / wanted || 1;
  const power = 10 ** Math.floor(Math.log10(raw));
  const error = raw / power;
  const step = power * (error >= 7.07 ? 10 : error >= 3.16 ? 5 : error >= 1.41 ? 2 : 1);
  const first = Math.ceil(low / step - 1e-9) * step;
  const count = Math.floor((high - first) / step + 1e-9) + 1;
  return { step, values: Array.from({ length: count }, (_, i) => first + i * step) };
}

const SVG = "http://www.w3.org/2000/svg";

function add(parent, name, attributes, text) {
  const node = document.createElementNS(SVG, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text !== undefined) node.textContent = text;
  parent.appendChild(node);
  return node;
}

function drawHistogram(svg, data, summary, format) {
  const [width, height] = [720, 320];
  // The band above the plot holds the median and p95 labels, clear of the bars.
  const box = { left: 52, right: 16, top: 48, bottom: 48 };
  const edges = data.edges;
  const top = Math.max(...summary.allCounts, 1);
  const yTicks = ticks(0, Math.max(top, 5), 5).values.filter((v) => Number.isInteger(v));
  const yMax = Math.max(top, yTicks[yTicks.length - 1]);
  const x = (s) =>
    box.left + ((s - edges[0]) / (edges[edges.length - 1] - edges[0])) * (width - box.left - box.right);
  const y = (n) => height - box.bottom - (n / yMax) * (height - box.top - box.bottom);
  const { colors, text } = data;
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  yTicks.forEach((v) => {
    add(svg, "line", { x1: box.left, x2: width - box.right, y1: y(v), y2: y(v), stroke: colors.all });
    add(svg, "text", { x: box.left - 8, y: y(v) + 4, "text-anchor": "end" }, String(v));
  });
  const xTicks = ticks(edges[0], edges[edges.length - 1], 8);
  xTicks.values.forEach((s) => {
    const label = format.tick(s, xTicks.step);
    add(svg, "text", { x: x(s), y: height - box.bottom + 18, "text-anchor": "middle" }, label);
  });
  summary.allCounts.forEach((all, i) => {
    const [left, right] = [x(edges[i]) + 0.5, x(edges[i + 1]) - 0.5];
    const group = add(svg, "g", {});
    const range = `${format.seconds(edges[i])}–${format.seconds(edges[i + 1])} s`;
    add(group, "title", {}, fill(text.bin, { range, n: summary.counts[i], all }));
    // A full-height hit area, so thin bars still show their tooltip.
    add(group, "rect", { x: left, y: box.top, width: right - left, height: y(0) - box.top, fill: "transparent" });
    add(group, "rect", { x: left, y: y(all), width: right - left, height: y(0) - y(all), fill: colors.all });
    const n = summary.counts[i];
    add(group, "rect", { x: left, y: y(n), width: right - left, height: y(0) - y(n), fill: colors.selected });
  });
  add(svg, "line", { x1: box.left, x2: width - box.right, y1: y(0), y2: y(0), stroke: colors.muted });
  if (summary.n > 0) {
    [
      [summary.median, fill(text.median, { value: format.ms(summary.median) })],
      [summary.p95, fill(text.p95, { value: format.ms(summary.p95) })],
    ].forEach(([ms, label], i) => {
      const at = x(ms / 1000);
      add(svg, "line", { x1: at, x2: at, y1: 22 + 18 * i, y2: y(0), stroke: colors.ink, "stroke-dasharray": "5 4" });
      // Labels of close lines are staggered, and turned to the left near the right edge.
      const end = at > width - 160;
      add(
        svg,
        "text",
        { x: at + (end ? -6 : 6), y: 14 + 18 * i, "text-anchor": end ? "end" : "start", class: "mark" },
        label,
      );
    });
  }
  add(svg, "text", { x: (box.left + width - box.right) / 2, y: height - 8, "text-anchor": "middle" }, text.axis);
  add(svg, "text", { transform: `translate(14 ${(box.top + y(0)) / 2}) rotate(-90)`, "text-anchor": "middle" }, text.runs);
}

function drawSplit(svg, data, summary, format) {
  const width = 480;
  const box = { left: 92, right: 56, top: 20 };
  const rows = [
    [data.text.selected, summary.means],
    [data.text.every, summary.allMeans],
  ];
  const sums = rows.map(([, means]) => (means ? means.reduce((a, b) => a + b, 0) : 0));
  const xTicks = ticks(0, Math.max(...sums) / 1000, 4);
  const xMax = Math.max(Math.max(...sums) / 1000, xTicks.values[xTicks.values.length - 1]);
  const x = (s) => box.left + (s / xMax) * (width - box.left - box.right);
  const { colors, text, kinds } = data;
  const axis = box.top + 2 * 52;
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${axis + 60 + Math.ceil(kinds.length / 2) * 22}`);
  xTicks.values.forEach((s) => {
    add(svg, "line", { x1: x(s), x2: x(s), y1: box.top - 6, y2: axis, stroke: colors.all });
    add(svg, "text", { x: x(s), y: axis + 18, "text-anchor": "middle" }, format.tick(s, xTicks.step));
  });
  add(svg, "text", { x: (box.left + width - box.right) / 2, y: axis + 40, "text-anchor": "middle" }, text.split);
  rows.forEach(([label, means], row) => {
    const top = box.top + row * 52;
    add(svg, "text", { x: box.left - 8, y: top + 24, "text-anchor": "end" }, label);
    if (!means) return;
    const end = means.reduce((left, ms, k) => {
      const group = add(svg, "g", {});
      add(group, "title", {}, `${kinds[k].label}: ${format.ms(ms)}`);
      // A 2px surface gap separates neighbor segments.
      add(group, "rect", {
        x: x(left / 1000),
        y: top + 6,
        width: Math.max(x((left + ms) / 1000) - x(left / 1000) - 2, 0),
        height: 30,
        fill: kinds[k].color,
      });
      return left + ms;
    }, 0);
    add(svg, "text", { x: x(end / 1000) + 6, y: top + 26, class: "mark" }, format.ms(end));
  });
  kinds.forEach((kind, k) => {
    const [column, line] = [k % 2, Math.floor(k / 2)];
    const [left, top] = [box.left + column * 170, axis + 66 + line * 22];
    add(svg, "rect", { x: left, y: top - 10, width: 12, height: 12, rx: 2, fill: kind.color });
    add(svg, "text", { x: left + 18, y: top }, kind.label);
  });
}

function init(root) {
  const data = JSON.parse(root.querySelector(".explorer-data").textContent);
  const format = formats(data.lang);
  const [summaryLine, histogram, split] = [".explorer-summary", ".explorer-histogram", ".explorer-split"].map(
    (selector) => root.querySelector(selector),
  );
  const draw = () => {
    const choice = Object.fromEntries(
      [...root.querySelectorAll("fieldset")].map((set) => [set.dataset.key, set.querySelector("input:checked").value]),
    );
    const summary = summarize(data, choice);
    const { text } = data;
    const p95 = summary.n > 0 ? format.ms(summary.p95) : "";
    summaryLine.textContent =
      summary.n === 0
        ? text.none
        : [
            fill(text.count, { n: summary.n, total: summary.total }),
            fill(text.median, { value: format.ms(summary.median) }),
            fill(text.p95, { value: summary.n < 20 ? fill(text.indicative, { value: p95 }) : p95 }),
          ].join(" · ");
    drawHistogram(histogram, data, summary, format);
    drawSplit(split, data, summary, format);
  };
  root.addEventListener("change", draw);
  draw();
}

if (typeof document !== "undefined") document.querySelectorAll(".explorer").forEach(init);
if (typeof module !== "undefined") module.exports = { summarize };
