"""Figure 3.y: Operationalising relations in this thesis.

A single row of four panels that carries one network through the four moves
of the method: nodes as tendencies identified through instances, ties
qualified by the relational function they carry, categories as operations
that relations cross, reproduce or refuse, and the function as what must
persist over time. Panels 1 to 3 share their node positions, so each panel
adds exactly one move.

The figure is drawn on a 1600 x 800 design canvas (1 design px = 0.1 mm at
the 16 cm print width) and written as SVG, PDF and 300 dpi PNG. The script
fails if a text block overlaps another or runs off the canvas, or if any text
is set below 8 pt at print size.

    python make_figure.py            # writes into ./output
    python make_figure.py --out DIR
"""

import argparse
import math
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

# ---------------------------------------------------------------------------
# Canvas and units
# ---------------------------------------------------------------------------

W, H = 1600, 800                      # design canvas in px
PRINT_W_CM = 16.0
PRINT_H_CM = PRINT_W_CM * H / W       # 8.0 cm
DPI_DESIGN = W / (PRINT_W_CM / 2.54)  # 254 design px per inch
PT = 72.0 / DPI_DESIGN                # one design px in points

FONT = ["Arial", "Liberation Sans", "DejaVu Sans"]
FS_TEXT = 8.0                         # minimum size at print size
FS_HEAD = 9.5
FS_CLAIM = 9.0
LEADING = 1.25

# ---------------------------------------------------------------------------
# Palette (as in the relevance, qualification and significance figure)
# ---------------------------------------------------------------------------

GEN = "#1F6F78"                       # generative: solid, 3.5 px
EXT = "#C4622D"                       # extractive: dashed 9/6, 3 px
UNQ = "#A3A8AE"                       # not qualified: 1.2 px
BAND = "#E9EDF1"                      # assignment by the order

INK = "#24282C"
INK_SOFT = "#5A6068"
INK_FAINT = "#9AA0A6"
DOT = "#3E444A"
FIELD = "#F3F5F7"                     # inside of a tendency
HALO = "#E4E8EC"
TAB = "#DADEE2"
RULE = "#C9CDD2"

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

N_PANELS = 4
GUTTER = 52
PANEL_W = (W - (N_PANELS - 1) * GUTTER) / N_PANELS    # 361
PANEL_X = [i * (PANEL_W + GUTTER) for i in range(N_PANELS)]

HEAD_BASE = 70                        # baseline of the last header line
DIAG_Y = 96                           # top of the diagram area
DIAG_H = 320
LABEL_Y = DIAG_Y + DIAG_H + 42
CLAIM_Y = 636
FOOT_Y = 732

HEADERS = [
    "What is a node?",
    "What is a tie?",
    "Where do categories sit?",
    "What must persist over time?",
]
LABELS = [
    "structured tendency identified through instances; "
    "the name serves only as an index",
    "tie qualified by the relational function it carries; "
    "a trace of the relation, not a proxy",
    "categories as operations that relations reproduce or refuse",
    "the function must persist; units may change",
]
CLAIM_HEAD = "What can be claimed?"
CLAIM = ("What relations do, and whether this holds as a tendency across "
         "layers, sites and time")
SCHEMATIC = "Schematic illustration, not a plot of the data."

ALT_TEXT = (
    "Four panels carry one small network through the steps of the method. "
    "First, each node is a loose cluster of dots, its instances, inside a "
    "soft dashed boundary, with its name on a small grey tab outside. Second, "
    "ties between the clusters are typed: a solid teal generative tie and a "
    "dashed orange extractive tie each carry a small mark for the interviews, "
    "ethnography and events that qualify them, and thin grey ties stay "
    "unqualified. Third, a single shaded band marks assignment by the order: "
    "two generative ties cross it and meet in a new node, one extractive tie "
    "runs along its edge and another stops at it. Fourth, the same chain of "
    "three generative ties appears at two time points while the instances "
    "inside the nodes change, one node fades and a new node joins."
)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": FONT,
    "lines.scale_dashes": False,
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


def lw(px):
    return px * PT


