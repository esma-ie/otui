<script lang="ts">
  // The RES network on a canvas (fast for large models), as a gravity-weighted force-directed
  // graph: every node is pulled to the centre in proportion to its weight (its number of links),
  // and pushes the others away in proportion to it too, so the hubs (ELC, a refinery) settle in
  // the middle and the end uses and dead ends on the rim. Processes are the nodes, with a shape
  // per sector; commodities are the flows between them: curved coloured lines from each producing
  // process to each consuming one, which meet at an undrawn junction per commodity (one line per
  // producer and consumer, not per pair, so a commodity like ELC with hundreds of users stays
  // drawable). Drag a process and the layout re-sorts around it, briefly, then freezes.
  import * as d3 from "d3";
  import { onDestroy } from "svelte";
  import { api, ApiError, fmt, type GLink, type GNode, type GraphData, type Hit } from "./api";
  import DummyNote from "./DummyNote.svelte";
  import ProcessFlow from "./ProcessFlow.svelte";

  let {
    modelId,
    focus = $bindable(""),
    regions = [],
    solveKey = "",
  }: { modelId: string; focus?: string; regions?: string[]; solveKey?: string } = $props();

  type N = GNode & d3.SimulationNodeDatum & { degree: number; out: number };
  type L = Omit<GLink, "source" | "target"> & d3.SimulationLinkDatum<N> & { com: N };

  const FLAG_LABEL: Record<string, string> = {
    no_supply: "used, never produced",
    no_use: "produced, never used",
    unused: "declared, never linked",
    disconnected: "process with no links",
  };
  const RED = new Set(["no_supply", "no_use"]);

  // Sector = the first letter of the process code. A common convention (A, C, E, I, R, T), but
  // not a rule: in demo12, IMP... (imports) and EXP... (exports) share letters with industry
  // and electricity. The letter is shown as is.
  const SECTOR_HINT: Record<string, string> = {
    A: "agriculture",
    C: "commercial",
    E: "electricity",
    I: "industry",
    R: "residential",
    T: "transport",
  };
  const SHAPES = [d3.symbolCircle, d3.symbolSquare, d3.symbolTriangle, d3.symbolDiamond, d3.symbolStar, d3.symbolWye, d3.symbolCross];
  const COM_TYPE: [string, string, string][] = [
    ["NRG", "com-nrg", "energy"],
    ["DEM", "com-dem", "demand"],
    ["ENV", "com-env", "emission"],
    ["MAT", "com-mat", "material"],
  ];

  type ProcessColour = "sector" | "type" | "checks";
  type LineColour = "type" | "commodity";
  let processColour = $state<ProcessColour>("sector");
  let lineColour = $state<LineColour>("type");

  let canvas: HTMLCanvasElement;
  let wrap: HTMLDivElement;
  let legend: HTMLDivElement | undefined;
  let data = $state<GraphData | null>(null);
  let depth = $state<number | undefined>(undefined);
  let slide = $state(1); // the slider's position, in links from the anchor
  let region = $state("");
  let query = $state("");
  let hits = $state<Hit[]>([]);
  let hover = $state<N | null>(null);
  let tip = $state({ x: 0, y: 0, right: 0, left: false }); // where the hover tooltip sits, in the stage
  let selected = $state<N | null>(null);
  let error = $state("");
  let tooLarge = $state("");
  let flagOpen = $state("");
  let sectors = $state<{ key: string; count: number; shape: number; colour: number }[]>([]);
  let groups = $state<{ key: string; count: number; colour: number }[]>([]);
  let pinned = $state(0);
  // two drawings of the same nodes and links: the force-directed network, or a process flow
  // diagram (boxes and commodity lines, left to right); the choice is kept per browser
  type View = "network" | "flow";
  let view = $state<View>(readView());
  let flow = $state<ProcessFlow | undefined>(undefined);
  function readView(): View {
    try {
      return localStorage.getItem("otui.resView") === "flow" ? "flow" : "network";
    } catch {
      return "network";
    }
  }
  function setView(v: View) {
    view = v;
    try {
      localStorage.setItem("otui.resView", v);
    } catch {
      /* private window: the choice lasts this page only */
    }
    if (v === "network") queueMicrotask(() => ((autoFit = true), fit(), draw()));
  }

  let nodes: N[] = [];
  let links: L[] = [];
  let sim: d3.Simulation<N, L> | null = null;
  let transform = d3.zoomIdentity;
  let width = 800;
  let height = 560;
  let colors: Record<string, string> = {};
  let cats: string[] = [];
  let sectorOf = new Map<string, { shape: number; colour: number }>();
  let groupOf = new Map<string, number>();

  function readColors() {
    const s = getComputedStyle(document.documentElement);
    for (const k of ["process", "com-nrg", "com-dem", "com-env", "com-mat", "node-line", "link", "error", "warn", "accent", "ink", "muted", "panel"])
      colors[k] = s.getPropertyValue(`--${k}`).trim();
    cats = Array.from({ length: 7 }, (_, i) => s.getPropertyValue(`--cat-${i + 1}`).trim());
  }

  const sectorKey = (n: GNode) => (n.name[0] ?? "?").toUpperCase();
  const groupKey = (n: GNode) => (n.types.length ? [...n.types].sort()[0] : "none");

  function hash(s: string) {
    let h = 0;
    for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) | 0;
    return Math.abs(h);
  }

  function comColour(c: GNode) {
    if (lineColour === "commodity") return cats[hash(c.name) % cats.length];
    for (const [t, token] of COM_TYPE) if (c.types.includes(t)) return colors[token];
    return colors["com-nrg"];
  }

  function processFill(n: GNode) {
    if (processColour === "sector") return cats[(sectorOf.get(sectorKey(n))?.colour ?? 0) % cats.length];
    if (processColour === "type") return cats[(groupOf.get(groupKey(n)) ?? 0) % cats.length];
    return n.flags.some((f) => RED.has(f)) ? colors.error : n.flags.length ? colors.warn : colors.process;
  }

  function processShape(n: GNode) {
    return SHAPES[(sectorOf.get(sectorKey(n))?.shape ?? 0) % SHAPES.length];
  }

  async function load() {
    error = "";
    tooLarge = "";
    try {
      data = await api.graph(modelId, { focus: focus || undefined, depth, region: region || undefined });
      slide = data.focus == null ? maxLinks(data) : (data.depth ?? 1);
      build(data);
    } catch (e) {
      data = null;
      build({ focus: null, nodes: [], links: [], flags: {} });
      if (e instanceof ApiError && e.status === 413) tooLarge = e.message;
      else error = String(e);
    }
  }

  // A line's curve: a quadratic Bézier bowed to the left of its direction by BEND of its length,
  // so a pair of opposite flows (fuel in, product out) bow apart instead of overlapping.
  const BEND = 0.18;
  function control(s: N, t: N): [number, number] {
    const dx = t.x! - s.x!, dy = t.y! - s.y!;
    return [(s.x! + t.x!) / 2 - dy * BEND, (s.y! + t.y!) / 2 + dx * BEND];
  }

  // sectors and process groups, most processes first: shape and colour by rank. The server ranks
  // them over the whole network, so a sector looks the same in every view of the model.
  function categorise(g: GraphData) {
    const count = (key: (n: GNode) => string) => {
      const m = new Map<string, number>();
      for (const n of g.nodes) if (n.kind === "process") m.set(key(n), (m.get(key(n)) ?? 0) + 1);
      return [...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
    };
    const shown = new Map(count(sectorKey));
    sectors = (g.sectors ?? count(sectorKey))
      // 7 shapes x 7 colours: past the 7th sector, the colours shift so no two sectors look alike
      .map(([key, n], i) => ({ key, count: n, shape: i % SHAPES.length, colour: (i + Math.floor(i / 7)) % 7 }))
      .filter((s) => shown.has(s.key));
    sectorOf = new Map(sectors.map((s) => [s.key, s]));
    const inView = new Map(count(groupKey));
    groups = (g.groups ?? count(groupKey))
      .map(([key, n], i) => ({ key, count: n, colour: i }))
      .filter((x) => inView.has(x.key));
    groupOf = new Map(groups.map((x) => [x.key, x.colour]));
  }

  // the RES's sides: -1 for an M process (left), +1 for a demand commodity (right), 0 otherwise
  const SIDE_PULL = 0.5; // stronger than the heaviest node's gravity (0.18)
  const side = (n: GNode) =>
    n.kind === "process" && /^m/i.test(n.name) ? -1 : n.kind === "commodity" && n.types.includes("DEM") ? 1 : 0;

  function build(g: GraphData) {
    sim?.stop();
    categorise(g);
    const old = new Map(nodes.map((n) => [n.id, n]));
    nodes = g.nodes.map((n) => {
      const o = old.get(n.id);
      return { ...n, degree: 0, out: 0, x: o?.x, y: o?.y, fx: o?.fx, fy: o?.fy };
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    links = g.links.map((l) => {
      const s = byId.get(l.source)!, t = byId.get(l.target)!;
      s.out++;
      s.degree++;
      t.degree++;
      // the commodity a line belongs to: its commodity end (for fuel -> emission, the emission)
      return { ...l, source: s, target: t, com: t.kind === "commodity" ? t : s };
    });
    // a commodity with no links has no lines to draw, and under gravity it would only ring the
    // network (2,224 of them in otnyc): it is listed under its "declared, never linked" chip
    nodes = nodes.filter((n) => n.kind === "process" || n.degree > 0);
    pinned = nodes.filter((n) => n.fx != null).length;
    // weight in [0, 1]: a node's links relative to the busiest node's, on a square-root scale so
    // that a mid-sized hub still counts next to ELC
    const maxDegree = Math.max(1, ...nodes.map((n) => n.degree));
    const weight = (n: N) => Math.sqrt(n.degree / maxDegree);
    const big = nodes.length > 800;
    // gravity: a weak pull for every node (so nothing unlinked drifts off), plus one that grows
    // with the node's weight, so the heavy nodes win the centre
    const gravity = (n: N) => 0.02 + 0.16 * weight(n);
    // repulsion grows with weight too: a hub keeps room around it for its many lines
    const repel = (n: N) => (n.kind === "commodity" ? -8 : big ? -16 : -45) * (1 + 3 * weight(n));
    sim = d3
      .forceSimulation<N, L>(nodes)
      .force("link", d3.forceLink<N, L>(links).distance((l) => 24 + 30 * Math.max(weight(l.source as N), weight(l.target as N))))
      .force("charge", d3.forceManyBody<N>().strength(repel).distanceMax(big ? 250 : 600))
      .force("x", d3.forceX<N>(width / 2).strength(gravity))
      .force("y", d3.forceY<N>(height / 2).strength(gravity))
      .force("collide", d3.forceCollide<N>((n) => (n.kind === "commodity" ? 3 : 9)))
      .stop();
    // lay out up front (fewer steps for big models), then fit and draw once: the graph appears
    // settled and in view, with no animation
    const ticks = Math.max(60, Math.min(300, Math.ceil(300_000 / Math.max(1, nodes.length))));
    // left to right, as a RES reads: the M processes (mining and primary supply, sector M) are
    // weighted to the left edge and the demand commodities (COM_TMAP DEM) to the right. First the
    // network settles under gravity alone; its width then sets the two edges, so the pull suits
    // a model of any size. Their pull to the vertical centre is weak, so they spread into columns.
    const sided = nodes.filter((n) => side(n) !== 0);
    if (sided.length && sided.length < nodes.length) {
      sim.tick(Math.ceil(ticks * 0.4));
      const xs = nodes
        .filter((n) => side(n) === 0)
        .map((n) => n.x ?? width / 2)
        .sort((a, b) => a - b);
      const ys = nodes
        .filter((n) => side(n) === 0)
        .map((n) => n.y ?? height / 2)
        .sort((a, b) => a - b);
      const at = (v: number[], q: number) => v[Math.floor((v.length - 1) * q)];
      const lo = at(xs, 0.05), hi = at(xs, 0.95);
      // the two edges a little outside the network, and far enough apart that the drawing has the
      // window's shape (wider than tall) rather than the network's own
      const half = Math.max((hi - lo) / 2 + 0.15 * (hi - lo) + 40, ((at(ys, 0.95) - at(ys, 0.05)) * (width / height) * 0.85) / 2);
      const mid = (lo + hi) / 2;
      const edge = (n: N) => (side(n) < 0 ? mid - half : side(n) > 0 ? mid + half : width / 2);
      sim
        .force("x", d3.forceX<N>(edge).strength((n) => (side(n) ? SIDE_PULL : gravity(n))))
        .force("y", d3.forceY<N>(height / 2).strength((n) => (side(n) ? 0.01 : gravity(n))))
        .alpha(0.8)
        .tick(ticks);
    } else {
      sim.tick(ticks);
    }
    // from now on, little motion: heavy damping and a quick cool-down to a freeze after a drag
    sim.velocityDecay(0.6).alphaDecay(0.12).on("tick", () => (layoutVersion++, draw()));
    layoutVersion++;
    autoFit = true;
    fit();
    draw();
    selected = focus ? (byId.get(focus) ?? null) : selected && byId.get(selected.id) ? byId.get(selected.id)! : null;
  }

  function resort() {
    for (const n of nodes) n.fx = n.fy = null;
    pinned = 0;
    sim?.alpha(0.5).restart();
  }

  // zoom so that every node is in view (until the user zooms or pans themselves)
  let autoFit = true;
  let programmatic = false;
  function fit() {
    if (!canvas || !nodes.length) return;
    const xs = nodes.map((n) => n.x ?? 0), ys = nodes.map((n) => n.y ?? 0);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    // room for the process labels on the right and for the legend along the bottom
    const pad = 40, labelRoom = 90, below = legend?.offsetHeight ?? 0;
    const k = Math.min(2, (width - 2 * pad - labelRoom) / Math.max(1, x1 - x0), (height - 2 * pad - below) / Math.max(1, y1 - y0));
    transform = d3.zoomIdentity
      .translate((width - labelRoom - k * (x0 + x1)) / 2, (height - below - k * (y0 + y1)) / 2)
      .scale(k);
    programmatic = true;
    d3.select(canvas).call(zoom.transform, transform);
    programmatic = false;
  }

  function resetView() {
    if (view === "flow") return flow?.reset();
    autoFit = true;
    fit();
  }

  const symbol = d3.symbol<unknown>();

  function draw() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.k, transform.k);
    const k = Math.sqrt(transform.k);

    // what the hovered or selected node lights up: its lines, and the nodes at their other ends
    const near = new Set<string>();
    const hl = hover ?? selected;
    if (hl)
      for (const l of links) {
        const s = l.source as N, t = l.target as N;
        if (s.id === hl.id) near.add(t.id);
        if (t.id === hl.id) near.add(s.id);
      }
    // a process lights its commodities' whole flows, so producers and users both show
    const litCom = new Set<string>(hl?.kind === "commodity" ? [hl.id] : [...near].filter((id) => id.startsWith("c:")));
    if (hl?.kind === "process")
      for (const l of links) if (litCom.has(l.com.id)) (near.add((l.source as N).id), near.add((l.target as N).id));

    // commodities: lines from producer to junction to user, coloured by commodity
    for (const l of links) {
      const s = l.source as N, t = l.target as N;
      if (s.x == null || t.x == null) continue;
      const lit = hl != null && litCom.has(l.com.id);
      const flagged = l.com.flags.some((f) => RED.has(f));
      ctx.strokeStyle = flagged ? colors.error : comColour(l.com);
      ctx.globalAlpha = hl && !lit ? 0.18 : lit ? 1 : 0.7;
      ctx.lineWidth = (lit ? 2.4 : l.kind === "aggregation" ? 0.7 : 1.3) / k;
      ctx.setLineDash(l.kind === "trade" ? [5, 3] : l.kind === "emission" || l.kind === "aggregation" ? [1.5, 2.5] : []);
      const [cx, cy] = control(s, t);
      ctx.beginPath();
      ctx.moveTo(s.x, s.y!);
      ctx.quadraticCurveTo(cx, cy, t.x, t.y!);
      ctx.stroke();
      // arrowheads where a flow arrives: at a process, or where a commodity ends (a demand, an
      // emission), along the curve's own direction there
      if (t.kind === "process" || t.out === 0) {
        const a = Math.atan2(t.y! - cy, t.x - cx);
        const back = t.kind === "process" ? 8 : 0;
        const ax = t.x - Math.cos(a) * back, ay = t.y! - Math.sin(a) * back;
        ctx.setLineDash([]);
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(ax - Math.cos(a - 0.45) * 6, ay - Math.sin(a - 0.45) * 6);
        ctx.lineTo(ax - Math.cos(a + 0.45) * 6, ay - Math.sin(a + 0.45) * 6);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);

    const processes = nodes.filter((n) => n.kind === "process");
    const labels = processes.length <= 120 || transform.k >= 1.6;
    ctx.font = `${11 / k}px "DM Sans", system-ui, sans-serif`;

    // commodity names where their lines meet (no box or circle: the lines are the commodity)
    for (const n of nodes) {
      if (n.kind !== "commodity" || n.x == null) continue;
      const lit = litCom.has(n.id);
      const flag = n.flags.find((f) => RED.has(f)) ? colors.error : n.flags.length ? colors.warn : "";
      if (flag) {
        // a flagged commodity: a small cross where its lines meet
        ctx.strokeStyle = flag;
        ctx.lineWidth = 2 / k;
        const r = 4 / k;
        ctx.beginPath();
        ctx.moveTo(n.x - r, n.y! - r);
        ctx.lineTo(n.x + r, n.y! + r);
        ctx.moveTo(n.x + r, n.y! - r);
        ctx.lineTo(n.x - r, n.y! + r);
        ctx.stroke();
      }
      // a lit commodity's name is placed with the other hover labels, below
      if (!lit && (flag || (labels && transform.k >= 1.2) || n.id === focus)) {
        ctx.globalAlpha = hl ? 0.35 : 1;
        ctx.fillStyle = flag || comColour(n);
        ctx.font = `italic ${10.5 / k}px "DM Sans", system-ui, sans-serif`;
        ctx.fillText(n.name, n.x + 4 / k, n.y! - 4 / k);
      }
    }
    ctx.globalAlpha = 1;

    // processes: a shape per sector, coloured by the legend's choice
    for (const n of processes) {
      if (n.x == null) continue;
      const dim = hl && n.id !== hl.id && !near.has(n.id);
      ctx.globalAlpha = dim ? 0.3 : 1;
      ctx.save();
      ctx.translate(n.x, n.y!);
      ctx.beginPath();
      symbol.type(processShape(n)).size(n.id === hl?.id ? 150 : 90).context(ctx)();
      ctx.fillStyle = processFill(n);
      ctx.fill();
      ctx.strokeStyle = colors["node-line"] !== "transparent" ? colors["node-line"] : colors.panel;
      ctx.lineWidth = 0.8;
      ctx.stroke();
      const flag = n.flags.find((f) => RED.has(f)) ? colors.error : n.flags.length ? colors.warn : "";
      if (flag || n.id === focus || n.id === selected?.id || n.fx != null) {
        ctx.strokeStyle = flag || colors.accent;
        ctx.lineWidth = n.fx != null && !flag && n.id !== focus && n.id !== selected?.id ? 1 : 2.2;
        ctx.beginPath();
        ctx.arc(0, 0, 9, 0, 2 * Math.PI);
        ctx.stroke();
      }
      ctx.restore();
      if (!(hl && (n.id === hl.id || near.has(n.id))) && (labels || n.flags.length)) {
        ctx.fillStyle = colors.ink;
        ctx.font = `${11 / k}px "DM Sans", system-ui, sans-serif`;
        ctx.fillText(n.name, n.x + 9, n.y! + 3.5);
      }
    }
    ctx.restore();
    ctx.globalAlpha = 1;

    // the hover labels: the lit node's name and those of everything it lights up
    if (hl) {
      const lit = [hl, ...nodes.filter((n) => n.id !== hl.id && (n.kind === "process" ? near.has(n.id) : litCom.has(n.id)))];
      drawLabels(ctx, layoutLabels(ctx, hl, lit.filter((n) => n.x != null)));
    }
  }

  // Hover labels are laid out in screen space, so they keep their size at any zoom, and placed
  // greedily so that none overlaps another or covers a lit shape. Each goes on the outer side of
  // its node: along the ray from the centre of the lit group through the node, at the first free
  // spot (fanning up to 80 degrees either side, then further out). The hovered node's label goes
  // first, then the others from the outside in, so the rim keeps its labels close and the inner
  // ones move out past them. A label moved away from its node gets a leader line; one with no
  // free spot within reach is left out (its node's tooltip still names it).
  type Label = {
    n: N;
    font: string;
    w: number;
    h: number;
    ax: number; // the node, on screen
    ay: number;
    r: number; // the room the node's shape needs
    x: number; // where the label sits (its centre)
    y: number;
  };
  let labelCache = { key: "", labels: [] as Label[] };
  let layoutVersion = 0; // bumped whenever the nodes move

  const LABEL_GAP = 3, LABEL_PAD = 2, LABEL_REACH = 320, CELL = 32;
  const FAN = [0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, 1.4, -1.4];
  type Box = { x0: number; y0: number; x1: number; y1: number };

  function layoutLabels(ctx: CanvasRenderingContext2D, hl: N, lit: N[]): Label[] {
    const key = `${hl.id}|${lit.length}|${transform.x}|${transform.y}|${transform.k}|${width}|${height}|${layoutVersion}`;
    if (key === labelCache.key) return labelCache.labels;
    const k = Math.sqrt(transform.k);
    const all: Label[] = [];
    for (const n of lit) {
      const [ax, ay] = transform.apply([n.x!, n.y!]);
      if (ax < -50 || ay < -50 || ax > width + 50 || ay > height + 50) continue; // off screen
      const process = n.kind === "process";
      const size = (process ? 11 : 10.5) * k;
      const font = `${process ? "" : "italic "}${n.id === hl.id || !process ? "600 " : ""}${size}px "DM Sans", system-ui, sans-serif`;
      ctx.font = font;
      const r = process ? Math.max(5, 9 * transform.k) : Math.max(3, 4 * k);
      all.push({ n, font, w: ctx.measureText(n.name).width, h: size * 1.15, ax, ay, r, x: ax, y: ay });
    }
    // what is taken: the lit shapes, then each label as it is placed, in a grid of CELL px
    const grid = new Map<number, Box[]>();
    const cells = (b: Box, f: (c: number) => boolean) => {
      for (let gx = Math.floor(b.x0 / CELL); gx <= Math.floor(b.x1 / CELL); gx++)
        for (let gy = Math.floor(b.y0 / CELL); gy <= Math.floor(b.y1 / CELL); gy++) if (f(gx * 65536 + gy)) return true;
      return false;
    };
    const take = (b: Box) =>
      cells(b, (c) => {
        const here = grid.get(c);
        if (here) here.push(b);
        else grid.set(c, [b]);
        return false; // visit every cell
      });
    const taken = (b: Box) =>
      cells(b, (c) => grid.get(c)?.some((o) => o.x0 < b.x1 && b.x0 < o.x1 && o.y0 < b.y1 && b.y0 < o.y1) ?? false);
    for (const l of all) take({ x0: l.ax - l.r, y0: l.ay - l.r, x1: l.ax + l.r, y1: l.ay + l.r });

    const cx = d3.mean(all, (l) => l.ax) ?? 0, cy = d3.mean(all, (l) => l.ay) ?? 0;
    const out = (l: Label) => Math.hypot(l.ax - cx, l.ay - cy);
    const order = [...all].sort((a, b) => +(b.n.id === hl.id) - +(a.n.id === hl.id) || out(b) - out(a));
    const labels: Label[] = [];
    for (const l of order) {
      // the outward direction (a node at the centre: to its right)
      const base = out(l) < 1 ? 0 : Math.atan2(l.ay - cy, l.ax - cx);
      let spot: Box | null = null;
      for (let d = l.r + LABEL_GAP; d < LABEL_REACH && !spot; d += 4)
        for (const f of FAN) {
          const ux = Math.cos(base + f), uy = Math.sin(base + f);
          const x = Math.max(l.w / 2 + 2, Math.min(width - l.w / 2 - 2, l.ax + ux * (d + l.w / 2)));
          const y = Math.max(l.h / 2 + 2, Math.min(height - l.h / 2 - 2, l.ay + uy * (d + l.h / 2)));
          const b = { x0: x - l.w / 2 - LABEL_PAD / 2, y0: y - l.h / 2 - LABEL_PAD / 2, x1: x + l.w / 2 + LABEL_PAD / 2, y1: y + l.h / 2 + LABEL_PAD / 2 };
          if (taken(b)) continue;
          [l.x, l.y, spot] = [x, y, b];
          break;
        }
      if (spot) {
        take(spot);
        labels.push(l);
      }
    }
    labelCache = { key, labels };
    return labels;
  }

  function drawLabels(ctx: CanvasRenderingContext2D, labels: Label[]) {
    // leader lines first, so the labels' halos sit over them
    ctx.strokeStyle = colors.muted;
    ctx.lineWidth = 0.8;
    ctx.globalAlpha = 0.7;
    ctx.beginPath();
    for (const l of labels) {
      const qx = Math.max(l.x - l.w / 2, Math.min(l.x + l.w / 2, l.ax));
      const qy = Math.max(l.y - l.h / 2, Math.min(l.y + l.h / 2, l.ay));
      const d = Math.hypot(qx - l.ax, qy - l.ay);
      if (d <= l.r + 8) continue;
      ctx.moveTo(l.ax + ((qx - l.ax) / d) * l.r, l.ay + ((qy - l.ay) / d) * l.r);
      ctx.lineTo(qx, qy);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = 3;
    ctx.strokeStyle = colors.panel;
    for (const l of labels) {
      ctx.font = l.font;
      const flag = l.n.flags.find((f) => RED.has(f)) ? colors.error : l.n.flags.length ? colors.warn : "";
      ctx.fillStyle = l.n.kind === "process" ? colors.ink : flag || comColour(l.n);
      ctx.strokeText(l.n.name, l.x, l.y);
      ctx.fillText(l.n.name, l.x, l.y);
    }
    ctx.textAlign = "start";
    ctx.textBaseline = "alphabetic";
  }

  // what is under the pointer: a process, else a commodity (where its lines meet, or on a line)
  function pick(px: number, py: number): N | null {
    const [mx, my] = transform.invert([px, py]);
    let best: N | null = null;
    let d2 = (12 / transform.k) ** 2;
    for (const n of nodes) {
      if (n.kind !== "process") continue;
      const dx = n.x! - mx, dy = n.y! - my, d = dx * dx + dy * dy;
      if (d < d2) [d2, best] = [d, n];
    }
    if (best) return best;
    d2 = (8 / transform.k) ** 2;
    for (const n of nodes) {
      if (n.kind !== "commodity") continue;
      const dx = n.x! - mx, dy = n.y! - my, d = dx * dx + dy * dy;
      if (d < d2) [d2, best] = [d, n];
    }
    if (best) return best;
    // on a line: each curve as 8 straight pieces, close enough at 4 px
    const r = 4 / transform.k;
    d2 = r * r;
    for (const l of links) {
      const s = l.source as N, t = l.target as N;
      const [cx, cy] = control(s, t);
      if (mx < Math.min(s.x!, t.x!, cx) - r || mx > Math.max(s.x!, t.x!, cx) + r) continue;
      if (my < Math.min(s.y!, t.y!, cy) - r || my > Math.max(s.y!, t.y!, cy) + r) continue;
      let [px, py] = [s.x!, s.y!];
      for (let i = 1; i <= 8; i++) {
        const u = i / 8, w = 1 - u;
        const qx = w * w * s.x! + 2 * w * u * cx + u * u * t.x!;
        const qy = w * w * s.y! + 2 * w * u * cy + u * u * t.y!;
        const vx = qx - px, vy = qy - py;
        const f = Math.max(0, Math.min(1, ((mx - px) * vx + (my - py) * vy) / (vx * vx + vy * vy || 1)));
        const dx = px + f * vx - mx, dy = py + f * vy - my, d = dx * dx + dy * dy;
        if (d < d2) [d2, best] = [d, l.com];
        [px, py] = [qx, qy];
      }
    }
    return best;
  }

  const at = (ev: MouseEvent) => pick(...(d3.pointer(ev, canvas) as [number, number]));

  const zoom = d3
    .zoom<HTMLCanvasElement, unknown>()
    .scaleExtent([0.1, 8])
    .on("zoom", (e) => {
      transform = e.transform;
      if (!programmatic && e.sourceEvent) autoFit = false;
      draw();
    });

  // drag a process: it stays where it is dropped (pinned), and the rest re-sorts around it, with
  // heavy damping, then freezes; "Re-sort" unpins everything
  const drag = d3
    .drag<HTMLCanvasElement, unknown, N>()
    .subject((e) => {
      const n = pick(...(d3.pointer(e.sourceEvent, canvas) as [number, number]));
      return n?.kind === "process" ? n : (undefined as unknown as N);
    })
    .on("start", (e) => {
      sim?.alphaTarget(0.08).restart();
      e.subject.fx = e.subject.x;
      e.subject.fy = e.subject.y;
      canvas.style.cursor = "grabbing";
    })
    .on("drag", (e) => {
      const [x, y] = transform.invert(d3.pointer(e.sourceEvent, canvas) as [number, number]);
      e.subject.fx = x;
      e.subject.fy = y;
    })
    .on("end", () => {
      sim?.alphaTarget(0);
      pinned = nodes.filter((n) => n.fx != null).length;
      canvas.style.cursor = "grab";
    });

  function setup(node: HTMLCanvasElement) {
    readColors();
    const box = node.parentElement!; // the stage; bind:this is not set yet when an action runs
    const resize = () => {
      width = box.clientWidth;
      height = Math.max(420, box.clientHeight);
      const dpr = window.devicePixelRatio || 1;
      node.width = width * dpr;
      node.height = height * dpr;
      node.style.width = `${width}px`;
      node.style.height = `${height}px`;
      draw();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(box);
    resize();
    // drag before zoom, so a press on a process drags it and a press elsewhere pans
    d3.select(node).call(drag).call(zoom).on("dblclick.zoom", null);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const recolor = () => {
      readColors();
      draw();
    };
    mq.addEventListener("change", recolor);
    window.addEventListener("otui-theme", recolor); // a company theme applied or reset
    return {
      destroy: () => (ro.disconnect(), mq.removeEventListener("change", recolor), window.removeEventListener("otui-theme", recolor)),
    };
  }

  $effect(() => {
    void [focus, depth, region];
    load();
  });
  $effect(() => {
    void [processColour, lineColour];
    draw();
  });

  onDestroy(() => sim?.stop());

  let searchTimer: ReturnType<typeof setTimeout>;
  function search() {
    clearTimeout(searchTimer);
    const q = query.trim();
    if (!q) return (hits = []);
    searchTimer = setTimeout(async () => (hits = await api.search(modelId, q)), 150);
  }

  function go(id: string) {
    focus = id;
    hits = [];
    query = "";
  }

  // The slider: from n (the anchor's whole connected part) down to 1 link. The anchor is the
  // searched node or, for the whole model, its most central node.
  const maxLinks = (g: GraphData | null) => Math.max(0, (g?.reach?.length ?? 1) - 1);
  let anchor = $derived(data?.focus ?? data?.centre ?? "");
  let fitDepth = $derived(
    data?.reach ? Math.max(0, ...data.reach.map((n, d) => (n <= (data?.limit ?? Infinity) ? d : 0))) : 0,
  );
  let slideCount = $derived(data?.reach?.[slide] ?? 0);

  function applySlide() {
    if (!data) return;
    if (slide > fitDepth) slide = fitDepth;
    if (!focus && !data.auto && slide === maxLinks(data)) return; // already the whole model
    if (!focus) focus = anchor;
    depth = slide;
  }

  // the process flow draws a few hundred processes at most: the most links from the anchor that
  // keep the nodes within FLOW_NODES (about the processes it can draw, with their commodities)
  const FLOW_NODES = 400;
  let flowDepth = $derived(
    data?.reach ? Math.max(1, ...data.reach.map((n, d) => (d > 0 && n <= FLOW_NODES ? d : 1))) : 1,
  );
  function fewerForFlow() {
    if (!data) return;
    slide = flowDepth;
    if (!focus) focus = anchor;
    depth = slide;
  }

  function wholeModel() {
    focus = "";
    depth = undefined;
  }

  let flagged = $derived(
    (data?.nodes ?? []).filter((n) => n.flags.includes(flagOpen)).sort((a, b) => a.name.localeCompare(b.name)),
  );
  let counts = $derived({
    processes: (data?.nodes ?? []).filter((n) => n.kind === "process").length,
    commodities: (data?.nodes ?? []).filter((n) => n.kind === "commodity").length,
  });
  const swatch = (shape: number) => d3.symbol(SHAPES[shape % SHAPES.length], 64)() ?? "";
  const LEGEND_MAX = 10;
</script>

<div class="graph">
  <div class="bar">
    <div class="views" role="group" aria-label="Drawing">
      <button class:on={view === "network"} aria-pressed={view === "network"} onclick={() => setView("network")}
        title="A force-directed network: the most connected in the middle">Network</button
      ><button class:on={view === "flow"} aria-pressed={view === "flow"} onclick={() => setView("flow")}
        title="An engineering process flow diagram: process boxes, vertical commodity lines, supply to demand from left to right"
        >Process flow</button
      >
    </div>
    <div class="search">
      <input placeholder="Find a process or commodity (code or words)" bind:value={query} oninput={search} aria-label="Find a node" />
      {#if hits.length}
        <ul class="hits panel">
          {#each hits as h (h.id)}
            <li>
              <button onclick={() => go(h.id)}>
                <span class="dot {h.kind}"></span><span class="mono">{h.name}</span>
                <span class="muted">{h.description}</span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
    {#if anchor && maxLinks(data) >= 1}
      <label class="reach" title="How many links out from {anchor.slice(2)} to show">
        Links from <span class="mono">{anchor.slice(2)}</span>
        <input type="range" min="1" max={maxLinks(data)} step="1" bind:value={slide} onchange={applySlide} />
        <span class="muted">
          {slide} of {maxLinks(data)} · {fmt(slideCount, 0)} nodes{slide > fitDepth ? " · too many to draw" : ""}
        </span>
      </label>
    {/if}
    {#if regions.length > 1}
      <label>Region
        <select bind:value={region}>
          <option value="">All</option>
          {#each regions as r}<option value={r}>{r}</option>{/each}
        </select>
      </label>
    {/if}
    <button onclick={wholeModel} disabled={!focus && !data?.auto}>Whole model</button>
    <button onclick={resetView}>Reset view</button>
    {#if view === "network"}
      <button onclick={resort} disabled={!pinned} title="Release the processes you have dragged and let the layout sort itself">Re-sort</button>
    {/if}
    {#if data}
      <span class="muted count">{fmt(counts.processes, 0)} processes · {fmt(counts.commodities, 0)} commodities</span>
    {/if}
  </div>

  {#if data && Object.keys(data.flags).length}
    <div class="flags">
      {#each Object.entries(data.flags) as [f, n]}
        <button
          class="flag"
          class:red={RED.has(f)}
          class:open={flagOpen === f}
          title={f === "unused" ? "Not drawn: with no links, these commodities have no lines. Click to list them." : undefined}
          onclick={() => (flagOpen = flagOpen === f ? "" : f)}
        >
          {n} {FLAG_LABEL[f] ?? f}
        </button>
      {/each}
      {#if flagOpen}
        <div class="flaglist">
          {#each flagged as n (n.id)}<button class="mono" onclick={() => go(n.id)}>{n.name}</button>{/each}
        </div>
      {/if}
    </div>
  {/if}
  {#if error}<p class="err">{error}</p>{/if}
  {#if tooLarge}
    <div class="panel large">
      <strong>This {region ? `region` : `model`} is too large to draw whole.</strong>
      <span class="muted">{tooLarge}.</span>
      <span>Search above for a process or commodity to see its neighbourhood{regions.length > 1 && !region ? ", or pick a region" : ""}.</span>
    </div>
  {/if}
  {#if data?.auto}
    <p class="note">
      <strong>Too large to draw whole</strong> ({fmt(data.reach?.at(-1), 0)} connected nodes; the limit is
      {fmt(data.limit, 0)}): centred on <span class="mono">{anchor.slice(2)}</span>, the most central node, up to
      {data.depth} links out. Search to centre on another node.
    </p>
  {/if}
  <DummyNote {modelId} {solveKey} />

  {#if view === "flow" && data}
    <ProcessFlow
      bind:this={flow}
      {data}
      onfocus={go}
      fewer={anchor ? { label: `${flowDepth} link${flowDepth === 1 ? "" : "s"} from ${anchor.slice(2)}`, run: fewerForFlow } : undefined}
    />
  {/if}
  <div class="stage panel" class:hidden={view === "flow"} bind:this={wrap}>
    <canvas
      use:setup
      bind:this={canvas}
      onmousemove={(e) => {
        if (e.buttons) return void (hover = null); // no tooltip while dragging or panning
        hover = at(e);
        tip = { x: e.offsetX, y: e.offsetY, right: width - e.offsetX, left: e.offsetX > width - 340 };
        canvas.style.cursor = hover?.kind === "process" ? "move" : hover ? "pointer" : "grab";
        draw();
      }}
      onmouseleave={() => ((hover = null), draw())}
      onclick={(e) => {
        const n = at(e);
        if (n) {
          selected = n;
          if (e.detail === 2 || focus) focus = n.id;
          draw();
        }
      }}
    ></canvas>

    <div class="legend" bind:this={legend}>
      <div class="row">
        <label>Processes by
          <select bind:value={processColour} aria-label="Colour processes by">
            <option value="sector">sector</option>
            <option value="type">process type</option>
            <option value="checks">checks</option>
          </select>
        </label>
        {#each sectors.slice(0, LEGEND_MAX) as s (s.key)}
          <span title={SECTOR_HINT[s.key] ? `${s.key}: ${SECTOR_HINT[s.key]} (by its code's first letter)` : `${s.key}: by its code's first letter`}>
            <svg width="12" height="12" viewBox="-6 -6 12 12" aria-hidden="true">
              <path d={swatch(s.shape)} fill={processColour === "sector" ? `var(--cat-${(s.colour % 7) + 1})` : "var(--muted)"} />
            </svg>
            <b>{s.key}</b>{SECTOR_HINT[s.key] ? ` ${SECTOR_HINT[s.key]}` : ""} ({s.count})
          </span>
        {/each}
        {#if sectors.length > LEGEND_MAX}<span>+{sectors.length - LEGEND_MAX} more</span>{/if}
        {#if processColour === "type"}
          {#each groups.slice(0, LEGEND_MAX) as g (g.key)}
            <span><i class="sw" style="background: var(--cat-{(g.colour % 7) + 1})"></i>{g.key} ({g.count})</span>
          {/each}
        {:else if processColour === "checks"}
          <span><i class="sw" style="background: var(--process)"></i>no issue</span>
          <span><i class="sw" style="background: var(--error)"></i>dead end</span>
          <span><i class="sw" style="background: var(--warn)"></i>unlinked</span>
        {/if}
      </div>
      <div class="row">
        <label>Commodity lines by
          <select bind:value={lineColour} aria-label="Colour commodity lines by">
            <option value="type">commodity type</option>
            <option value="commodity">commodity</option>
          </select>
        </label>
        {#if lineColour === "type"}
          {#each COM_TYPE as [, token, label]}<span><i class="ln" style="border-color: var(--{token})"></i>{label}</span>{/each}
        {/if}
        <span><i class="ln"></i>flow</span>
        <span><i class="ln dash"></i>trade</span>
        <span><i class="ln dotted"></i>emits / counts into</span>
        <span><i class="x">×</i>commodity with an issue</span>
      </div>
    </div>

    {#if hover}
      <!-- the hovered node's own description: PRC_DESC for a process, COM_DESC for a commodity -->
      <div
        class="tip panel"
        role="tooltip"
        style:left={tip.left ? "auto" : `${tip.x + 14}px`}
        style:right={tip.left ? `${tip.right + 14}px` : "auto"}
        style:top={`${Math.max(4, tip.y - 10)}px`}
      >
        <div>
          <strong class="mono">{hover.name}</strong>
          <span class="muted">
            {hover.kind}{hover.kind === "process" ? ` · sector ${sectorKey(hover)}` : ""}{hover.types.length
              ? ` · ${hover.types.join(", ")}`
              : ""}
          </span>
        </div>
        <div class:muted={!hover.description}>{hover.description || "No description in the model"}</div>
        {#each hover.flags as f}<div class:err={RED.has(f)} class:warn={!RED.has(f)}>⚠ {FLAG_LABEL[f] ?? f}</div>{/each}
      </div>
    {/if}

    {#if selected}
      <div class="info panel">
        <button class="close" onclick={() => ((selected = null), draw())} aria-label="Close">✕</button>
        <div><span class="dot {selected.kind}"></span><strong class="mono">{selected.name}</strong> <span class="muted">{selected.kind}{selected.kind === "process" ? ` · sector ${sectorKey(selected)}` : ""}</span></div>
        {#if selected.description}<p>{selected.description}</p>{/if}
        {#if selected.types.length}<p class="muted">Type/groups: {selected.types.join(", ")}</p>{/if}
        <p class="muted">Regions: {selected.regions.join(", ") || "none"}</p>
        {#each selected.flags as f}<p class:err={RED.has(f)} class:warn={!RED.has(f)}>⚠ {FLAG_LABEL[f] ?? f}</p>{/each}
        {#if focus !== selected.id}<button class="primary" onclick={() => (focus = selected!.id)}>Centre on this</button>{/if}
      </div>
    {/if}
  </div>
  <p class="muted small" hidden={view === "flow"}>
    Processes are the shapes; commodities are the lines between them. The most connected sit in the middle, the least on
    the rim; the M processes (mining and primary supply) are drawn to the left and the demand commodities to the right. Scroll to zoom, drag the background to pan, drag a process to move it (the rest re-sorts around it). Click for details; double-click to centre on it.
  </p>
</div>

<style>
  .graph {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    height: 100%;
    min-height: 0;
  }
  .bar {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .bar label {
    display: flex;
    gap: 0.3rem;
    align-items: center;
    font-size: 0.9rem;
  }
  .search {
    position: relative;
    flex: 1 1 260px;
    max-width: 440px;
  }
  .search input {
    width: 100%;
  }
  .hits {
    position: absolute;
    z-index: 5;
    top: 100%;
    left: 0;
    right: 0;
    margin: 2px 0 0;
    padding: 0.25rem;
    list-style: none;
    max-height: 320px;
    overflow: auto;
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.15);
  }
  .hits button {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    width: 100%;
    border: 0;
    text-align: left;
    background: transparent;
  }
  .hits button:hover {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
  }
  .hits .muted {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.85rem;
  }
  .count {
    margin-left: auto;
    font-size: 0.85rem;
  }
  .flags {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
    align-items: center;
  }
  .flag {
    border-color: var(--warn);
    color: var(--warn);
    font-size: 0.85rem;
    padding: 0.15rem 0.6rem;
    border-radius: 999px;
  }
  .flag.red {
    border-color: var(--error);
    color: var(--error);
  }
  .flag.open {
    background: color-mix(in srgb, currentColor 12%, var(--panel));
  }
  .flaglist {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    width: 100%;
  }
  .flaglist button {
    font-size: 0.8rem;
    padding: 0.1rem 0.4rem;
  }
  .stage.hidden {
    display: none;
  }
  .views {
    display: inline-flex;
  }
  .views button {
    border-radius: 0;
  }
  .views button:first-child {
    border-radius: 6px 0 0 6px;
  }
  .views button:last-child {
    border-radius: 0 6px 6px 0;
    margin-left: -1px;
  }
  .views button.on {
    background: var(--accent);
    color: var(--accent-ink);
    border-color: var(--accent);
    font-weight: 600;
  }
  .stage {
    position: relative;
    flex: 1;
    min-height: 420px;
    overflow: hidden;
  }
  canvas {
    display: block;
    cursor: grab;
  }
  .legend {
    position: absolute;
    left: 0.5rem;
    bottom: 0.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    max-width: calc(100% - 1rem);
    font-size: 0.75rem;
    color: var(--muted);
    background: color-mix(in srgb, var(--panel) 85%, transparent);
    padding: 0.25rem 0.5rem;
    border-radius: 6px;
  }
  .legend .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.2rem 0.7rem;
  }
  .legend span,
  .legend label {
    display: inline-flex;
    gap: 0.25rem;
    align-items: center;
  }
  .legend select {
    font-size: 0.75rem;
    padding: 0 0.2rem;
  }
  .legend b {
    color: var(--ink);
  }
  .sw {
    width: 9px;
    height: 9px;
    border-radius: 2px;
  }
  .x {
    font-style: normal;
    font-weight: 700;
    color: var(--error);
  }
  i {
    display: inline-block;
  }
  .sq {
    width: 9px;
    height: 9px;
    background: var(--process);
  }
  .ci {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    box-shadow: inset 0 0 0 1px var(--node-line);
  }
  .ci.mat {
    background: var(--com-mat);
  }
  .ci.nrg {
    background: var(--com-nrg);
  }
  .ci.dem {
    background: var(--com-dem);
  }
  .ci.env {
    background: var(--com-env);
  }
  .ln {
    width: 16px;
    border-top: 2px solid var(--muted);
  }
  .ln.dash {
    border-top-style: dashed;
  }
  .ln.dotted {
    border-top-style: dotted;
  }
  .ring {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    border: 2px solid var(--warn);
  }
  .ring.red {
    border-color: var(--error);
  }
  .dot {
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--com-nrg);
    margin-right: 0.2rem;
  }
  .dot.process {
    border-radius: 2px;
    background: var(--process);
  }
  .tip {
    position: absolute;
    z-index: 4;
    max-width: 320px;
    padding: 0.35rem 0.6rem;
    font-size: 0.85rem;
    line-height: 1.35;
    pointer-events: none;
    box-shadow: 0 4px 14px rgb(0 0 0 / 0.15);
  }
  .info {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    width: min(320px, calc(100% - 1rem));
    padding: 0.6rem 0.8rem;
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.12);
    font-size: 0.9rem;
  }
  .info p {
    margin: 0.3rem 0;
  }
  .close {
    float: right;
    border: 0;
    background: transparent;
    padding: 0 0.2rem;
  }
  .err {
    color: var(--error);
  }
  .large {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.75rem 1rem;
  }
  .note {
    margin: 0;
    font-size: 0.85rem;
  }
  .reach {
    display: inline-flex;
    gap: 0.4rem;
    align-items: center;
  }
  .reach input {
    width: 160px;
    accent-color: var(--accent);
  }
  .warn {
    color: var(--warn);
  }
  .small {
    font-size: 0.85rem;
    margin: 0;
  }
</style>
