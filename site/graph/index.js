// Link graph page (/graaf): notes as nodes, links as edges, drawn by force-graph on a canvas.
// Owner: WP-I (graph). Contract: docs/CONTRACTS.md, "HTTP endpoints" row I.
import { api, on, registerPage, toast } from "/static/app.js";

const LABEL_PX = 8; // a node at least this large on screen keeps its label without hovering
const COOLDOWN_TICKS = 20; // animated ticks after the warm-up
const ALPHA_MIN = 0.001; // d3-force's default: the layout counts as finished at this heat
const FIT_MAX_ZOOM = 4; // fitting two or three notes must not blow them up to discs filling the page

const radius = (node) => 3 + 1.5 * Math.sqrt(node.degree);

registerPage("graph", async () => {
  const root = document.getElementById("graaf");
  const form = document.getElementById("graaf-filtrid");
  const box = document.getElementById("graaf-louend");
  const empty = document.getElementById("graaf-tuhi");
  const count = document.getElementById("graaf-arv");
  // Loaded here rather than imported at the top: app.js loads this module on every page.
  const { default: ForceGraph } = await import("/static/vendor/force-graph.js");

  let colors = readColors();
  let hover = null;
  let lit = null; // ids of the hovered node and its neighbours
  let neighbours = new Map();
  let fitPending = false;
  let optionsFilled = false;
  let loads = 0;

  const touches = (link) => link.source === hover || link.target === hover;

  function drawNode(node, ctx, scale) {
    const r = radius(node);
    ctx.globalAlpha = lit && !lit.has(node.id) ? 0.15 : 1;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = node === hover ? colors.accent : colors.ink;
    ctx.fill();
    if (node === hover) {
      ctx.lineWidth = 2 / scale;
      ctx.strokeStyle = colors.ink;
      ctx.stroke();
    }
    if (lit ? lit.has(node.id) : r * scale >= LABEL_PX) {
      const text = node.title || node.slug;
      const y = node.y + r + 2 / scale;
      ctx.font = `600 ${12 / scale}px "Schibsted Grotesk", sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      // A paper-coloured halo keeps the label readable where it crosses edges.
      ctx.lineWidth = 3 / scale;
      ctx.strokeStyle = colors.paper;
      ctx.strokeText(text, node.x, y);
      ctx.fillStyle = colors.ink;
      ctx.fillText(text, node.x, y);
    }
    ctx.globalAlpha = 1;
  }

  // A settled graph only repaints when a drawing prop is set, so setting one forces the repaint.
  const repaint = () => fg.nodeCanvasObject(drawNode);

  const fg = new ForceGraph(box)
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint((node, color, ctx, scale) => {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius(node) + 3 / scale, 0, 2 * Math.PI);
      ctx.fill();
    })
    .linkColor((link) => (!lit ? colors.edge : touches(link) ? colors.edgeHi : colors.edgeDim))
    .linkWidth((link) => (lit && touches(link) ? 2 : 1))
    .d3Force("gravity", gravity(0.05))
    .cooldownTicks(COOLDOWN_TICKS)
    .onEngineTick(() => {
      if (!fitPending) return;
      fitPending = false;
      fg.zoomToFit(0, 40);
      if (fg.zoom() > FIT_MAX_ZOOM) fg.zoom(FIT_MAX_ZOOM);
    })
    .onEngineStop(() => (root.dataset.settled = "1"))
    .onNodeHover((node) => {
      hover = node || null;
      lit = node ? new Set([node.id, ...neighbours.get(node.id)]) : null;
      repaint();
    })
    .onNodeClick((node) => (location.href = "/m/" + node.slug));
  if (root.dataset.test) window.__graph = fg;

  new ResizeObserver(() => fg.width(box.clientWidth).height(box.clientHeight)).observe(box);
  on("theme:changed", () => {
    colors = readColors();
    repaint();
  });
  form.addEventListener("change", load);

  async function load() {
    const seq = ++loads;
    const filtered = Boolean(form.folder.value || form.tag.value || !form.orphans.checked);
    const params = new URLSearchParams({ folder: form.folder.value, tag: form.tag.value, orphans: form.orphans.checked ? "1" : "0" });
    root.dataset.settled = "0";
    let data;
    try {
      data = await api("/api/graph?" + params);
    } catch (err) {
      toast(err.message, "error");
      return;
    }
    if (seq !== loads) return; // a newer filter choice is already on its way
    if (!optionsFilled && !filtered) {
      fillOptions(form.folder, data.nodes.flatMap((n) => ancestors(n.folder)));
      fillOptions(form.tag, data.nodes.flatMap((n) => n.tags));
      optionsFilled = true;
    }
    neighbours = new Map(data.nodes.map((n) => [n.id, new Set()]));
    for (const e of data.edges) {
      neighbours.get(e.source).add(e.target);
      neighbours.get(e.target).add(e.source);
    }
    hover = lit = null;
    count.textContent = `${data.nodes.length} ${data.nodes.length === 1 ? "märge" : "märget"} · ${data.edges.length} ${data.edges.length === 1 ? "seos" : "seost"}`;
    empty.textContent = filtered ? "Valikule vastavaid märkmeid pole." : "Märkmeid veel pole. Siin on iga märge täpp ja iga link nende vahel joon.";
    empty.hidden = data.nodes.length > 0;
    box.hidden = !empty.hidden;
    // Most of the layout runs as warm-up ticks before the first frame. A tick costs time in
    // proportion to the notes, so a small vault gets a fully relaxed layout and a large one a
    // coarser one; either way it stands still within about a second.
    const ticks = Math.min(300, Math.max(40, Math.round(60000 / data.nodes.length)));
    fg.warmupTicks(ticks).d3AlphaDecay(1 - ALPHA_MIN ** (1 / (ticks + COOLDOWN_TICKS)));
    fitPending = true;
    fg.graphData({ nodes: data.nodes, links: data.edges });
    if (!data.nodes.length) root.dataset.settled = "1";
  }

  await load();
});

// Pulls every node gently towards the centre. Without it, notes with no links drift to the edges
// and zoom-to-fit shrinks everything else.
function gravity(strength) {
  let nodes = [];
  const force = (alpha) => {
    for (const n of nodes) {
      n.vx -= n.x * strength * alpha;
      n.vy -= n.y * strength * alpha;
    }
  };
  force.initialize = (all) => (nodes = all);
  return force;
}

function readColors() {
  const css = getComputedStyle(document.documentElement);
  const slate = css.getPropertyValue("--slate").trim();
  return {
    ink: css.getPropertyValue("--ink").trim(),
    paper: css.getPropertyValue("--paper").trim(),
    accent: css.getPropertyValue("--hivis").trim(),
    edge: withAlpha(slate, 0.5),
    edgeHi: slate,
    edgeDim: withAlpha(slate, 0.1),
  };
}

// A canvas normalises any opaque CSS colour to #rrggbb, which then takes an alpha byte.
const probe = document.createElement("canvas").getContext("2d");
function withAlpha(color, alpha) {
  probe.fillStyle = color;
  return probe.fillStyle + Math.round(alpha * 255).toString(16).padStart(2, "0");
}

function ancestors(folder) {
  const parts = folder ? folder.split("/") : [];
  return parts.map((_, i) => parts.slice(0, i + 1).join("/"));
}

function fillOptions(select, values) {
  for (const v of [...new Set(values)].sort((a, b) => a.localeCompare(b, "et"))) select.add(new Option(v, v));
}
