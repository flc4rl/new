"""Figure 3.y: Operationalising relations.

Comparative schematic in small multiples (2 rows x 5 columns). Row A shows the
typical operationalisation in social movement network analysis, row B the
approach of this thesis. Columns 1 to 4 are small diagrams, column 5 is text.

The figure is drawn on a 1600 x 950 design canvas (1 design px = 0.1 mm at
the 16 cm print width) and written as SVG, PDF and 300 dpi PNG. The script
checks that no text block runs into the next one and that no text is smaller
than 8 pt at print size.

    python make_figure.py            # writes into ./output
    python make_figure.py --out DIR
"""

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patheffects
from matplotlib.lines import Line2D
from matplotlib.patches import (Circle, Ellipse, FancyBboxPatch, Polygon,
                                Rectangle)

# ---------------------------------------------------------------------------
# Canvas and units
# ---------------------------------------------------------------------------

W, H = 1600, 950                      # design canvas in px
PRINT_W_CM = 16.0
PRINT_H_CM = PRINT_W_CM * H / W       # 9.5 cm
DPI_DESIGN = W / (PRINT_W_CM / 2.54)  # 254 design px per inch
PT = 72.0 / DPI_DESIGN                # one design px in points (0.2835 pt)

FONT = ["Arial", "Liberation Sans", "DejaVu Sans"]
FS_TEXT = 8.0                         # minimum text size at print size
FS_HEAD = 9.5                         # column headers, bold
FS_ROW = 8.5                          # row labels, bold
FS_CLAIM = 9.0                        # column 5 text
LEADING = 1.18

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

# Row A: greyscale only
A_FILL_LIGHT = "#D9DDE1"
A_FILL_DARK = "#9AA0A6"
A_TIE = "#7A8088"
A_OUTLINE = "#000000"

# Row B: palette of the relevance/qualification/significance figure
GEN = "#1F6F78"                       # generative: solid, 3.5 px
EXT = "#C4622D"                       # extractive: dashed 9/6, 3 px
UNQ = "#A3A8AE"                       # not qualified: 1.2 px
BAND = "#E9EDF1"                      # assignment by the order

INK = "#1E1E1E"
INK_SOFT = "#454A50"
INK_MUTED = "#6B7178"
RULE = "#B8BDC3"
B_EDGE = A_OUTLINE

# ---------------------------------------------------------------------------
# Layout (design px)
# ---------------------------------------------------------------------------

MARGIN = 250                          # row label margin, 2.5 cm
COL5_W = 196
GUTTER = 20
CELL_W = (W - MARGIN - COL5_W - 4 * GUTTER) // 4     # 268
COL_X = [MARGIN + i * (CELL_W + GUTTER) for i in range(4)] + [W - COL5_W]
COL_W = [CELL_W] * 4 + [COL5_W]

HEAD_BASE = 112                       # baseline of the last header line
ROW_Y = {"A": 126, "B": 520}          # top of each diagram area
DIAG_H = 160
LABEL_GAP = 10
RULE_Y = 508
FOOT_Y = 872

COLUMNS = [
    "What is a node?",
    "What is a tie?",
    "Where do categories sit?",
    "What must persist over time?",
    "What can be claimed?",
]
ROWS = {
    "A": "Typical operationalisation in social movement network analysis",
    "B": "This thesis",
}
CELL_LABELS = {
    ("A", 0): "pre-given unit with attributes",
    ("B", 0): "structured tendency identified through instances; "
              "the name serves only as an index",
    ("A", 1): "tie weighted by frequency of joint participation; "
              "quality established alongside the network",
    ("B", 1): "tie qualified by the relational function it carries; "
              "a trace of the relation, not a proxy",
    ("A", 2): "categories as node attributes; their crossing can be counted, "
              "their production cannot be shown",
    ("B", 2): "categories as operations that relations reproduce or refuse",
    ("A", 3): "units held constant; change registers only in ties",
    ("B", 3): "the function must persist; units may change",
}
CLAIMS = {
    "A": "How often formations co-occur, and whether ties cross categories",
    "B": "What relations do, and whether this holds as a tendency across "
         "layers, sites and time",
}
NOTE = ("Row A shows how nodes and ties are typically operationalised. "
        "Many studies interpret networks in their context "
        "(Diani, 2011; Mische, 2011).")
SCHEMATIC = "Schematic illustration, not a plot of the data."

