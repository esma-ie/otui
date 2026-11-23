<script lang="ts">
  // The RES as an engineering process flow diagram (SVG, D3): processes are horizontal boxes,
  // commodities are vertical lines, and a horizontal connector joins each of a process's inputs
  // (ports on the box's left) and outputs (ports on its right) to its commodity's line.
  //
  // Columns run left to right, as a RES reads: processes with no inputs and commodities nobody
  // produces start on the left, every other node one step right of the nearest of them
  // (breadth-first, so loops such as electricity into a refinery do not stretch the diagram), and
  // the demand commodities (COM_TMAP DEM), with the processes that make them, end on the right. A
  // d3 force simulation then places each column's boxes vertically: connected nodes pull
  // together, boxes keep apart, so lines cross as little as the layout allows.
  import * as d3 from "d3";
  import { fmt, type GNode, type GraphData } from "./api";

  let {
    data,
    onfocus,
    fewer,
  }: {
    data: GraphData;
    onfocus: (id: string) => void;
    fewer?: { label: string; run: () => void }; // draw a smaller neighbourhood, when too many
  } = $props();

  export const MAX_PROCESSES = 400; // more is unreadable as boxes, and slow as SVG
  const BOX_W = 104;
  const PORT = 8; // vertical space per port on a box
  const GAP = 26; // minimum horizontal gap between a process column and the next lines
  const LINE_STEP = 9; // horizontal space between commodity lines in one column
  const COM_TYPES = ["NRG", "DEM", "ENV", "MAT"];
  const TOKEN: Record<string, string> = { NRG: "com-nrg", DEM: "com-dem", ENV: "com-env", MAT: "com-mat" };

  interface P {
    node: GNode;
    layer: number;
    ins: C[];
    outs: C[];
    x: number;
    y: number;
    h: number;
  }
  interface C {
    node: GNode;
    layer: number;
    x: number;
    y: number;
    type: string;
  }
  interface Port {
    p: P;
    c: C;
    input: boolean;
    y: number;
  }

  let svg = $state<SVGSVGElement | undefined>();
  let host = $state<HTMLDivElement | undefined>();
  let width = $state(900);
  let transform = $state(d3.zoomIdentity);
  let zoom: d3.ZoomBehavior<SVGSVGElement, unknown>;
  let hover = $state<string>("");

  // emissions (COM_TMAP ENV) touch most processes: off by default, as on most RES diagrams
  let showEnv = $state(false);
  let layout = $derived(lay(data, showEnv));
  // as tall as the diagram at its fitted scale, within reason: drag or zoom for the rest
  let svgHeight = $derived(Math.round(Math.min(820, Math.max(360, layout.height * Math.max(0.3, Math.min(1.2, (width - 20) / Math.max(1, layout.w))) + 30))));

  function typeOf(n: GNode) {
    return COM_TYPES.find((t) => n.types.includes(t)) ?? "";
  }

  function lay(g: GraphData, env: boolean) {
    const procs = new Map<string, P>();
    const coms = new Map<string, C>();
    for (const n of g.nodes) {
      if (n.kind === "process") procs.set(n.id, { node: n, layer: -1, ins: [], outs: [], x: 0, y: 0, h: 0 });
      else if (env || typeOf(n) !== "ENV") coms.set(n.id, { node: n, layer: -1, x: 0, y: 0, type: typeOf(n) });
    }
    if (procs.size > MAX_PROCESSES) return { tooMany: procs.size, procs: [], coms: [], ports: [], height: 0, w: 0, unlinked: 0 };
    // ports: commodity -> process is an input, process -> commodity an output (commodity to
    // commodity links, emissions counted into a total, are left out of this view)
    for (const l of g.links) {
      const sp = procs.get(l.source), tp = procs.get(l.target);
      const sc = coms.get(l.source), tc = coms.get(l.target);
      if (sp && tc && !sp.outs.includes(tc)) sp.outs.push(tc);
      else if (sc && tp && !tp.ins.includes(sc)) tp.ins.push(sc);
    }
    const P = [...procs.values()].filter((p) => p.ins.length || p.outs.length);
    if (!P.length) return { tooMany: 0, procs: [], coms: [], ports: [], height: 0, w: 0, unlinked: procs.size };
    const used = new Set(P.flatMap((p) => [...p.ins, ...p.outs]));
    const C = [...coms.values()].filter((c) => used.has(c));
    const producers = new Map<C, P[]>(C.map((c) => [c, []]));
    const consumers = new Map<C, P[]>(C.map((c) => [c, []]));
    for (const p of P) {
      for (const c of p.outs) producers.get(c)!.push(p);
      for (const c of p.ins) consumers.get(c)!.push(p);
    }

    // columns: the longest path from a source, with the loops broken (an edge back to a node on
    // the current path, such as electricity into a refinery, is left out of the count), so each
    // stage of the chain (supply, conversion, transmission, end use) gets a column of its own.
    // Sources: commodities nobody produces (layer 0) and processes with no inputs or sector M
    // (layer 1); every edge alternates commodity and process, so commodities stay on even layers
    // and processes on odd ones.
    type X = P | C;
    const isP = (x: X): x is P => "ins" in x;
    const next = (x: X): X[] => (isP(x) ? x.outs : consumers.get(x)!);
    const all: X[] = [...C, ...P];
    const isSource = (x: X) => (isP(x) ? !x.ins.length || /^m/i.test(x.node.name) : !producers.get(x)!.length);
    const state = new Map<X, number>(); // 1 on the path, 2 done
    const order: X[] = [];
    const back = new Set<string>();
    const key = (a: X, b: X) => `${a.node.id}>${b.node.id}`;
    const roots = [...all.filter(isSource), ...all];
    for (const r of roots) {
      if (state.has(r)) continue;
      const stack: [X, number][] = [[r, 0]];
      state.set(r, 1);
      while (stack.length) {
        const top = stack[stack.length - 1];
        const kids = next(top[0]);
        if (top[1] < kids.length) {
          const k = kids[top[1]++];
          const s = state.get(k);
          if (s === 1) back.add(key(top[0], k));
          else if (s === undefined) {
            state.set(k, 1);
            stack.push([k, 0]);
          }
        } else {
          state.set(top[0], 2);
          order.push(top[0]);
          stack.pop();
        }
      }
    }
    order.reverse(); // a topological order of the graph without its back edges
    for (const x of all) x.layer = isSource(x) ? (isP(x) ? 1 : 0) : -1;
    for (const x of order) {
      if (x.layer < 0) x.layer = isP(x) ? 1 : 0;
      for (const k of next(x)) if (!back.has(key(x, k)) && !(isSource(k) && isP(k))) k.layer = Math.max(k.layer, x.layer + 1);
    }
    // the right-hand end: demand commodities in the last column, the processes making them next to it
    let last = Math.max(0, ...P.map((p) => p.layer + 1), ...C.map((c) => c.layer));
    if (last % 2) last++;
    for (const c of C) if (c.type === "DEM") c.layer = last;
    for (const p of P) if (p.outs.some((c) => c.type === "DEM")) p.layer = last - 1;
    // keep processes odd and commodities even after the moves above
    for (const p of P) if (p.layer % 2 === 0) p.layer += 1;
    for (const c of C) if (c.layer % 2) c.layer += 1;
    // drop empty columns
    const layers = [...new Set([...P.map((p) => p.layer), ...C.map((c) => c.layer)])].sort((a, b) => a - b);
    const index = new Map(layers.map((l, i) => [l, i]));

    // column x: process columns have a box's width, commodity columns room for their lines
    const colX: number[] = [];
    const lineCount = layers.map((l) => C.filter((c) => c.layer === l).length);
    let x = 20;
    layers.forEach((l, i) => {
      const w = l % 2 ? BOX_W : Math.max(GAP * 2, (lineCount[i] + 1) * LINE_STEP);
      colX[i] = x + w / 2;
      x += w + GAP;
    });
    const w = x;
    for (const p of P) {
      p.x = colX[index.get(p.layer)!];
      p.h = Math.max(18, PORT * Math.max(p.ins.length, p.outs.length) + 8);
    }
    for (const c of C) c.x = colX[index.get(c.layer)!];

    // vertical placement: a force simulation with x fixed by column
    type S = d3.SimulationNodeDatum & { ref: P | C };
    const sn: S[] = [...P, ...C].map((r, i) => ({ ref: r, x: r.x, fx: r.x, y: i * 3 }));
    const byRef = new Map(sn.map((s) => [s.ref, s]));
    const sl = P.flatMap((p) => [...p.ins, ...p.outs].map((c) => ({ source: byRef.get(p)!, target: byRef.get(c)! })));
    const sim = d3
      .forceSimulation<S>(sn)
      .force("link", d3.forceLink<S, d3.SimulationLinkDatum<S>>(sl).distance((l) => Math.abs((l.source as S).fx! - (l.target as S).fx!) + 1).strength(0.6))
      .force("y", d3.forceY<S>(0).strength(0.02))
      .force(
        "collide",
        d3.forceCollide<S>((s) => ("ins" in s.ref ? s.ref.h / 2 + 6 : 4)).strength(1).iterations(2),
      )
      .stop();
    sim.tick(Math.min(400, 60 + 4 * sn.length));
    for (const s of sn) s.ref.y = s.y ?? 0;

    // commodity lines side by side within their column, ordered by height to keep crossings few
    layers.forEach((l, i) => {
      const here = C.filter((c) => c.layer === l).sort((a, b) => a.y - b.y);
      const span = (here.length - 1) * LINE_STEP;
      here.forEach((c, k) => (c.x = colX[i] - span / 2 + k * LINE_STEP));
    });
    // ports, spread down each box's sides in the order of the lines they reach
    const ports: Port[] = [];
    for (const p of P) {
      for (const [list, input] of [
        [p.ins, true],
        [p.outs, false],
      ] as const) {
        const sorted = [...list].sort((a, b) => a.y - b.y);
        sorted.forEach((c, k) => ports.push({ p, c, input, y: p.y + (k - (sorted.length - 1) / 2) * PORT }));
      }
    }
    // shift everything to start at the top
    const top = Math.min(...P.map((p) => p.y - p.h / 2), ...ports.map((q) => q.y)) - 30;
    for (const p of P) p.y -= top;
    for (const q of ports) q.y -= top;
    const bottom = Math.max(...P.map((p) => p.y + p.h / 2), ...ports.map((q) => q.y)) + 20;
    return { tooMany: 0, procs: P, coms: C, ports, height: bottom, w, unlinked: procs.size - P.length };
  }

  // a commodity's line runs from its highest port to its lowest
  function extent(c: C, ports: Port[]) {
    const ys = ports.filter((q) => q.c === c).map((q) => q.y);
    return ys.length ? [Math.min(...ys), Math.max(...ys)] : [c.y, c.y];
  }

  function setup(el: SVGSVGElement) {
    zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 6])
      .on("zoom", (e) => (transform = e.transform));
    d3.select(el).call(zoom).on("dblclick.zoom", null);
    const ro = new ResizeObserver(() => (width = host?.clientWidth ?? width));
    if (host) ro.observe(host);
    return { destroy: () => ro.disconnect() };
  }

  export function reset() {
    if (!svg || !layout.w) return;
    // fit the width (the columns, left to right) and start at the top; drag to pan down
    const k = Math.max(0.3, Math.min(1.2, (width - 20) / layout.w));
    d3.select(svg).call(zoom.transform, d3.zoomIdentity.translate(Math.max(0, (width - k * layout.w) / 2), 10).scale(k));
  }
  // fit each new diagram into view
  $effect(() => {
    void layout;
    queueMicrotask(reset);
  });

  const lit = (id: string) => !hover || hover === id;
  const portLit = (q: Port) => !hover || hover === q.p.node.id || hover === q.c.node.id;
  const colour = (c: C) => (c.type ? `var(--${TOKEN[c.type]})` : "var(--link)");
