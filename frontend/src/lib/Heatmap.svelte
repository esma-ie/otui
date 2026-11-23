<script lang="ts">
  // Differences from the base run as a grid: a row per group, a column per period, the cell
  // coloured by the difference (diverging: blue for more, red for less, grey for none), with the
  // value written in when it fits. Drawn with D3; title and key inside the SVG, for export.
  import * as d3 from "d3";
  import { compact, full } from "./results";

  let {
    title,
    subtitle = "",
    unit = null,
    periods,
    groups,
    value,
    onpick,
    svg = $bindable(),
  }: {
    title: string;
    subtitle?: string;
    unit?: string | null;
    periods: string[];
    groups: string[];
    value: (period: string, group: string) => number;
    onpick?: (group: string) => void;
    svg?: SVGSVGElement;
  } = $props();

  const W = 640;
  let tip = $state<{ x: number; y: number; lines: string[] } | null>(null);
  let box = $state<HTMLDivElement>();

  // redrawn when the mode or a company theme changes the diverging colours
  let themed = $state(0);
  $effect(() => {
    const bump = () => themed++;
    window.addEventListener("otui-theme", bump);
    return () => window.removeEventListener("otui-theme", bump);
  });
  $effect(() => {
    void themed;
    if (svg) draw(svg, periods, groups, value, title, subtitle, unit);
  });

  function draw(
    el: SVGSVGElement,
    periods: string[],
    groups: string[],
    value: (p: string, g: string) => number,
    title: string,
    subtitle: string,
    unit: string | null,
  ) {
    const root = d3.select(el);
    root.selectAll("*").remove();
    const left = Math.min(170, 16 + d3.max(groups, (g) => Math.min(g.length, 24) * 6.4)!);
    const m = { top: subtitle ? 56 : 42, right: 14, bottom: 46, left };
    const cell = Math.max(18, Math.min(30, 360 / Math.max(1, groups.length)));
    const H = m.top + groups.length * cell + m.bottom;
    root.attr("viewBox", `0 0 ${W} ${H}`);
    root.append("text").attr("class", "title").attr("x", 12).attr("y", 20).text(title);
    if (subtitle) root.append("text").attr("class", "sub").attr("x", 12).attr("y", 37).text(subtitle);

    const x = d3
      .scaleBand()
      .domain(periods)
      .range([m.left, W - m.right])
      .padding(0.06);
    const yb = d3
      .scaleBand()
      .domain(groups)
      .range([m.top, m.top + groups.length * cell])
      .padding(0.08);
    const cells = groups.flatMap((g) => periods.map((p) => ({ g, p, v: value(p, g) || 0 })));
    const max = d3.max(cells, (c) => Math.abs(c.v)) || 1;
    const css = getComputedStyle(el);
    const pole = (n: string, f: string) => css.getPropertyValue(n).trim() || f;
    const colour = d3
      .scaleDiverging<string>()
      .domain([-max, 0, max])
      .interpolator(d3.interpolateRgbBasis([pole("--diverge-neg", "#e34948"), pole("--diverge-mid", "#f0efec"), pole("--diverge-pos", "#2a78d6")]));

    root
      .append("g")
      .selectAll("text")
      .data(periods)
      .join("text")
      .attr("class", "tick")
      .attr("x", (p) => x(p)! + x.bandwidth() / 2)
      .attr("y", m.top - 6)
      .attr("text-anchor", "middle")
      .text((p) => p);
    root
      .append("g")
      .selectAll("text")
      .data(groups)
      .join("text")
      .attr("class", "tick")
      .attr("x", m.left - 6)
      .attr("y", (g) => yb(g)! + yb.bandwidth() / 2 + 4)
      .attr("text-anchor", "end")
      .text((g) => (g.length > 24 ? g.slice(0, 23) + "…" : g))
      .append("title")
      .text((g) => g);

    const c = root
      .append("g")
      .selectAll("g")
      .data(cells)
      .join("g")
      .attr("class", "cell")
      .on("mousemove", (ev: MouseEvent, d) => show(ev, [d.g, d.p, `${d.v > 0 ? "+" : ""}${full(d.v)}${unit ? ` ${unit}` : ""}`]))
      .on("mouseleave", () => (tip = null))
      .on("click", (_: MouseEvent, d) => onpick?.(d.g));
    c.append("rect")
      .attr("x", (d) => x(d.p)!)
      .attr("y", (d) => yb(d.g)!)
      .attr("width", x.bandwidth())
      .attr("height", yb.bandwidth())
      .attr("rx", 3)
      .attr("fill", (d) => colour(d.v));
    // a value inside its cell when it fits: white or near-black by the fill's lightness
    c.filter((d) => d.v !== 0 && x.bandwidth() >= compact(d.v).length * 6.2 + 8)
      .append("text")
      .attr("x", (d) => x(d.p)! + x.bandwidth() / 2)
      .attr("y", (d) => yb(d.g)! + yb.bandwidth() / 2 + 4)
      .attr("text-anchor", "middle")
      .attr("class", "val")
      .attr("fill", (d) => (d3.hsl(colour(d.v)).l > 0.6 ? "#0b0b0b" : "#ffffff"))
      .text((d) => `${d.v > 0 ? "+" : ""}${compact(d.v)}`);

    // the key: less, none, more
    const kx = m.left,
      ky = H - 28,
      kw = 180;
    const grad = root.append("defs").append("linearGradient").attr("id", "hm-key");
    for (const t of [0, 0.25, 0.5, 0.75, 1])
      grad
        .append("stop")
        .attr("offset", `${t * 100}%`)
        .attr("stop-color", colour(-max + 2 * max * t));
    root.append("rect").attr("x", kx).attr("y", ky).attr("width", kw).attr("height", 10).attr("rx", 3).attr("fill", "url(#hm-key)");
    root.append("text").attr("class", "tiny").attr("x", kx).attr("y", ky + 23).text(`−${compact(max)} less`);
    root.append("text").attr("class", "tiny").attr("x", kx + kw / 2).attr("y", ky + 23).attr("text-anchor", "middle").text("0");
    root
      .append("text")
      .attr("class", "tiny")
      .attr("x", kx + kw)
      .attr("y", ky + 23)
      .attr("text-anchor", "end")
      .text(`+${compact(max)} more${unit ? ` (${unit})` : ""}`);
  }

  function show(ev: MouseEvent, lines: string[]) {
    const r = box?.getBoundingClientRect();
    if (!r) return;
    tip = { x: ev.clientX - r.left, y: ev.clientY - r.top, lines };
  }
</script>

<div class="heat" bind:this={box}>
  <svg bind:this={svg} role="img" aria-label={title} class:pick={!!onpick}></svg>
  {#if tip}
    <div class="tip" style:left={`${tip.x + 14}px`} style:top={`${tip.y + 10}px`}>
      <strong>{tip.lines[0]}</strong>
      {#each tip.lines.slice(1) as l}<div>{l}</div>{/each}
    </div>
  {/if}
</div>

<style>
  .heat {
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
  }
  svg :global(.tiny) {
    font-size: 10px;
    fill: var(--muted);
  }
  svg :global(.val) {
    font-size: 10px;
    font-variant-numeric: tabular-nums;
  }
  svg.pick :global(.cell) {
    cursor: pointer;
  }
  svg :global(.cell:hover rect) {
    stroke: var(--ink);
    stroke-width: 1;
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