ALT_TEXT = (
    "Two rows of small diagrams compare how networks are operationalised. "
    "The top row shows fixed nodes with category fills, ties of varying "
    "thickness by frequency, and identical nodes at two time points. The "
    "bottom row shows nodes as clusters of instances, ties typed as generative "
    "or extractive, a band marking assignment by the order that some ties "
    "cross and others reproduce, and a recurring pattern of generative ties "
    "across changing nodes."
)

# Break points for words wider than the row label margin
HYPHENATE = {"operationalisation": ("operational-", "isation")}

# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": FONT,
    "lines.scale_dashes": False,
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
})


def lw(px):
    """Line width in points for a stroke given in design px."""
    return px * PT


def dashes(on, off):
    return (0, (on * PT, off * PT))


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
        self.ox, self.oy = 0, 0       # current cell origin
        self.blocks = []              # (name, x0, y0, x1, y1) text blocks

    # -- coordinates -------------------------------------------------------

    def cell(self, row, col):
        self.ox, self.oy = COL_X[col], ROW_Y[row]

    def page(self):
        self.ox, self.oy = 0, 0

    def p(self, x, y):
        return self.ox + x, self.oy + y

    # -- text ----------------------------------------------------------------

    def text_width(self, s, size, weight="normal", style="normal"):
        t = self.ax.text(0, 0, s, fontsize=size, fontweight=weight,
                         fontstyle=style)
        w = t.get_window_extent(renderer=self._renderer).width
        t.remove()
        return w                      # display px == design px

    def wrap(self, s, width, size, weight="normal", style="normal"):
        fits = lambda t: self.text_width(t, size, weight, style) <= width
        # (token, glue before it); a hyphenation break carries no glue
        tokens = []
        for word in s.split():
            parts = HYPHENATE.get(word.lower()) if not fits(word) else None
            if parts:
                tokens += [(parts[0], " "), (parts[1], "")]
            else:
                tokens.append((word, " "))
        lines, cur = [], ""
        for tok, glue in tokens:
            joined = (cur[:-1] if not glue else cur + glue) + tok
            if cur and not fits(joined):
                lines.append(cur)
                cur = tok
            else:
                cur = joined if cur else tok
        lines.append(cur)
        return lines

    def paragraph(self, x, y, s, width, size, weight="normal", style="normal",
                  color=INK, ha="left", leading=LEADING, name=None):
        """Wrapped text in page coordinates; returns the y below the block."""
        lines = self.wrap(s, width, size, weight, style)
        step = size * leading / PT
        ax_x = {"left": x, "center": x + width / 2, "right": x + width}[ha]
        for i, line in enumerate(lines):
            self.ax.text(ax_x, y + i * step, line, fontsize=size,
                         fontweight=weight, fontstyle=style, color=color,
                         ha=ha, va="top")
            assert self.text_width(line, size, weight, style) <= width + 0.5, \
                f"line too wide: {line!r}"
        bottom = y + len(lines) * step
        self.blocks.append((name or s[:30], x, y, x + width, bottom))
        return bottom

    def label(self, x, y, s, size=FS_TEXT, color=INK, ha="center",
              va="center", **kw):
        X, Y = self.p(x, y)
        kw.setdefault("zorder", 8)
        return self.ax.text(X, Y, s, fontsize=size, color=color, ha=ha,
                            va=va, **kw)

    # -- marks ---------------------------------------------------------------

    def line(self, pts, color, width_px, dash=None, z=2, alpha=1.0,
             cap="round"):
        xs, ys = zip(*(self.p(*q) for q in pts))
        ln = Line2D(xs, ys, color=color, linewidth=lw(width_px), zorder=z,
                    alpha=alpha, solid_capstyle=cap, dash_capstyle="butt",
                    solid_joinstyle="miter")
        if dash:
            ln.set_linestyle(dashes(*dash))
        self.ax.add_line(ln)
        return ln

    def tie(self, a, b, kind, width_px=None):
        """kind: gen | ext | unq for row B, 'a' for the grey ties of row A."""
        if kind == "gen":
            return self.line([a, b], GEN, 3.5, z=3)
        if kind == "ext":
            return self.line([a, b], EXT, 3.0, dash=(9, 6), z=3)
        if kind == "unq":
            return self.line([a, b], UNQ, 1.2, z=2)
        return self.line([a, b], A_TIE, width_px or 2.2, z=2)

    def node_a(self, x, y, fill, r=14):
        self.ax.add_patch(Circle(self.p(x, y), r, facecolor=fill,
                                 edgecolor=A_OUTLINE, linewidth=lw(2.2),
                                 zorder=5))

    def node_b(self, x, y, r=14, new=False, faded=False):
        c = Circle(self.p(x, y), r, facecolor="white", edgecolor=B_EDGE,
                   linewidth=lw(2.2), zorder=5)
        if new:
            c.set_linestyle(dashes(5, 3.5))
        if faded:
            c.set_edgecolor("#C9CDD2")
            c.set_linestyle(dashes(2, 3))
        self.ax.add_patch(c)

    def glyph(self, a, b, color, t=0.5):
        """Three tiny tabs (interview, ethnography, event) sitting on a tie."""
        (x1, y1), (x2, y2) = a, b
        self.glyph_at(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t,
                      math.atan2(y2 - y1, x2 - x1), color)

    def glyph_at(self, cx, cy, ang, color):
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
                                      joinstyle="miter", zorder=7))

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
# Shared geometry (cell-local design px; a diagram cell is 268 x 160)
# ---------------------------------------------------------------------------