</script>

<div class="flow" bind:this={host}>
  {#if layout.tooMany}
    <p class="note">
      <strong>{fmt(layout.tooMany, 0)} processes</strong> are too many to draw as boxes (the limit is {MAX_PROCESSES}).
      Search for a process or commodity, or lower the links slider, to draw its neighbourhood as a process flow.
    </p>
    {#if fewer}<p class="note"><button class="primary" onclick={fewer.run}>Draw {fewer.label}</button></p>{/if}
  {:else if !layout.procs.length}
    <p class="note">
      No process flows to draw here{layout.unlinked && !showEnv ? ": these processes are linked only by emissions." : "."}
      {#if !showEnv}<label><input type="checkbox" bind:checked={showEnv} /> Show emissions</label>{/if}
    </p>
  {:else}
    <svg bind:this={svg} use:setup role="img" aria-label="Process flow diagram of the reference energy system" height={svgHeight}>
      <g transform={transform.toString()}>
        <!-- commodity lines -->
        {#each layout.coms as c (c.node.id)}
          {@const [y0, y1] = extent(c, layout.ports)}
          <g class="com" class:dim={!lit(c.node.id) && !layout.ports.some((q) => q.c === c && portLit(q))}>
            <line x1={c.x} x2={c.x} y1={y0 - 10} y2={y1 + 10} stroke={colour(c)} stroke-width="2.2" />
            <text
              class="clabel"
              x={c.x}
              y={y0 - 14}
              transform="rotate(-50 {c.x} {y0 - 14})"
              role="button"
              tabindex="-1"
              onmouseenter={() => (hover = c.node.id)}
              onmouseleave={() => (hover = "")}
              onclick={() => onfocus(c.node.id)}
              onkeydown={() => {}}
            >
              {c.node.name}<title>{c.node.name}{c.node.description ? `: ${c.node.description}` : ""}{c.type ? ` (${c.type})` : ""}. Click to centre on it.</title>
            </text>
          </g>
        {/each}
        <!-- connectors, with a dot where they meet the line -->
        {#each layout.ports as q, i (i)}
          {@const edge = q.input ? q.p.x - BOX_W / 2 : q.p.x + BOX_W / 2}
          <g class:dim={!portLit(q)}>
            <line x1={q.c.x} x2={edge} y1={q.y} y2={q.y} stroke={colour(q.c)} stroke-width="1.3" />
            <circle cx={q.c.x} cy={q.y} r="2.4" fill={colour(q.c)} />
            {#if q.input}
              <path d="M{edge - 5},{q.y - 3}L{edge},{q.y}L{edge - 5},{q.y + 3}" fill="none" stroke={colour(q.c)} stroke-width="1.3" />
            {/if}
          </g>
        {/each}
        <!-- process boxes -->
        {#each layout.procs as p (p.node.id)}
          <g
            class="proc"
            class:dim={!lit(p.node.id) && !(hover && [...p.ins, ...p.outs].some((c) => c.node.id === hover))}
            role="button"
            tabindex="-1"
            onmouseenter={() => (hover = p.node.id)}
            onmouseleave={() => (hover = "")}
            onclick={() => onfocus(p.node.id)}
            onkeydown={() => {}}
          >
            <rect x={p.x - BOX_W / 2} y={p.y - p.h / 2} width={BOX_W} height={p.h} rx="3" />
            <text x={p.x} y={p.y + 3.5}>{p.node.name.length > 15 ? `${p.node.name.slice(0, 14)}…` : p.node.name}</text>
            <title>{p.node.name}{p.node.description ? `: ${p.node.description}` : ""}
In: {p.ins.map((c) => c.node.name).join(", ") || "none"}
Out: {p.outs.map((c) => c.node.name).join(", ") || "none"}
Click to centre on it.</title>
          </g>
        {/each}
      </g>
    </svg>
    <div class="legend muted">
      <label><input type="checkbox" bind:checked={showEnv} /> Show emissions</label>
      <span><i class="box"></i>process</span>
      {#each COM_TYPES as t (t)}<span><i class="ln" style="border-color: var(--{TOKEN[t]})"></i>{{ NRG: "energy", DEM: "demand", ENV: "emission", MAT: "material" }[t]}</span>{/each}
      <span>› input · ● joins a commodity's line</span>
      <span>Left to right: supply to demand. Hover to trace, click to centre on a node, scroll to zoom.</span>
      {#if layout.unlinked}
        <span>
          {fmt(layout.unlinked, 0)} process{layout.unlinked === 1 ? "" : "es"} not drawn: linked here only by
          {showEnv ? "links to other commodities" : "emissions (Show emissions draws them)"}.
        </span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .flow {
    position: relative;
  }
  svg {
    width: 100%;
    display: block;
    cursor: grab;
    background: var(--panel);
    border-radius: 6px;
  }
  .proc rect {
    fill: var(--bg);
    stroke: var(--process);
    stroke-width: 1.4;
  }
  .proc:hover rect {
    stroke: var(--accent);
    stroke-width: 2;
  }
  .proc text {
    font: 10px var(--mono, monospace);
    fill: var(--ink, currentColor);
    text-anchor: middle;
    pointer-events: none;
  }
  .proc {
    cursor: pointer;
  }
  .clabel {
    font: 9px var(--mono, monospace);
    fill: var(--muted);
    cursor: pointer;
  }
  .clabel:hover {
    fill: var(--accent);
  }
  .dim {
    opacity: 0.15;
  }
  .note {
    padding: 1rem;
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem 1rem;
    font-size: 0.8rem;
    padding: 0.4rem 0.2rem 0;
  }
  .legend i.box {
    display: inline-block;
    width: 16px;
    height: 9px;
    border: 1.4px solid var(--process);
    border-radius: 2px;
    margin-right: 0.3rem;
    vertical-align: middle;
  }
  .legend i.ln {
    display: inline-block;
    width: 0;
    height: 12px;
    border-left: 2.2px solid;
    margin: 0 0.35rem 0 0.2rem;
    vertical-align: middle;
  }
</style>
