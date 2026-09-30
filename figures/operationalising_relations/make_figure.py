"""Figure 3.y: Operationalising relations.

A generated field rather than a diagram. Nothing in it is drawn as a finished
shape: tendencies are wherever instances cohere, and their edges are density
contours, so they stay porous and can form or dissolve. Relations are bundles
of individual traces that fray between the tendencies they connect. The
assignment by the order is a current running through the whole field. Time
runs left to right through three moments (t1, t2, t3): the instances drift,
one tendency dissolves and new ones form where generative relations cross the
order, while the generative pattern itself recurs.

Encodings carried over from the relevance, qualification and significance
figure: generative #1F6F78 (solid), extractive #C4622D (dashed), not
qualified #A3A8AE (thin), band #E9EDF1. Function is always carried by both
colour and dash, so the figure survives greyscale printing.

The field is drawn on a 1600 x 900 design canvas (1 px = 0.1 mm at 16 cm
wide) and written as SVG, PDF and 300 dpi PNG. It is seeded, so every run
gives the same image.

    python make_figure.py            # writes into ./output
    python make_figure.py --out DIR
"""

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, to_rgba
from matplotlib.lines import Line2D
from scipy.ndimage import gaussian_filter

# ---------------------------------------------------------------------------
# Canvas, units, palette
# ---------------------------------------------------------------------------

W, H = 1600, 900
PRINT_W_CM = 16.0
PRINT_H_CM = PRINT_W_CM * H / W       # 9 cm
DPI_DESIGN = W / (PRINT_W_CM / 2.54)
PT = 72.0 / DPI_DESIGN                # one design px in points

FONT = ["Arial", "Liberation Sans", "DejaVu Sans"]
FS = 8.0                              # minimum size at print size

GEN = "#1F6F78"
EXT = "#C4622D"
UNQ = "#A3A8AE"
BAND = "#E9EDF1"
BAND_LINE = "#A7B2BC"
INK = "#2A2F34"
INK_SOFT = "#586068"
DOT = "#353B41"
CONTOUR = "#6E767E"

FIELD_TOP, FIELD_BOTTOM = 70, 770     # vertical extent of the field
PHASES = {"t1": 0, "t2": 540, "t3": 1080}
PHASE_W = 520