# Column 1: three nodes in a zigzag, shared by A1 and B1
C1 = [(70, 32), (198, 80), (70, 128)]
C1_RX, C1_RY = 68, 30

# Column 2: shared by A2 and B2; lower strip holds the A2 fieldwork box
C2 = {"n1": (28, 28), "n2": (240, 28), "n3": (134, 94)}

# Column 3: six nodes, identical in A3 and B3; X is the new node in B3.
# The two generative ties in B3 cross the band through the word gaps of its
# label ("assignment | by | the order"), so the label never covers a tie.
C3 = {
    "n1": (141, 21), "n2": (22, 90), "n3": (80, 47),
    "n4": (252, 72), "n5": (140, 150), "n6": (250, 148),
}
C3_X = (200, 122)
# Band centre line y = BAND_Y0 + BAND_K * x; BAND_HALF is normal half-width
BAND_Y0, BAND_K, BAND_HALF = 160.0, -0.62, 25.0

# Column 4: two mini networks; T1 layout shared by A4 and B4
T_OFF = {"T1": 0, "T2": 146}
C4 = {"a": (16, 44), "b": (106, 40), "c": (60, 90), "d": (16, 146),
      "e": (106, 144)}
C4_NEW = ("f", (61, 146))


def band_dy():
    """Vertical distance from the band's centre line to either edge."""
    return BAND_HALF * math.sqrt(1 + BAND_K ** 2)


def band_edge_hit(p, q, side):
    """Where segment p->q meets the upper (side=-1) or lower (+1) band edge."""
    (x1, y1), (x2, y2) = p, q
    t = (BAND_Y0 + side * band_dy() + BAND_K * x1 - y1) / \
        ((y2 - y1) - BAND_K * (x2 - x1))
    return x1 + t * (x2 - x1), y1 + t * (y2 - y1)


def midpoint(a, b):
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

def cell_a1(c):
    c.cell("A", 0)
    fills = [A_FILL_LIGHT, A_FILL_DARK, A_FILL_LIGHT]
    for i, ((x, y), f) in enumerate(zip(C1, fills), start=1):
        c.ax.add_patch(Ellipse(c.p(x, y), 2 * C1_RX, 2 * C1_RY, facecolor=f,
                               edgecolor=A_OUTLINE, linewidth=lw(2.2),
                               zorder=4))
        tw, th = 114, 26
        c.ax.add_patch(FancyBboxPatch(c.p(x - tw / 2, y - th / 2), tw, th,
                                      boxstyle="round,pad=0,rounding_size=5",
                                      facecolor="white", edgecolor=A_OUTLINE,
                                      linewidth=lw(1.2), zorder=5))
        c.label(x, y + 0.5, f"Group {i}")