def dashes(on, off):
    return (0, (on * PT, off * PT))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def blob(cx, cy, r, seed, scale=1.0, n=160):
    """A smooth, slightly irregular closed outline around (cx, cy)."""
    rng = random.Random(seed)
    a2, a3 = rng.uniform(0.04, 0.08), rng.uniform(0.02, 0.05)
    p2, p3 = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        rr = r * scale * (1 + a2 * math.cos(2 * t + p2)
                          + a3 * math.cos(3 * t + p3))
        pts.append((cx + rr * math.cos(t), cy + rr * math.sin(t)))
    return pts


def scatter(r, k, seed, dot_r):
    """k instance positions inside a disc, kept apart from each other."""
    rng = random.Random(seed)
    pts, tries = [], 0
    while len(pts) < k and tries < 5000:
        tries += 1
        a, d = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.uniform(0, 1))
        q = (d * math.cos(a), d * math.sin(a))
        if all(math.dist(q, p) > dot_r * 3.4 for p in pts):
            pts.append(q)
    return pts


def bezier(a, b, bend=0.0, through=None, n=80):
    """Quadratic curve from a to b. bend is a sideways offset relative to the
    chord length; through puts the curve's midpoint on a given point."""
    (x1, y1), (x2, y2) = a, b
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    if through is not None:
        cx, cy = 2 * through[0] - mx, 2 * through[1] - my
    else:
        dx, dy = x2 - x1, y2 - y1
        cx, cy = mx - dy * bend, my + dx * bend
    return [((1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2,
             (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2)
            for t in (i / n for i in range(n + 1))]


def trim(pts, a=None, ra=0.0, b=None, rb=0.0):
    """Drop the parts of a polyline that lie inside the end nodes."""
    return [p for p in pts
            if (a is None or math.dist(p, a) >= ra)
            and (b is None or math.dist(p, b) >= rb)]


def at(pts, t):
    """Point and tangent angle at fraction t of a polyline's length."""
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    goal, acc = t * sum(seg), 0.0
    for i, s in enumerate(seg):
        if acc + s >= goal:
            f = (goal - acc) / s if s else 0
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            return (x1 + f * (x2 - x1), y1 + f * (y2 - y1)), \
                math.atan2(y2 - y1, x2 - x1)
        acc += s
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    return pts[-1], math.atan2(y2 - y1, x2 - x1)


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------

class Canvas:
    def __init__(self):
        self.fig = plt.figure(figsize=(PRINT_W_CM / 2.54, PRINT_H_CM / 2.54),
                              dpi=DPI_DESIGN)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, W)
        self.ax.set_ylim(H, 0)
        self.ax.axis("off")
        self.fig.patch.set_facecolor("white")
        self._renderer = self.fig.canvas.get_renderer()
        self.ox, self.oy = 0.0, 0.0
        self.blocks = []

    def panel(self, i, dx=0.0):
        self.ox, self.oy = PANEL_X[i] + dx, DIAG_Y

    def page(self):
        self.ox, self.oy = 0.0, 0.0

    def p(self, x, y):
        return self.ox + x, self.oy + y

    # -- text ----------------------------------------------------------------

    def width(self, s, size, weight="normal", style="normal"):
        t = self.ax.text(0, 0, s, fontsize=size, fontweight=weight,
                         fontstyle=style)
        w = t.get_window_extent(renderer=self._renderer).width
        t.remove()
        return w                      # display px == design px

    def wrap(self, s, width, size, weight="normal", style="normal"):
        lines, cur = [], ""
        for word in s.split():
            trial = f"{cur} {word}".strip()
            if cur and self.width(trial, size, weight, style) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        return lines + [cur]

    def paragraph(self, x, y, s, width, size, name, weight="normal",
                  style="normal", color=INK, leading=LEADING):
        lines = self.wrap(s, width, size, weight, style)
        step = size * leading / PT
        for i, line in enumerate(lines):
            self.ax.text(x, y + i * step, line, fontsize=size,
                         fontweight=weight, fontstyle=style, color=color,
                         va="top")
            assert self.width(line, size, weight, style) <= width + 0.5, line
        self.blocks.append((name, x, y, x + width, y + len(lines) * step))
        return y + len(lines) * step

    def note(self, x, y, s, color=INK_SOFT, style="italic", ha="left",
             va="center", **kw):
        X, Y = self.p(x, y)
        kw.setdefault("zorder", 9)
        return self.ax.text(X, Y, s, fontsize=FS_TEXT, color=color,
                            fontstyle=style, ha=ha, va=va, **kw)

    # -- marks ---------------------------------------------------------------

    def poly(self, pts, color, width_px, dash=None, z=3, alpha=1.0,
             cap="round"):
        xs, ys = zip(*(self.p(*q) for q in pts))
        ln = Line2D(xs, ys, color=color, linewidth=lw(width_px), zorder=z,
                    alpha=alpha, solid_capstyle=cap, dash_capstyle="round",
                    solid_joinstyle="round")
        if dash:
            ln.set_linestyle(dashes(*dash))
        self.ax.add_line(ln)

    def tie(self, pts, kind):
        if kind == "gen":
            self.poly(pts, GEN, 3.5, z=4)
        elif kind == "ext":
            self.poly(pts, EXT, 3.0, dash=(9, 6), z=4)
        else:
            self.poly(pts, UNQ, 1.2, z=3)

    def tendency(self, x, y, r, seed, k=6, dot_r=5.0, faded=False,
                 new=False):
        """A node: instances inside a soft, dashed, slightly irregular edge."""
        alpha = 0.32 if faded else 1.0
        for scale, a in ((1.22, 0.22), (1.12, 0.45)):
            self.ax.add_patch(Polygon(
                [self.p(*q) for q in blob(x, y, r, seed, scale)], closed=True,
                facecolor=HALO, edgecolor="none", alpha=a * alpha, zorder=1.8))
        self.ax.add_patch(Polygon(
            [self.p(*q) for q in blob(x, y, r, seed)], closed=True,
            facecolor=FIELD, edgecolor=INK if new else INK_SOFT,
            linewidth=lw(1.9 if new else 1.5),
            linestyle=dashes(2.2, 3.2) if new else dashes(5, 4),
            alpha=alpha, zorder=2))
        for dx, dy in scatter(r * 0.62, k, seed + 101, dot_r):
            self.ax.add_patch(Circle(self.p(x + dx, y + dy), dot_r,
                                     facecolor=DOT, edgecolor="none",
                                     alpha=alpha, zorder=2.5))

    def tab(self, x, y, text, side):
        """Name tab attached to the outside of a boundary."""
        w, h = self.width(text, FS_TEXT) + 18, 25
        tx = {"right": x, "left": x - w, "above": x - w / 2,
              "below": x - w / 2}[side]
        ty = {"right": y - h / 2, "left": y - h / 2, "above": y - h,
              "below": y}[side]
        self.ax.add_patch(FancyBboxPatch(
            self.p(tx, ty), w, h, boxstyle="round,pad=0,rounding_size=5",
            facecolor=TAB, edgecolor="none", zorder=1.9))
        X, Y = self.p(tx + w / 2, ty + h / 2 + 0.5)
        self.ax.text(X, Y, text, fontsize=FS_TEXT, color="#4B5157",
                     ha="center", va="center", zorder=3)

    def glyph(self, pts, color, t=0.5):
        (cx, cy), ang = at(pts, t)
        self.glyph_at(cx, cy, ang, color)

    def glyph_at(self, cx, cy, ang, color):
        """Three tiny tabs sitting on a tie: interview, ethnography, event."""
        tw, th, gap = 12.0, 10.0, 2.5
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        for k in (-1, 0, 1):
            mx, my = cx + ux * k * (tw + gap), cy + uy * k * (tw + gap)
            corners = [self.p(mx + ux * sx * tw / 2 + nx * sy * th / 2,
                              my + uy * sx * tw / 2 + ny * sy * th / 2)
                       for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            self.ax.add_patch(Polygon(corners, closed=True, facecolor="white",
                                      edgecolor=color, linewidth=lw(1.6),
                                      joinstyle="round", zorder=6))

    def save(self, out: Path, stem: str):
        out.mkdir(parents=True, exist_ok=True)
        title = "Operationalising relations"
        self.fig.savefig(out / f"{stem}.svg",
                         metadata={"Title": title, "Description": ALT_TEXT})
        self.fig.savefig(out / f"{stem}.pdf",
                         metadata={"Title": title, "Subject": ALT_TEXT})
        self.fig.savefig(out / f"{stem}.png", dpi=300,
                         metadata={"Title": title, "Description": ALT_TEXT})


# ---------------------------------------------------------------------------
# Shared network (panel-local px; a diagram panel is 361 x 320)
# ---------------------------------------------------------------------------

R_NODE = 34
NODES = {"P": (62, 70), "Q": (192, 44), "S": (70, 206),
         "R": (318, 118), "T": (178, 286)}
SEEDS = {"P": 3, "Q": 11, "S": 7, "R": 19, "T": 23, "N": 31}
COUNTS = {"P": 6, "Q": 5, "S": 7, "R": 6, "T": 5, "N": 4}
# The node the crossing produces. Its position and the label centre were
# found by search so that straight lines from S and Q to it pass almost
# exactly through the word gaps of the band label.
NEW = (226, 206)
LABEL_CX = 150.0

# Band: centre line y = BAND_Y0 + BAND_K * x, BAND_HALF measured normal to it
BAND_Y0, BAND_K, BAND_HALF = 382.0, -1.25, 24.0
BAND_LABEL = "assignment by the order"


def curve(u, v, bend):
    a, b = NODES[u], NODES[v]
    return trim(bezier(a, b, bend), a, R_NODE + 5, b, R_NODE + 5)


def panel_nodes(c):
    c.panel(0)
    # every name tab hangs from the lower edge of its boundary
    for i, (k, (x, y)) in enumerate(NODES.items(), start=1):
        c.tendency(x, y, R_NODE, SEEDS[k], COUNTS[k])
        bottom = max(q[1] for q in blob(x, y, R_NODE, SEEDS[k])
                     if abs(q[0] - x) < 6)
        c.tab(x, bottom - 1.5, f"Group {i}", "below")


def panel_ties(c):
    c.panel(1)
    for u, v, kind, bend, glyph in [
        ("P", "Q", "gen", -0.10, True),
        ("Q", "R", "ext", -0.10, True),
        ("P", "S", "unq", 0.08, False),
        ("S", "T", "unq", 0.10, False),
    ]:
        pts = curve(u, v, bend)
        c.tie(pts, kind)
        if glyph:
            c.glyph(pts, GEN if kind == "gen" else EXT)
    for k, (x, y) in NODES.items():
        c.tendency(x, y, R_NODE, SEEDS[k], COUNTS[k])


def band_dy():
    return BAND_HALF * math.sqrt(1 + BAND_K ** 2)


def label_gaps(c, mx):
    """Centre of the band label and the positions of its word gaps."""
    L = c.width(BAND_LABEL, FS_TEXT, style="italic")
    space = c.width("a b", FS_TEXT, style="italic") - \
        c.width("ab", FS_TEXT, style="italic")
    k = math.hypot(1, BAND_K)
    ux, uy = 1 / k, BAND_K / k
    my = BAND_Y0 + BAND_K * mx
    sx, sy = mx - ux * L / 2, my - uy * L / 2
    words = BAND_LABEL.split()
    gaps = []
    for i in range(1, len(words)):
        d = c.width(" ".join(words[:i]), FS_TEXT, style="italic") + space / 2
        gaps.append((sx + ux * d, sy + uy * d))
    return (mx, my), gaps


def panel_categories(c):
    c.panel(2)
    # the single band, feathered: a solid core and two faint fringes
    clip = Rectangle(c.p(-6, -6), PANEL_W + 12, DIAG_H + 12,
                     transform=c.ax.transData)
    x0, x1 = -40, PANEL_W + 40
    for grow, a in ((1.5, 0.35), (1.22, 0.6), (1.0, 1.0)):
        dy = band_dy() * grow
        pts = [(x0, BAND_Y0 + BAND_K * x0 - dy), (x1, BAND_Y0 + BAND_K * x1 - dy),
               (x1, BAND_Y0 + BAND_K * x1 + dy), (x0, BAND_Y0 + BAND_K * x0 + dy)]
        patch = Polygon([c.p(*q) for q in pts], closed=True, facecolor=BAND,
                        edgecolor="none", alpha=a, zorder=1)
        c.ax.add_patch(patch)
        patch.set_clip_path(clip)
    (mx, my), gaps = label_gaps(c, LABEL_CX)
    ang = -math.degrees(math.atan(BAND_K))
    c.note(mx, my, BAND_LABEL, ha="center", rotation=ang,
           rotation_mode="anchor", color="#5B636B", zorder=1.5)

    # the two generative ties cross through the label's word gaps, so the
    # label never covers them, and meet in the node they produce
    for u, g in (("S", gaps[0]), ("Q", gaps[2])):
        a = NODES[u]
        c.tie(trim(bezier(a, NEW, through=g), a, R_NODE + 5, NEW, R_NODE + 5),
              "gen")
    # extractive: one runs along the band's edge, one stops at it
    c.tie(curve("S", "Q", 0.0), "ext")
    a, toward = NODES["R"], NODES["P"]
    ux, uy = toward[0] - a[0], toward[1] - a[1]
    t = (BAND_Y0 + band_dy() + BAND_K * a[0] - a[1]) / (uy - BAND_K * ux)
    stop = (a[0] + t * ux, a[1] + t * uy)
    c.tie(trim(bezier(a, stop), a, R_NODE + 5), "ext")
    d = math.hypot(ux, uy)
    nx, ny = -uy / d * 9, ux / d * 9
    c.poly([(stop[0] - nx, stop[1] - ny), (stop[0] + nx, stop[1] + ny)],
           EXT, 3.0, z=4)
    # left unqualified
    c.tie(curve("P", "Q", -0.10), "unq")
    a = NODES["T"]
    c.tie(trim(bezier(a, NEW, -0.10), a, R_NODE + 5, NEW, R_NODE + 5), "unq")

    for k, (x, y) in NODES.items():
        c.tendency(x, y, R_NODE, SEEDS[k], COUNTS[k])
    c.tendency(*NEW, R_NODE, SEEDS["N"], COUNTS["N"], new=True)
    c.note(NEW[0] + R_NODE + 10, NEW[1] + 4, "new")


# Panel 4: the same generative chain at two time points
SLICE_W = 150
SLICE_X = {"T1": 0, "T2": PANEL_W - SLICE_W}
R_MINI = 22
MINI = {"a": (34, 62), "b": (118, 50), "c": (52, 150), "d": (120, 228),
        "e": (30, 276)}
MINI_NEW = (100, 298)
CHAIN = [("a", "b", -0.12), ("b", "c", 0.10), ("c", "d", -0.12)]


def mini_curve(a, b, bend):
    return trim(bezier(a, b, bend), a, R_MINI + 4, b, R_MINI + 4)


def panel_time(c):
    for key, x0 in SLICE_X.items():
        c.panel(3, x0)
        c.note(SLICE_W / 2, 8, key, style="normal", ha="center",
               color=INK_SOFT, fontweight="bold")
        # the instances differ between T1 and T2; the tendency is the same
        shift = 0 if key == "T1" else 500
        for u, v, bend in CHAIN:
            c.tie(mini_curve(MINI[u], MINI[v], bend), "gen")
        if key == "T1":
            c.tie(mini_curve(MINI["c"], MINI["e"], 0.1), "unq")
        else:
            c.tie(mini_curve(MINI["d"], MINI_NEW, -0.1), "unq")
        for k, (x, y) in MINI.items():
            s = ord(k) + shift
            c.tendency(x, y, R_MINI, s, k=4 + s % 2, dot_r=3.6,
                       faded=(key == "T2" and k == "e"))
        if key == "T2":
            c.tendency(*MINI_NEW, R_MINI, 77, k=3, dot_r=3.6, new=True)
            c.note(MINI_NEW[0], MINI_NEW[1] + R_MINI + 16, "new",
                   ha="center")
    # a quiet arrow of time between the slices
    c.panel(3)
    y, x_from, x_to = 8, SLICE_W - 30, PANEL_W - SLICE_W + 30
    c.poly([(x_from, y), (x_to, y)], INK_FAINT, 1.2, z=2)
    c.poly([(x_to - 9, y - 5), (x_to, y), (x_to - 9, y + 5)], INK_FAINT, 1.2,
           z=2)


# ---------------------------------------------------------------------------
# Text frame
# ---------------------------------------------------------------------------

def frame(c):
    c.page()
    step = FS_HEAD * 1.15 / PT
    for i, q in enumerate(HEADERS):
        num_w = c.width(f"{i + 1}", FS_HEAD, "bold") + 14
        lines = c.wrap(q, PANEL_W - num_w, FS_HEAD, "bold")
        top = HEAD_BASE - (len(lines) - 1) * step
        c.ax.text(PANEL_X[i], top, f"{i + 1}", fontsize=FS_HEAD,
                  fontweight="bold", color=GEN, va="baseline")
        for k, line in enumerate(lines):
            c.ax.text(PANEL_X[i] + num_w, top + k * step, line,
                      fontsize=FS_HEAD, fontweight="bold", color=INK,
                      va="baseline")
        c.blocks.append((q, PANEL_X[i], top - FS_HEAD * 0.8 / PT,
                         PANEL_X[i] + PANEL_W, HEAD_BASE + 4))
        c.paragraph(PANEL_X[i] + num_w, LABEL_Y, LABELS[i], PANEL_W - num_w,
                    FS_TEXT, f"label {i + 1}", color=INK_SOFT)

    # what can be claimed
    c.ax.add_line(Line2D([0, W], [CLAIM_Y - 22, CLAIM_Y - 22], color=RULE,
                         linewidth=lw(1.0)))
    c.paragraph(0, CLAIM_Y, CLAIM_HEAD, W, FS_HEAD, "claim head",
                weight="bold", leading=1.1)
    c.paragraph(0, CLAIM_Y + 40, CLAIM, W, FS_CLAIM, "claim", color=INK)

    legend(c)
    c.ax.text(0, FOOT_Y + 50, SCHEMATIC, fontsize=FS_TEXT, fontstyle="italic",
              color=INK_SOFT, va="center")
    c.blocks.append(("schematic", 0, FOOT_Y + 36,
                     c.width(SCHEMATIC, FS_TEXT, style="italic"), FOOT_Y + 64))


def legend(c):
    """One quiet line of keys under the figure."""
    c.page()
    x, y = 0.0, FOOT_Y + 12
    for kind, text in [("gen", "generative"), ("ext", "extractive"),
                       ("unq", "not qualified"),
                       ("glyph", "interview, ethnography, event")]:
        if kind == "glyph":
            c.poly([(x, y), (x + 56, y)], INK_FAINT, 1.2, z=3)
            c.glyph_at(x + 28, y, 0.0, INK_SOFT)
        else:
            c.tie([(x, y), (x + 56, y)], kind)
        c.ax.text(x + 68, y, text, fontsize=FS_TEXT, color=INK_SOFT,
                  va="center")
        x += 68 + c.width(text, FS_TEXT) + 40
    c.blocks.append(("legend", 0, FOOT_Y, x - 40, FOOT_Y + 26))


def check_layout(c):
    b = c.blocks
    for name, x0, y0, x1, y1 in b:
        assert 0 <= x0 and x1 <= W + 0.5 and 0 <= y0 and y1 <= H, \
            f"{name} leaves the canvas: {(x0, y0, x1, y1)}"
        if name.startswith("label"):
            assert y1 < CLAIM_Y - 30, f"{name} reaches the claim rule"
    for i in range(len(b)):
        for j in range(i + 1, len(b)):
            _, ax0, ay0, ax1, ay1 = b[i]
            _, bx0, by0, bx1, by1 = b[j]
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                raise AssertionError(f"overlap: {b[i][0]!r} / {b[j][0]!r}")
    for t in c.ax.texts:
        assert t.get_fontsize() >= FS_TEXT, t.get_text()


def build():
    c = Canvas()
    frame(c)
    panel_nodes(c)
    panel_ties(c)
    panel_categories(c)
    panel_time(c)
    check_layout(c)
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "output")
    ap.add_argument("--stem", default="fig3y_operationalising_relations")
    args = ap.parse_args()
    c = build()
    c.save(args.out, args.stem)
    for ext in ("svg", "pdf", "png"):
        print(args.out / f"{args.stem}.{ext}")


if __name__ == "__main__":
    main()
