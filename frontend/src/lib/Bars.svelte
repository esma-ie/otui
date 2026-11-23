<script lang="ts">
  // Stacked columns over periods, drawn with D3: one column per period, or one per period and
  // run, side by side; negative values stack below zero (a difference between runs).
  // Title, unit and legend are inside the SVG, so an exported chart (SVG, PNG, JPEG) is whole.
  // Hover a segment for its value; click it, or a legend entry, to pick that group (onpick).
  import * as d3 from "d3";
  import { compact, full, OTHER } from "./results";

  interface Sub {
    id: string;
    label: string;
  }
  let {
    title,
    subtitle = "",
    unit = null,
    periods,
    subs,
    groups,
    value,
    colour,
    onpick,
    svg = $bindable(),
    height = 300,
    totals = true,
  }: {
    title: string;
    subtitle?: string;
    unit?: string | null;
    periods: string[];
    subs: Sub[];
    groups: string[];
    value: (sub: string, period: string, group: string) => number; // a run's value
    colour: Map<string, string>;
    onpick?: (group: string) => void;
    svg?: SVGSVGElement;
    height?: number;
    totals?: boolean; // column totals on the caps (not for differences: they cancel)
  } = $props();

  const W = 640;
  let tip = $state<{ x: number; y: number; lines: string[] } | null>(null);
  let box = $state<HTMLDivElement>();

  $effect(() => {
    if (svg) draw(svg, periods, subs, groups, value, colour, title, subtitle, unit, totals);
  });

  function draw(
    el: SVGSVGElement,
    periods: string[],
    subs: Sub[],
    groups: string[],
    value: (s: string, p: string, g: string) => number,
    colour: Map<string, string>,
    title: string,
    subtitle: string,
    unit: string | null,
    totals: boolean,
  ) {
    const root = d3.select(el);
    root.selectAll("*").remove();
    // the legend first: its rows set the chart's height
    const items = groups.map((g) => ({ g, w: 22 + Math.min(g.length, 28) * 6.6 }));
    const rows: { g: string; x: number; row: number }[] = [];
    let x = 0,
      row = 0;
    for (const it of items) {
      if (x + it.w > W - 24 && x > 0) {
        x = 0;
        row++;
      }
      rows.push({ g: it.g, x, row });
      x += it.w + 10;
    }
    const legendH = groups.length > 1 ? (row + 1) * 18 + 8 : 0;
    const sideBySide = subs.length > 1;
    const m = { top: subtitle ? 50 : 36, right: 14, bottom: sideBySide ? 44 : 26, left: 58 };
    const H = height + legendH;
    const plotH = height - m.top - m.bottom;
    root.attr("viewBox", `0 0 ${W} ${H}`);

    root.append("text").attr("class", "title").attr("x", 12).attr("y", 20).text(title);
    if (subtitle) root.append("text").attr("class", "sub").attr("x", 12).attr("y", 37).text(subtitle);

    // one record per column: period, run, a value per group
    type Col = { period: string; sub: string; [g: string]: number | string };
    const cols: Col[] = [];
    for (const p of periods)
      for (const s of subs) {
        const c: Col = { period: p, sub: s.id };
        for (const g of groups) c[g] = value(s.id, p, g) || 0;
        cols.push(c);
      }
    const stacked = d3
      .stack<Col>()
      .keys(groups)
      .value((d, k) => d[k] as number)
      .offset(d3.stackOffsetDiverging)(cols);
    const lo = d3.min(stacked, (s) => d3.min(s, (d) => d[0])) ?? 0;
    const hi = d3.max(stacked, (s) => d3.max(s, (d) => d[1])) ?? 0;
    const y = d3
      .scaleLinear()
      .domain([Math.min(0, lo), Math.max(0, hi) || 1])
      .nice()
      .range([m.top + plotH, m.top]);
    const x0 = d3
      .scaleBand()
      .domain(periods)
      .range([m.left, W - m.right])
      .paddingInner(sideBySide ? 0.18 : 0.3)
      .paddingOuter(0.1);
    const x1 = d3
      .scaleBand()
      .domain(subs.map((s) => s.id))
      .range([0, x0.bandwidth()])
      .padding(0.12);
    const bw = Math.min(x1.bandwidth(), sideBySide ? 34 : 40);
    const colX = (d: Col) => x0(d.period)! + x1(d.sub)! + (x1.bandwidth() - bw) / 2;

    // grid and axes: hairlines, recessive
    const ticks = y.ticks(5);
    root
      .append("g")
      .selectAll("line")
      .data(ticks)
      .join("line")
      .attr("class", (t) => (t === 0 ? "zero" : "grid"))
      .attr("x1", m.left)
      .attr("x2", W - m.right)
      .attr("y1", (t) => y(t))
      .attr("y2", (t) => y(t));
    root
      .append("g")
      .selectAll("text")
      .data(ticks)
      .join("text")
      .attr("class", "tick")
      .attr("x", m.left - 6)
      .attr("y", (t) => y(t) + 4)
      .attr("text-anchor", "end")
      .text((t) => compact(t));
    if (unit) root.append("text").attr("class", "tick").attr("x", m.left - 6).attr("y", m.top - 8).attr("text-anchor", "end").text(unit);
    root
      .append("g")
      .selectAll("text")
      .data(periods)
      .join("text")
      .attr("class", "tick")
      .attr("x", (p) => x0(p)! + x0.bandwidth() / 2)
      .attr("y", m.top + plotH + (sideBySide ? 34 : 17))
      .attr("text-anchor", "middle")
      .text((p) => p);
    if (sideBySide) {
      // each run's letter under its column; the key is in the subtitle
      root
        .append("g")
        .selectAll("text")
        .data(cols)
        .join("text")
        .attr("class", "tiny")
        .attr("x", (d) => colX(d) + bw / 2)
        .attr("y", m.top + plotH + 15)
        .attr("text-anchor", "middle")
        .text((d) => String.fromCharCode(65 + subs.findIndex((s) => s.id === d.sub)));
    }

    // the segments, a surface gap between them
    const tipFor = (g: string, d: Col) => {
      const v = d[g] as number;
      const total = groups.reduce((a, k) => a + ((d[k] as number) || 0), 0);
      const s = subs.find((t) => t.id === d.sub);
      return [
        `${g}${g === OTHER ? " (smaller groups)" : ""}`,
        `${d.period}${sideBySide && s ? ` · ${s.label}` : ""}`,
        `${full(v)}${unit ? ` ${unit}` : ""}`,
        ...(groups.length > 1 ? [`column total ${full(total)}`] : []),
      ];
    };
    const layer = root.append("g");
    for (const s of stacked) {
      const g = s.key;
      layer
        .append("g")
        .attr("fill", colour.get(g) ?? "var(--series-other)")
        .selectAll("rect")
        .data(s.filter((d) => Math.abs(d[1] - d[0]) > 0))
        .join("rect")
        .attr("class", "seg")
        .attr("x", (d) => colX(d.data))
        .attr("width", bw)
        .attr("y", (d) => y(Math.max(d[0], d[1])))
        .attr("height", (d) => Math.max(0.5, Math.abs(y(d[0]) - y(d[1]))))
        .on("mousemove", (ev: MouseEvent, d) => show(ev, tipFor(g, d.data)))
        .on("mouseleave", () => (tip = null))
        .on("click", () => onpick?.(g));
    }

    // column totals on the caps, when the columns are few
    if (totals && !sideBySide && periods.length <= 14) {
      root
        .append("g")
        .selectAll("text")
        .data(cols)
        .join("text")
        .attr("class", "tiny")
        .attr("x", (d) => colX(d) + bw / 2)
        .attr("y", (d) => {
          const t = groups.reduce((a, k) => a + Math.max(0, (d[k] as number) || 0), 0);
          return y(t) - 4;
        })
        .attr("text-anchor", "middle")
        .text((d) => {
          const t = groups.reduce((a, k) => a + ((d[k] as number) || 0), 0);
          return t ? compact(t) : "";
        });
    }

    // the legend: a swatch beside each group's name; click to pick it
    if (groups.length > 1) {
      const lg = root.append("g").attr("transform", `translate(12, ${height + 4})`);
      const it = lg
        .selectAll("g")
        .data(rows)
        .join("g")
        .attr("class", "key")
        .attr("transform", (r) => `translate(${r.x}, ${r.row * 18})`)
        .on("click", (_: MouseEvent, r) => onpick?.(r.g));
      it.append("rect")
        .attr("width", 12)
        .attr("height", 12)
        .attr("rx", 3)
        .attr("fill", (r) => colour.get(r.g) ?? "var(--series-other)");
      it.append("text")
        .attr("class", "tick")
        .attr("x", 17)
        .attr("y", 10)
        .text((r) => (r.g.length > 28 ? r.g.slice(0, 27) + "…" : r.g))
        .append("title")
        .text((r) => r.g);
    }
  }

  function show(ev: MouseEvent, lines: string[]) {
    const r = box?.getBoundingClientRect();
    if (!r) return;
    tip = { x: ev.clientX - r.left, y: ev.clientY - r.top, lines };
  }