def cell_b1(c):
    c.cell("B", 0)
    # instances: loose, irregular clusters of 5 to 7 dots
    clusters = [
        [(-40, -6), (-22, 12), (-4, -12), (14, 8), (34, -8), (44, 10)],
        [(-38, 8), (-18, -12), (2, 10), (22, -10), (40, 6)],
        [(-44, 4), (-28, -12), (-10, 10), (8, -6), (24, 13), (38, -10),
         (-2, -16)],
    ]
    # tabs sit on the outer side of each boundary that has room
    tab_side = ["right", "left", "right"]
    rx, ry = C1_RX - 3, C1_RY - 2
    for i, ((x, y), dots, side) in enumerate(zip(C1, clusters, tab_side), 1):
        for grow, a in ((12, 0.25), (6, 0.45)):   # soft edge
            c.ax.add_patch(Ellipse(c.p(x, y), 2 * rx + grow, 2 * ry + grow,
                                   facecolor="#E6EAEE", edgecolor="none",
                                   alpha=a, zorder=3))
        c.ax.add_patch(Ellipse(c.p(x, y), 2 * rx, 2 * ry, facecolor="#F4F6F8",
                               edgecolor=INK_MUTED, linewidth=lw(1.6),
                               linestyle=dashes(5, 4), zorder=4))
        for dx, dy in dots:
            c.ax.add_patch(Circle(c.p(x + dx, y + dy), 5.5,
                                  facecolor=INK_SOFT, edgecolor="none",
                                  zorder=5))
        tw, th = 112, 24
        tx = x + rx - 2 if side == "right" else x - rx - tw + 2
        c.ax.add_patch(FancyBboxPatch(c.p(tx, y - th / 2), tw, th,
                                      boxstyle="round,pad=0,rounding_size=4",
                                      facecolor="#D3D7DC", edgecolor="none",
                                      zorder=3.5))
        c.label(tx + tw / 2, y + 0.5, f"Group {i}", color="#4F555B")


def cell_a2(c):
    c.cell("A", 1)
    n = C2
    for (u, v), w in {("n1", "n3"): 1, ("n2", "n3"): 3,
                      ("n1", "n2"): 6}.items():
        c.tie(n[u], n[v], "a", width_px=w)
    # categories are the subject of column 3; here all nodes share one fill
    for x, y in n.values():
        c.node_a(x, y, A_FILL_LIGHT)
    c.label(134, 13, "6×", color=INK_SOFT)
    c.label(66, 72, "1×", color=INK_SOFT)
    c.label(204, 72, "3×", color=INK_SOFT)
    # quality established alongside the network: a separate box, bracketed
    # to the network as a whole and to no single tie
    by = 118
    c.line([(6, by - 8), (6, by), (262, by), (262, by - 8)], INK_MUTED, 1.4,
           dash=(4, 3), cap="butt")
    c.line([(134, by), (134, by + 8)], INK_MUTED, 1.4, cap="butt")
    bh = 30
    c.ax.add_patch(FancyBboxPatch(c.p(0, by + 8), CELL_W, bh,
                                  boxstyle="round,pad=0,rounding_size=4",
                                  facecolor="white", edgecolor=INK_SOFT,
                                  linewidth=lw(1.2), zorder=4))
    c.label(CELL_W / 2, by + 8 + bh / 2 + 0.5, "interviews, fieldwork")


def cell_b2(c):
    c.cell("B", 1)
    n = C2
    c.tie(n["n1"], n["n2"], "gen")
    c.tie(n["n2"], n["n3"], "ext")
    c.tie(n["n1"], n["n3"], "unq")
    c.glyph(n["n1"], n["n2"], GEN)
    c.glyph(n["n2"], n["n3"], EXT)
    for x, y in n.values():
        c.node_b(x, y)


def cell_a3(c):
    c.cell("A", 2)
    n = C3
    fills = {k: A_FILL_LIGHT if k in ("n1", "n2", "n3") else A_FILL_DARK
             for k in n}
    within = [("n2", "n3"), ("n1", "n3"), ("n4", "n6")]
    crossing = [("n1", "n4"), ("n3", "n5")]
    for u, v in within + crossing:
        c.tie(n[u], n[v], "a")
    for k, (x, y) in n.items():
        c.node_a(x, y, fills[k])
    # bracket over the two ties that connect different fills
    m1, m2 = midpoint(n["n1"], n["n4"]), midpoint(n["n3"], n["n5"])
    dx, dy = m2[0] - m1[0], m2[1] - m1[1]
    d = math.hypot(dx, dy)
    nx, ny = dy / d, -dx / d                  # normal, pointing down and right
    off = 10
    a1 = (m1[0] + nx * off, m1[1] + ny * off)
    a2 = (m2[0] + nx * off, m2[1] + ny * off)
    c.line([m1, a1, a2, m2], INK, 1.4, cap="butt", z=6)
    ang = -math.degrees(math.atan2(-dy, -dx))
    mid = midpoint(a1, a2)
    back = -10                                # slide away from n4
    c.label(mid[0] + nx * 4 - dx / d * back, mid[1] + ny * 4 - dy / d * back,
            "crossing counted",
            rotation=ang, rotation_mode="anchor", va="top",
            path_effects=[patheffects.withStroke(linewidth=2.5,
                                                 foreground="white")])