ALT_TEXT = (
    "A continuous field read from left to right across three moments, t1, "
    "t2 and t3. Thousands of small dots are instances. Where they cohere, "
    "faint dashed contour lines emerge around them as tendencies, whose edges "
    "stay open. Between tendencies run bundles of fine, fraying traces: teal "
    "solid ones for generative relations and orange dashed ones for "
    "extractive relations, each dotted with small marks for interview, "
    "ethnography and event evidence, and a few faint grey ones that are not "
    "qualified. A pale current, the assignment by the order, winds through "
    "the whole field. Teal traces cross it and new tendencies gather on the "
    "far side; orange traces are drawn into it and follow it, or thin out "
    "at its edge. Across the three moments the instances drift, one tendency "
    "dissolves and a new one forms, while the same chain of generative "
    "relations recurs."
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


# ---------------------------------------------------------------------------
# Smooth noise
# ---------------------------------------------------------------------------

class Noise:
    """Sum of random plane waves: cheap, smooth, deterministic."""

    def __init__(self, seed, waves=6, scale=160.0):
        r = np.random.default_rng(seed)
        ang = r.uniform(0, 2 * np.pi, waves)
        k = 2 * np.pi / (scale * r.uniform(0.6, 1.6, waves))
        self.kx, self.ky = k * np.cos(ang), k * np.sin(ang)
        self.ph = r.uniform(0, 2 * np.pi, waves)
        self.amp = r.uniform(0.5, 1.0, waves) / waves ** 0.5

    def __call__(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        return sum(a * np.sin(kx * x + ky * y + p)
                   for a, kx, ky, p in zip(self.amp, self.kx, self.ky,
                                           self.ph))


def wave1d(t, rng, terms=3, lo=1.0, hi=3.5):
    """Smooth random function on [0, 1]."""
    out = np.zeros_like(t)
    for _ in range(terms):
        out += rng.uniform(0.4, 1.0) * np.sin(
            2 * np.pi * rng.uniform(lo, hi) * t + rng.uniform(0, 2 * np.pi))
    return out / terms ** 0.5


# ---------------------------------------------------------------------------
# The order: a current through the field
# ---------------------------------------------------------------------------

def band_y(x):
    x = np.asarray(x, float)
    return (478 + 36 * np.sin(2 * np.pi * x / 900 + 0.5)
            + 13 * np.sin(2 * np.pi * x / 330 + 1.7))


def band_slope(x):
    return (band_y(x + 1) - band_y(x - 1)) / 2


BAND_HALF = 34


# ---------------------------------------------------------------------------
# Instances and tendencies
# ---------------------------------------------------------------------------

class Field:
    def __init__(self):
        self.points = []              # (N, 2) arrays that feed the density
        self.dots = []                # (xy, size, alpha)
        self.trails = []              # (xy_from, xy_to, alpha)

    def tendency(self, c, n, sx, sy, rot, seed, state="stable",
                 drift=(0.0, 0.0), toward=None):
        """Sample the instances of one tendency.

        state: stable | forming | dissolving | faint
        """
        r = np.random.default_rng(seed)
        pts = r.normal(size=(n, 2)) * (sx, sy)
        ca, sa = math.cos(rot), math.sin(rot)
        pts = pts @ np.array([[ca, sa], [-sa, ca]])
        warp = Noise(seed + 7, scale=70)
        pts[:, 0] += 10 * warp(pts[:, 0], pts[:, 1])
        pts[:, 1] += 10 * warp(pts[:, 1] + 40, pts[:, 0])
        pts += c
        prev = None
        if state == "dissolving":
            # instances disperse; trails point back to where they were
            out = pts - c
            prev = pts.copy()
            pts = c + out * r.uniform(1.3, 2.0, (n, 1)) + drift
        elif state == "forming":
            # fewer instances arrive, from the direction of the relation
            d = np.array(toward if toward is not None else (0, -1), float)
            d /= np.linalg.norm(d)
            prev = pts + d * r.uniform(10, 28, (n, 1)) \
                + r.normal(scale=5, size=(n, 2))
        elif drift != (0.0, 0.0):
            prev = pts - np.array(drift) * r.uniform(0.6, 1.2, (n, 1))
        size = r.uniform(1.6, 3.1, n)
        alpha = {"stable": 0.85, "forming": 0.8, "dissolving": 0.45,
                 "faint": 0.3}[state]
        self.dots.append((pts, size, alpha))
        if prev is not None:
            k = n // 2 if state != "stable" else n // 4
            idx = r.choice(n, k, replace=False)
            self.trails.append((prev[idx], pts[idx], 0.22))
        weight = {"stable": 1.0, "forming": 1.0, "dissolving": 0.35,
                  "faint": 0.4}[state]
        self.points.append((pts, weight))
        return pts

    def dust(self, n, seed):
        r = np.random.default_rng(seed)
        pts = np.column_stack([r.uniform(0, W, n),
                               r.uniform(FIELD_TOP, FIELD_BOTTOM, n)])
        self.dots.append((pts, r.uniform(0.9, 1.8, n), 0.28))

    def density(self, cell=4, sigma=5.0):
        nx, ny = W // cell, (FIELD_BOTTOM - FIELD_TOP) // cell
        grid = np.zeros((ny, nx))
        for pts, wgt in self.points:
            h, _, _ = np.histogram2d(
                pts[:, 1], pts[:, 0], bins=(ny, nx),
                range=((FIELD_TOP, FIELD_BOTTOM), (0, W)))
            grid += wgt * h
        grid = gaussian_filter(grid, sigma)
        xs = np.linspace(0, W, nx)
        ys = np.linspace(FIELD_TOP, FIELD_BOTTOM, ny)
        return xs, ys, grid


# ---------------------------------------------------------------------------
# Relations: bundles of traces
# ---------------------------------------------------------------------------

def quad(a, b, bend, t):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    ctrl = (a + b) / 2 + np.array([-d[1], d[0]]) * bend
    t = t[:, None]
    return (1 - t) ** 2 * a + 2 * (1 - t) * t * ctrl + t ** 2 * b


def normals(curve):
    d = np.gradient(curve, axis=0)
    d /= np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
    return np.column_stack([-d[:, 1], d[:, 0]])


def bundle(a, b, n, seed, bend=0.0, spread=16.0, fray=10.0, reach=18.0,
           m=90):
    """n traces from inside tendency a to inside tendency b. They pinch
    where they leave and enter, and fray in between."""
    r = np.random.default_rng(seed)
    t = np.linspace(0, 1, m)
    out = []
    for _ in range(n):
        a_j = np.asarray(a) + r.normal(scale=reach, size=2)
        b_j = np.asarray(b) + r.normal(scale=reach, size=2)
        base = quad(a_j, b_j, bend + r.normal(scale=0.03), t)
        env = np.sin(np.pi * t) ** 0.9
        off = env * (r.normal(scale=spread * 0.5)
                     + fray * wave1d(t, r, 3, 1.0, 3.0))
        out.append(base + normals(base) * off[:, None])
    return out


def add_traces(ax, traces, color, width_px, alpha=(0.35, 0.75), dash=None,
               z=4, fade=None, rng=None):
    """Draw traces; fade=(t0, t1) thins them out along their length."""
    rng = rng or np.random.default_rng(0)
    segs, cols, widths = [], [], []
    for tr in traces:
        a0 = rng.uniform(*alpha)
        w = width_px * rng.uniform(0.6, 1.25)
        if fade is None:
            segs.append(tr)
            cols.append(to_rgba(color, a0))
            widths.append(lw(w))
        else:
            n = len(tr)
            for i in range(0, n - 1, 3):
                f = i / (n - 1)
                k = 1.0 if f < fade[0] else max(
                    0.0, 1 - (f - fade[0]) / (fade[1] - fade[0]))
                if k <= 0.02:
                    continue
                segs.append(tr[i:i + 4])
                cols.append(to_rgba(color, a0 * k))
                widths.append(lw(w * (0.5 + 0.5 * k)))
    lc = LineCollection(segs, colors=cols, linewidths=widths, zorder=z,
                        capstyle="round", joinstyle="round")
    if dash:
        lc.set_linestyle((0, (dash[0] * PT, dash[1] * PT)))
    ax.add_collection(lc)


def evidence(ax, traces, color, seed, k=6, z=6):
    """Small marks along a bundle: interview, ethnography, event."""
    r = np.random.default_rng(seed)
    core = np.mean(np.stack(traces), axis=0)
    ts = np.sort(r.uniform(0.22, 0.78, k))
    for i, t in enumerate(ts):
        p = core[int(t * (len(core) - 1))] + r.normal(scale=5, size=2)
        mark = "osv"[i % 3]
        ax.scatter([p[0]], [p[1]], s=(4.6 * PT * 2) ** 2, marker=mark,
                   facecolor="white", edgecolor=color, linewidths=lw(1.3),
                   zorder=z)


def along_band(start, x_end, n, seed, side=-1):
    """Traces that are drawn into the order and follow it."""
    r = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = np.asarray(start) + r.normal(scale=12, size=2)
        xe = x_end + r.normal(scale=25)
        lane = side * r.uniform(4, BAND_HALF - 6)
        entry_x = s[0] + (xe - s[0]) * 0.28
        t = np.linspace(0, 1, 40)
        p0 = quad(s, (entry_x, band_y(entry_x) + lane),
                  r.normal(scale=0.05) - 0.12, t)
        xs = np.linspace(entry_x, xe, 90)
        wob = 4 * wave1d(np.linspace(0, 1, 90), r, 2, 1, 3)
        p1 = np.column_stack([xs, band_y(xs) + lane + wob])
        out.append(np.vstack([p0, p1[1:]]))
    return out


def toward_band(start, x_target, n, seed, side=+1):
    """Traces that head for the order and thin out at its edge."""
    r = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        s = np.asarray(start) + r.normal(scale=12, size=2)
        xt = x_target + r.normal(scale=14)
        e = (xt, band_y(xt) + side * (BAND_HALF + r.uniform(-4, 8)))
        t = np.linspace(0, 1, 60)
        base = quad(s, e, r.normal(scale=0.06), t)
        off = np.sin(np.pi * t) * 6 * wave1d(t, r)
        out.append(base + normals(base) * off[:, None])
    return out


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

# How far each tendency has moved by a given moment: the configuration
# drifts, so the recurring pattern is never a copy of the one before.
DRIFT = {
    "t1": {},
    "t2": {"A": (10, 12), "B": (-8, 16), "C": (14, -10), "D": (-6, -8),
           "F": (-10, 12)},
    "t3": {"A": (-4, 6), "B": (12, -6), "C": (-14, 14), "D": (26, -12),
           "F": (-6, 0)},
}


def phase_layout(key):
    """Tendency centres for one moment, placed relative to the order."""
    ox = PHASES[key]

    def above(x, h):
        return (ox + x, float(band_y(ox + x)) - h)

    def below(x, h):
        return (ox + x, float(band_y(ox + x)) + h)

    base = {
        "A": (ox + 82, 188), "B": (ox + 236, 142), "C": (ox + 392, 262),
        "D": below(318, 150), "E": above(128, 105), "F": below(470, 175),
    }
    return {k: (x + DRIFT[key].get(k, (0, 0))[0],
                y + DRIFT[key].get(k, (0, 0))[1])
            for k, (x, y) in base.items()}


CHAIN = [("A", "B", -0.10), ("B", "C", 0.12), ("C", "D", -0.08)]


def compose(ax):
    f = Field()
    f.dust(1400, 1)
    L = {k: phase_layout(k) for k in PHASES}

    # t1: an established configuration
    l1 = L["t1"]
    f.tendency(l1["A"], 110, 30, 22, 0.3, 11)
    f.tendency(l1["B"], 90, 34, 18, -0.2, 12)
    f.tendency(l1["C"], 105, 26, 26, 0.8, 13)
    f.tendency(l1["D"], 120, 36, 22, -0.3, 14)
    f.tendency(l1["E"], 75, 26, 16, 0.1, 15)
    f.tendency(l1["F"], 55, 22, 18, 0.5, 16)

    # t2: instances drift, B reshapes, E dissolves
    l2 = L["t2"]
    f.tendency(l2["A"], 105, 28, 24, 0.5, 21, drift=(6, -4))
    f.tendency(l2["B"], 85, 24, 26, 0.9, 22, drift=(-8, 6))
    f.tendency(l2["C"], 100, 28, 24, 0.4, 23, drift=(4, 6))
    f.tendency(l2["D"], 105, 34, 24, -0.1, 24, drift=(8, 2))
    f.tendency(l2["E"], 60, 22, 14, 0.1, 25, state="dissolving",
               drift=(-10, -14))
    f.tendency(l2["F"], 60, 24, 18, 0.3, 26, drift=(-6, 4))

    # t3: E is gone, the old D drifts off and a new D forms where the
    # generative relation crosses the order; the chain recurs
    l3 = L["t3"]
    old_d = (l3["D"][0] + 90, l3["D"][1] + 58)
    f.tendency(l3["A"], 100, 30, 22, 0.2, 31, drift=(-5, 5))
    f.tendency(l3["B"], 95, 30, 20, 0.1, 32, drift=(6, -3))
    f.tendency(l3["C"], 95, 26, 28, 0.9, 33, drift=(-4, 5))
    f.tendency(old_d, 55, 26, 18, -0.2, 34, state="dissolving",
               drift=(22, 10))
    f.tendency(l3["D"], 70, 26, 20, -0.4, 35, state="forming",
               toward=np.subtract(l3["C"], l3["D"]))
    f.tendency(l3["F"], 70, 26, 20, 0.2, 36, drift=(-4, -3))
    f.tendency(l3["E"], 18, 30, 20, 0.0, 37, state="faint")

    # --- trajectories: each tendency carried through the moments ----------
    old_d = tuple(old_d)
    paths = [[L["t1"][k], L["t2"][k], L["t3"][k]] for k in "ABCF"]
    paths += [[L["t1"]["D"], L["t2"]["D"], old_d],
              [L["t1"]["E"], L["t2"]["E"]]]
    tr_rng = np.random.default_rng(9)
    trajs = []
    for pts in paths:
        for a, b in zip(pts, pts[1:]):
            trajs += bundle(a, b, 2, int(tr_rng.integers(1e6)), bend=0.06,
                            spread=6, fray=8, reach=6, m=120)
    add_traces(ax, trajs, "#8E969E", 0.7, alpha=(0.28, 0.4), dash=(1.5, 4),
               z=0.8, rng=tr_rng)

    # --- density: halo and porous contours --------------------------------
    xs, ys, dens = f.density()
    dens /= dens.max()
    halo = LinearSegmentedColormap.from_list(
        "halo", [(1, 1, 1, 0), to_rgba("#DDE3E8", 0.9)])
    ax.imshow(np.clip(dens / 0.55, 0, 1) ** 0.8, extent=(0, W, FIELD_BOTTOM,
              FIELD_TOP), cmap=halo, interpolation="bicubic", zorder=0.5,
              aspect="auto")
    ax.contour(xs, ys, dens, levels=[0.16, 0.30, 0.48], colors=CONTOUR,
               linewidths=[lw(0.9), lw(1.0), lw(1.1)],
               linestyles=[(0, (1.5 * PT, 3.5 * PT)),
                           (0, (3 * PT, 4 * PT)),
                           (0, (5 * PT, 4 * PT))],
               alpha=0.85, zorder=2)

    # --- the order ---------------------------------------------------------
    bx = np.linspace(-20, W + 20, 700)
    by = band_y(bx)
    for half, a in ((BAND_HALF * 1.9, 0.25), (BAND_HALF * 1.45, 0.35),
                    (BAND_HALF * 1.1, 0.5), (BAND_HALF * 0.8, 0.6)):
        ax.fill_between(bx, by - half, by + half, color=BAND, alpha=a,
                        lw=0, zorder=1)
    r = np.random.default_rng(3)
    lanes = []
    for j in range(22):
        lane = r.uniform(-BAND_HALF, BAND_HALF)
        wob = 3.5 * wave1d(np.linspace(0, 1, len(bx)), r, 3, 3, 9)
        x0 = r.uniform(-20, W * 0.35)
        x1 = x0 + r.uniform(W * 0.4, W)
        m = (bx >= x0) & (bx <= x1)
        lanes.append(np.column_stack([bx[m], by[m] + lane + wob[m]]))
    add_traces(ax, lanes, BAND_LINE, 0.8, alpha=(0.35, 0.7), z=1.2,
               rng=np.random.default_rng(4))

    # --- relations, moment by moment ---------------------------------------
    rel_seed = 100
    for key, lay in L.items():
        rel_seed += 100
        rng = np.random.default_rng(rel_seed)
        # the generative chain recurs in every moment
        for i, (u, v, bend) in enumerate(CHAIN):
            n = 14 if (key, v) != ("t3", "D") else 11
            tr = bundle(lay[u], lay[v], n, rel_seed + i, bend=bend)
            add_traces(ax, tr, GEN, 1.25, rng=rng)
            evidence(ax, tr, GEN, rel_seed + 10 + i, k=3)
        # extractive: drawn into the order and following it
        if key != "t3":
            tr = along_band(lay["E"], lay["E"][0] + 330, 11, rel_seed + 5,
                            side=-1)
            add_traces(ax, tr, EXT, 1.1, alpha=(0.4, 0.8), dash=(6, 4.5),
                       fade=(0.6, 1.0), rng=rng)
            evidence(ax, tr[:6], EXT, rel_seed + 15, k=3)
        # extractive: heading for the order and thinning out at its edge
        tr = toward_band(lay["F"], lay["F"][0] - 70, 9, rel_seed + 6, +1)
        add_traces(ax, tr, EXT, 1.0, alpha=(0.4, 0.75), dash=(6, 4.5),
                   fade=(0.55, 1.0), rng=rng)
        # not qualified: a few faint wisps
        tr = bundle(lay["A"], lay["E"] if key != "t3" else lay["C"], 4,
                    rel_seed + 7, bend=0.15, spread=10, fray=6)
        add_traces(ax, tr, UNQ, 0.8, alpha=(0.45, 0.7), z=3, rng=rng)
        tr = bundle(lay["D"], lay["F"], 3, rel_seed + 8, bend=-0.1,
                    spread=8, fray=5)
        add_traces(ax, tr, UNQ, 0.8, alpha=(0.45, 0.7), z=3, rng=rng)

    # --- instances on top ----------------------------------------------------
    for pts, size, alpha in f.dots:
        ax.scatter(pts[:, 0], pts[:, 1], s=(size * PT * 2) ** 2, color=DOT,
                   alpha=alpha, linewidths=0, zorder=5)
    tsegs = [np.stack([a, b]) for fr, to, _ in f.trails
             for a, b in zip(fr, to)]
    talpha = [al for fr, to, al in f.trails for _ in fr]
    ax.add_collection(LineCollection(
        tsegs, colors=[to_rgba(DOT, a) for a in talpha],
        linewidths=lw(0.6), zorder=4.5))
    return L, old_d


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

def annotate(ax, text, xy, xytext, ha="left", va="center"):
    ax.annotate(text, xy=xy, xytext=xytext, fontsize=FS, fontstyle="italic",
                color=INK_SOFT, ha=ha, va=va, zorder=10,
                arrowprops=dict(arrowstyle="-", color="#8C939A",
                                lw=lw(0.8), shrinkA=3, shrinkB=2,
                                connectionstyle="arc3,rad=0.15"))


def tag(ax, xy, text):
    ax.text(xy[0], xy[1], text, fontsize=FS, color="#5B6269", ha="center",
            va="center", zorder=9,
            bbox=dict(boxstyle="round,pad=0.25,rounding_size=0.5",
                      facecolor="#E3E7EB", edgecolor="none"))


def frame(ax, L, old_d):
    # time
    y = 46
    ax.add_line(Line2D([10, W - 14], [y, y], color="#9AA0A6",
                       linewidth=lw(1.0)))
    ax.add_line(Line2D([W - 26, W - 12, W - 26], [y - 6, y, y + 6],
                       color="#9AA0A6", linewidth=lw(1.0)))
    for key, ox in PHASES.items():
        cx = ox + PHASE_W / 2
        ax.add_line(Line2D([cx, cx], [y - 5, y + 5], color="#9AA0A6",
                           linewidth=lw(1.0)))
        ax.text(cx, y - 10, key, fontsize=9.5, fontweight="bold",
                color=INK_SOFT, ha="center", va="bottom")

    l1, l2, l3 = L["t1"], L["t2"], L["t3"]
    # names hang on two tendencies, as an index only
    tag(ax, (l1["A"][0] + 4, l1["A"][1] + 58), "Group 1")
    tag(ax, (l1["D"][0] + 6, l1["D"][1] + 62), "Group 2")

    annotate(ax, "instances", (l1["A"][0] - 34, l1["A"][1] - 16), (14, 86))
    annotate(ax, "a tendency: instances cohering, porous at its edge",
             (l1["B"][0] + 24, l1["B"][1] - 34), (178, 86))
    annotate(ax, "a name is only an index",
             (l1["D"][0] - 34, l1["D"][1] + 66), (14, 786))
    annotate(ax, "extractive relations are\ndrawn into the order",
             (236, float(band_y(236)) - 8), (200, 330), ha="center")
    annotate(ax, "or thin out\nat its edge",
             (l1["F"][0] - 52, float(band_y(l1["F"][0] - 60)) + 48),
             (536, 640))
    annotate(ax, "generative relations cross the order;\n"
             "a new tendency forms",
             (l3["D"][0] - 40, l3["D"][1] + 30), (1064, 712))
    annotate(ax, "units drift; a tendency dissolves",
             (old_d[0] + 10, old_d[1] + 44), (W - 4, 786), ha="right")
    ax.text(W - 4, 86, "the generative pattern recurs; the units carrying "
            "it change", fontsize=FS, fontstyle="italic", color=GEN,
            ha="right", va="center", zorder=10)
    # the order's name, on the current where nothing crosses it
    x = 630.0
    ang = math.degrees(math.atan(-band_slope(x)))
    ax.text(x, float(band_y(x)), "assignment by the order", fontsize=FS,
            fontstyle="italic", color="#56606A", ha="center", va="center",
            rotation=ang, rotation_mode="anchor", zorder=1.5)


def legend(ax):
    y = 832
    x = 0.0

    def text(s):
        nonlocal x
        t = ax.text(x, y, s, fontsize=FS, color=INK_SOFT, va="center")
        x += t.get_window_extent(
            renderer=ax.figure.canvas.get_renderer()).width + 34

    for color, dash, width, label in (
            (GEN, None, 3.0, "generative"),
            (EXT, (7, 5), 2.6, "extractive"),
            (UNQ, None, 1.2, "not qualified")):
        ln = Line2D([x, x + 50], [y, y], color=color, linewidth=lw(width),
                    solid_capstyle="round")
        if dash:
            ln.set_linestyle((0, (dash[0] * PT, dash[1] * PT)))
        ax.add_line(ln)
        x += 62
        text(label)
    for mark, label in (("o", "interview"), ("s", "ethnography"),
                        ("v", "event")):
        ax.scatter([x + 6], [y], s=(4.6 * PT * 2) ** 2, marker=mark,
                   facecolor="white", edgecolor=INK_SOFT, linewidths=lw(1.3))
        x += 20
        text(label)
    ax.scatter([x + 4], [y], s=(2.4 * PT * 2) ** 2, color=DOT)
    x += 16
    text("instance")
    ax.text(W, y + 42, "Generated illustration, not a plot of the data.",
            fontsize=FS, fontstyle="italic", color=INK_SOFT, ha="right",
            va="center")


def build():
    fig = plt.figure(figsize=(PRINT_W_CM / 2.54, PRINT_H_CM / 2.54),
                     dpi=DPI_DESIGN)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    L, old_d = compose(ax)
    frame(ax, L, old_d)
    legend(ax)
    for t in ax.texts:
        assert t.get_fontsize() >= FS, t.get_text()
    return fig


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "output")
    ap.add_argument("--stem", default="fig3y_operationalising_relations")
    args = ap.parse_args()
    fig = build()
    args.out.mkdir(parents=True, exist_ok=True)
    meta = {"Title": "Operationalising relations"}
    fig.savefig(args.out / f"{args.stem}.svg",
                metadata={**meta, "Description": ALT_TEXT})
    fig.savefig(args.out / f"{args.stem}.pdf",
                metadata={**meta, "Subject": ALT_TEXT})
    fig.savefig(args.out / f"{args.stem}.png", dpi=300,
                metadata={**meta, "Description": ALT_TEXT})
    for ext in ("svg", "pdf", "png"):
        print(args.out / f"{args.stem}.{ext}")


if __name__ == "__main__":
    main()