</script>

<div class="bars" bind:this={box}>
  <svg bind:this={svg} role="img" aria-label={title} class:pick={!!onpick}></svg>
  {#if tip}
    <div class="tip" style:left={`${tip.x + 14}px`} style:top={`${tip.y + 10}px`}>
      <strong>{tip.lines[0]}</strong>
      {#each tip.lines.slice(1) as l}<div>{l}</div>{/each}
    </div>
  {/if}
</div>

<style>
  .bars {
    position: relative;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
    font-family: var(--sans);
  }
  svg :global(.title) {
    font-size: 14px;
    font-weight: 600;
    fill: var(--heading);
  }
  svg :global(.sub) {
    font-size: 11.5px;
    fill: var(--muted);
  }
  svg :global(.tick) {
    font-size: 11px;
    fill: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  svg :global(.tiny) {
    font-size: 10px;
    fill: var(--muted);
  }
  svg :global(.grid) {
    stroke: var(--line);
    stroke-width: 1;
  }
  svg :global(.zero) {
    stroke: var(--muted);
    stroke-width: 1;
  }
  svg :global(.seg) {
    stroke: var(--panel);
    stroke-width: 1;
  }
  svg.pick :global(.seg),
  svg.pick :global(.key) {
    cursor: pointer;
  }
  svg :global(.seg:hover) {
    opacity: 0.8;
  }
  .tip {
    position: absolute;
    pointer-events: none;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.35rem 0.55rem;
    font-size: 12px;
    box-shadow: 0 2px 10px rgb(0 0 0 / 0.15);
    z-index: 5;
    white-space: nowrap;
  }
</style>