def cell_b3(c):
    c.cell("B", 2)
    n = C3
    # the single band: assignment by the order
    dy = band_dy()
    x0, x1 = 0, CELL_W
    band = Polygon(
        [c.p(x0, BAND_Y0 + BAND_K * x0 - dy), c.p(x1, BAND_Y0 + BAND_K * x1 - dy),
         c.p(x1, BAND_Y0 + BAND_K * x1 + dy), c.p(x0, BAND_Y0 + BAND_K * x0 + dy)],
        closed=True, facecolor=BAND, edgecolor="none", zorder=1)
    c.ax.add_patch(band)
    clip = Rectangle(c.p(0, -6), CELL_W, DIAG_H + 12, transform=c.ax.transData)
    band.set_clip_path(clip)
    ang = -math.degrees(math.atan(BAND_K))
    lx = CELL_W / 2
    c.label(lx, BAND_Y0 + BAND_K * lx, "assignment by the order",
            rotation=ang, rotation_mode="anchor", color="#50575E",
            fontstyle="italic", zorder=1.5)
    # generative ties cross the band and continue to a new node
    c.tie(n["n1"], C3_X, "gen")
    c.tie(n["n3"], C3_X, "gen")
    # extractive ties: one runs along the band's edge, one stops at it
    c.tie(n["n2"], n["n3"], "ext")
    stop = band_edge_hit(n["n5"], n["n2"], +1)
    c.tie(n["n5"], stop, "ext")
    ux, uy = stop[0] - n["n5"][0], stop[1] - n["n5"][1]
    d = math.hypot(ux, uy)
    bx, by = -uy / d * 8, ux / d * 8
    c.line([(stop[0] - bx, stop[1] - by), (stop[0] + bx, stop[1] + by)],
           EXT, 3.0, cap="butt", z=3)
    # one tie stays unqualified
    c.tie(n["n4"], n["n6"], "unq")
    for x, y in n.values():
        c.node_b(x, y)
    c.node_b(*C3_X, new=True)


def _t_frame(c):
    for key, off in T_OFF.items():
        c.label(off + 61, 10, key, fontweight="bold", color=INK_SOFT)
    c.line([(134, 26), (134, 160)], RULE, 1.2, dash=(3, 3), cap="butt")


def cell_a4(c):
    c.cell("A", 3)
    _t_frame(c)
    ties = {"T1": [("a", "b"), ("b", "c"), ("c", "e"), ("a", "d")],
            "T2": [("a", "c"), ("c", "d"), ("d", "e"), ("b", "e")]}
    for key, off in T_OFF.items():
        pos = {k: (x + off, y) for k, (x, y) in C4.items()}
        for u, v in ties[key]:
            c.tie(pos[u], pos[v], "a")
        for k, (x, y) in pos.items():
            c.node_a(x, y, A_FILL_LIGHT)
            c.label(x, y, k)


def cell_b4(c):
    c.cell("B", 3)
    _t_frame(c)
    chain = [("a", "b"), ("b", "c"), ("c", "e")]
    for key, off in T_OFF.items():
        pos = {k: (x + off, y) for k, (x, y) in C4.items()}
        new = (C4_NEW[1][0] + off, C4_NEW[1][1])
        for u, v in chain:
            c.tie(pos[u], pos[v], "gen")
        if key == "T1":
            c.tie(pos["a"], pos["d"], "unq")
        else:
            c.tie(pos["c"], new, "unq")
        for k, (x, y) in pos.items():
            faded = key == "T2" and k == "d"
            c.node_b(x, y, faded=faded)
            c.label(x, y, k, color="#C3C7CC" if faded else INK)
        if key == "T2":
            c.node_b(*new, new=True)
            c.label(*new, C4_NEW[0])


# ---------------------------------------------------------------------------
# Frame: headers, row labels, cell labels, legend, footer
# ---------------------------------------------------------------------------

def frame(c):
    c.page()
    # column headers, bottom-aligned on a common baseline
    step = FS_HEAD * 1.12 / PT
    for i, q in enumerate(COLUMNS):
        lines = c.wrap(q, COL_W[i], FS_HEAD, "bold")
        for k, line in enumerate(lines):
            y = HEAD_BASE - (len(lines) - 1 - k) * step
            c.ax.text(COL_X[i], y, line, fontsize=FS_HEAD, fontweight="bold",
                      color=INK, ha="left", va="baseline")
        c.blocks.append((q, COL_X[i],
                         HEAD_BASE - (len(lines) - 1) * step - FS_HEAD * 0.8 / PT,
                         COL_X[i] + COL_W[i], HEAD_BASE + 3))

    # row labels in the left margin
    for key, text in ROWS.items():
        c.paragraph(0, ROW_Y[key], text, MARGIN - 30, FS_ROW, weight="bold",
                    name=f"row {key}")

    c.ax.add_line(Line2D([0, W], [RULE_Y, RULE_Y], color=RULE,
                         linewidth=lw(1.2)))

    # labels under each diagram
    for (row, col), text in CELL_LABELS.items():
        c.paragraph(COL_X[col], ROW_Y[row] + DIAG_H + LABEL_GAP, text,
                    CELL_W, FS_TEXT, color=INK_SOFT, name=f"{row}{col + 1}")

    # column 5: what can be claimed
    for row, text in CLAIMS.items():
        c.paragraph(COL_X[4], ROW_Y[row], text, COL5_W, FS_CLAIM, color=INK,
                    leading=1.25, name=f"{row}5")

    legend(c)

    # footer: scope note, then the schematic status right-aligned
    lines = c.wrap(NOTE, W, FS_TEXT)
    step = FS_TEXT * LEADING / PT
    for k, line in enumerate(lines):
        c.ax.text(0, FOOT_Y + k * step, line, fontsize=FS_TEXT,
                  color=INK_SOFT, va="top")
    last = FOOT_Y + (len(lines) - 1) * step
    c.ax.text(W, last, SCHEMATIC, fontsize=FS_TEXT, fontstyle="italic",
              color=INK_SOFT, ha="right", va="top")
    assert c.text_width(lines[-1], FS_TEXT) + 40 + \
        c.text_width(SCHEMATIC, FS_TEXT, style="italic") < W
    c.blocks.append(("footer", 0, FOOT_Y, W, last + step))


def legend(c):
    """Key to the row B encodings, in the margin under 'This thesis'."""
    c.page()
    x0, tx = 0, 62
    y = ROW_Y["B"] + 50
    step = 34
    for kind, text in (("gen", "generative"), ("ext", "extractive"),
                       ("unq", "not qualified")):
        c.tie((x0 + 2, y), (x0 + 50, y), kind)
        c.ax.text(tx, y, text, fontsize=FS_TEXT, color=INK, va="center")
        y += step
    c.line([(x0 + 2, y), (x0 + 50, y)], INK_MUTED, 1.2, z=6)
    c.glyph_at(x0 + 26, y, 0.0, INK_MUTED)
    y = c.paragraph(tx, y - 14, "sources that qualify the tie: interview, "
                    "ethnography, event", MARGIN - tx - 8, FS_TEXT,
                    color=INK, name="legend glyph")
    y += 22
    c.node_b(x0 + 26, y, new=True)
    c.ax.text(tx, y, "new node", fontsize=FS_TEXT, color=INK, va="center")
    c.blocks.append(("legend end", 0, y - 17, MARGIN - 8, y + 17))


def check_layout(c):
    """No text block may overlap another or run past the canvas."""
    b = c.blocks
    for name, x0, y0, x1, y1 in b:
        assert 0 <= x0 and x1 <= W and 0 <= y0 and y1 <= H, \
            f"{name} leaves the canvas: {(x0, y0, x1, y1)}"
    for i in range(len(b)):
        for j in range(i + 1, len(b)):
            _, ax0, ay0, ax1, ay1 = b[i]
            _, bx0, by0, bx1, by1 = b[j]
            if ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1:
                raise AssertionError(f"overlap: {b[i][0]!r} / {b[j][0]!r}")
    # labels must not reach the next row
    for name, x0, y0, x1, y1 in b:
        if name in ("A1", "A2", "A3", "A4", "A5"):
            assert y1 < RULE_Y - 4, f"{name} reaches the row rule"
        if name.startswith(("B", "row B", "legend")):
            assert y1 < FOOT_Y - 4, f"{name} reaches the footer"


def build():
    c = Canvas()
    frame(c)
    for fn in (cell_a1, cell_b1, cell_a2, cell_b2, cell_a3, cell_b3,
               cell_a4, cell_b4):
        fn(c)
    check_layout(c)
    for t in c.ax.texts:
        assert t.get_fontsize() >= FS_TEXT, t.get_text()
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
