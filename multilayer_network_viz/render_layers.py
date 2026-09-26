#!/usr/bin/env python3
"""Render the macro, meso and micro social networks as three glowing spheres
joined by flows for the actors that appear in more than one layer.

Each layer is a fuzzy ball of its actors (positions carry no meaning), drawn in
perspective with depth cueing and sized by degree. Actors flagged in all three
layers (Triple_layer = yes) are drawn as bright gold lines through all three
blobs. Actors flagged in two layers only are drawn as faint lines between those
two.

Usage:
    python3 render_layers.py edges.csv -o figure            # dark background
    python3 render_layers.py edges.csv -o figure --light    # white background
"""

import argparse
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgb
from matplotlib.lines import Line2D

LAYERS = ["Macro", "Meso", "Micro"]
LAYER_COL = {"Macro": "Macro Network", "Meso": "Meso Network", "Micro": "Micro Network"}
SUFFIX = re.compile(r"_(Macro|Meso|Micro)( Network)?$")

# Spelling variants of the same actor across layers.
ALIASES = {"Alliance of Internationalist Feminists": "Alliance International Feminist"}

COLOURS = {"Macro": "#2f86ff", "Meso": "#62e03c", "Micro": "#e03cdc"}
GOLD = "#ffd23f"
# Two-layer flows get their own hues so the legend stays readable.
PAIR_COLOURS = {("Macro", "Meso"): "#3fd6c8", ("Meso", "Micro"): "#ff8f5a", ("Macro", "Micro"): "#a58bff"}


def actor(name):
    name = SUFFIX.sub("", str(name)).strip()
    return ALIASES.get(name, name)


# ---------------------------------------------------------------- data

def load(path):
    df = pd.read_csv(path)
    df = df[df["Source"].notna()]
    graphs = {}
    for layer in LAYERS:
        d = df[df["Layer"] == LAYER_COL[layer]]
        nodes, edges = set(), set()
        for s, t in zip(d["Source"].map(actor), d["Target"].map(actor)):
            if s:
                nodes.add(s)
            if t:
                nodes.add(t)
            if s and t and s != t:
                edges.add(tuple(sorted((s, t))))
        graphs[layer] = (sorted(nodes), sorted(edges))

    flagged = df[df["Intersection Type"].notna()].copy()
    flagged["actor"] = flagged["Source"].map(actor)
    triple = set(flagged.loc[flagged["Triple_layer"] == "yes", "actor"])
    pairs = {}
    for kind, d in flagged.groupby("Intersection Type"):
        a, b = sorted(kind.split("-"), key=LAYERS.index)
        pairs[(a, b)] = sorted(set(d["actor"]) - triple)
    return graphs, sorted(triple), pairs


# ---------------------------------------------------------------- layout

def fuzzy_ball(nodes, bridging, rng):
    """Scatter the actors in a unit ball, dense at the centre and soft at the
    edge. Positions carry no meaning. Bridging actors are kept on the half
    facing the viewer so their flows stay visible."""
    n = len(nodes)
    pts = np.empty((0, 3))
    while len(pts) < n:
        cand = rng.normal(0, 0.42, size=(2 * n, 3))
        pts = np.vstack([pts, cand[np.linalg.norm(cand, axis=1) < 1]])
    pts = pts[:n]
    for j, v in enumerate(nodes):
        if v in bridging:
            pts[j, 2] = abs(pts[j, 2]) * 0.8 + 0.1
    return {v: pts[j] for j, v in enumerate(nodes)}


def degrees(nodes, edges):
    idx = {v: j for j, v in enumerate(nodes)}
    deg = np.zeros(len(nodes))
    for a, b in edges:
        deg[idx[a]] += 1
        deg[idx[b]] += 1
    return deg


def project(p, centre, radius, focal=6.0):
    """Perspective projection of unit-ball coordinates. z > 0 faces the viewer."""
    z = p[..., 2]
    k = focal / (focal - z)
    xy = p[..., :2] * k[..., None] * radius * 0.98 + centre
    return xy, z


# ---------------------------------------------------------------- drawing helpers

def mix(c1, c2, t):
    c1 = np.array(to_rgb(c1)) if isinstance(c1, str) else np.asarray(c1)
    c2 = np.array(to_rgb(c2)) if isinstance(c2, str) else np.asarray(c2)
    t = np.asarray(t)[..., None]
    return c1 * (1 - t) + c2 * t


def radial_image(ax, centre, radius, colour, profile, extent_mult, alpha, zorder, res=400):
    ext = radius * extent_mult
    x = np.linspace(-ext, ext, res)
    X, Y = np.meshgrid(x, x)
    R = np.hypot(X, Y) / radius
    img = np.zeros((res, res, 4))
    img[..., :3] = to_rgb(colour)
    img[..., 3] = np.clip(profile(R), 0, 1) * alpha
    ax.imshow(img, extent=(centre[0] - ext, centre[0] + ext, centre[1] - ext, centre[1] + ext),
              origin="lower", interpolation="bilinear", zorder=zorder)


def catmull_rom(points, samples=60):
    """Centripetal Catmull-Rom spline through the given points."""
    P = np.asarray(points, float)
    P = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(len(P) - 3):
        p0, p1, p2, p3 = P[i:i + 4]
        t0 = 0.0
        t1 = t0 + np.linalg.norm(p1 - p0) ** 0.5 + 1e-9
        t2 = t1 + np.linalg.norm(p2 - p1) ** 0.5 + 1e-9
        t3 = t2 + np.linalg.norm(p3 - p2) ** 0.5 + 1e-9
        t = np.linspace(t1, t2, samples, endpoint=i == len(P) - 4)[:, None]
        a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
        a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
        a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
        b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
        b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
        out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    return np.vstack(out)


def segments(path):
    return np.stack([path[:-1], path[1:]], axis=1)


# ---------------------------------------------------------------- figure

def render(csv, out, light=False, labels=True, seed=7, dpi=600):
    graphs, triple, pairs = load(csv)
    rng = np.random.default_rng(seed)

    bg = "#ffffff" if light else "#0b0c0f"
    ink = "#1c1d21" if light else "#e8e9ec"
    sub_ink = "#55575e" if light else "#9a9ca3"

    # Blob area on the page is proportional to the actor count (radius ~ sqrt n),
    # so every blob has the same density of actors.
    n = {L: len(graphs[L][0]) for L in LAYERS}
    R = {L: (n[L] / n["Macro"]) ** 0.5 for L in LAYERS}
    C = {"Macro": np.array([0.0, 0.0]),
         "Meso": np.array([1.0 + R["Meso"] + 1.15, 0.42]),
         "Micro": np.array([1.0 + 2 * R["Meso"] + R["Micro"] + 2.2, 0.85])}
    # Actor size encodes degree on one scale shared by all layers.
    max_log_deg = max(np.log1p(degrees(*graphs[L]).max()) for L in LAYERS)

    bridging = set(triple)
    for actors in pairs.values():
        bridging.update(actors)

    fig = plt.figure(figsize=(14.08, 7.68), facecolor=bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(bg)
    x0, x1 = -1.3, C["Micro"][0] + R["Micro"] + 0.55
    half_h = (x1 - x0) / 2 * 7.68 / 14.08
    ax.set_xlim(x0, x1)
    ax.set_ylim(0.1 - half_h, 0.1 + half_h)
    ax.set_aspect("equal")
    ax.axis("off")

    screen = {}  # projected position of every actor, per layer
    stats = {}
    for L in LAYERS:
        nodes, edges = graphs[L]
        pos = fuzzy_ball(nodes, bridging, rng)
        deg = degrees(nodes, edges)
        P = np.array([pos[v] for v in nodes])
        xy, z = project(P, C[L], R[L])
        screen[L] = {v: xy[j] for j, v in enumerate(nodes)}
        stats[L] = (len(nodes), len(edges))
        col = COLOURS[L]
        depth = (z + 1) / 2  # 0 back, 1 front

        # soft glow behind the blob
        radial_image(ax, C[L], R[L], col, lambda r: np.exp(-(r / 0.7) ** 2), 1.8,
                     0.25 if light else 0.55, 1)

        # actors: size by degree, lighter for hubs and for the front of the blob
        hub = np.log1p(deg) / max_log_deg
        size = 1.5 * (0.45 + 0.8 * depth) * (1 + 14 * hub ** 2.2)
        whiten = np.clip(0.05 + 0.35 * depth + 0.75 * hub ** 1.5, 0, 0.92)
        ncol = np.zeros((len(nodes), 4))
        ncol[:, :3] = mix(col, "#ffffff", whiten * (0.5 if light else 1.0))
        ncol[:, 3] = np.clip(0.3 + 0.7 * depth ** 1.2 + 0.3 * hub, 0, 1)
        order = np.argsort(z)
        ax.scatter(xy[order, 0], xy[order, 1], s=size[order], c=ncol[order],
                   edgecolors=(1, 1, 1, 0.9) if light else (0.02, 0.03, 0.05, 0.6),
                   linewidths=0.15, zorder=3, rasterized=True)

    # ------------------------------------------------------------ flows
    # Thin, gently bowed lines from each actor in one blob to the same actor in
    # another, so the flows cross and weave into a web between the blobs.
    def pt(L, a):
        return screen[L][a]

    def bowed(p, q, bow):
        d = q - p
        normal = np.array([-d[1], d[0]])
        ctrl = (p + q) / 2 + normal * bow
        t = np.linspace(0, 1, 60)[:, None]
        return (1 - t) ** 2 * p + 2 * (1 - t) * t * ctrl + t ** 2 * q

    def pair_path(A, B, a):
        if (A, B) == ("Macro", "Micro"):
            # Arc over the meso blob rather than through it.
            d = C["Micro"] - C["Macro"]
            normal = np.array([-d[1], d[0]]) / np.linalg.norm(d)
            via = C["Meso"] + normal * R["Meso"] * rng.uniform(1.5, 2.3) + rng.normal(0, 0.06, 2)
            return catmull_rom([pt(A, a), via, pt(B, a)], 50)
        return bowed(pt(A, a), pt(B, a), rng.normal(0, 0.07))

    for (A, B), actors in pairs.items():
        for a in actors:
            if a not in screen[A] or a not in screen[B]:
                continue
            seg = segments(pair_path(A, B, a))
            t = np.linspace(0, 1, len(seg))
            c = np.zeros((len(seg), 4))
            c[:, :3] = mix(PAIR_COLOURS[(A, B)], "#000000" if light else "#ffffff", 0.15)
            c[:, 3] = 0.35 + 0.25 * np.sin(np.pi * t)
            ax.add_collection(LineCollection(seg, colors=c, linewidths=0.45, zorder=5,
                                             capstyle="round"))
            for L in (A, B):
                ax.scatter(*pt(L, a), s=4, color=PAIR_COLOURS[(A, B)], linewidths=0, zorder=6)

    gold_line = "#c98f00" if light else "#ffe27a"
    for a in triple:
        path = catmull_rom([pt("Macro", a), pt("Meso", a), pt("Micro", a)], 70)
        # A small random bow keeps the gold lines from lying on top of each other.
        mid = len(path) // 2
        d = path[-1] - path[0]
        normal = np.array([-d[1], d[0]]) / np.linalg.norm(d)
        w = np.sin(np.linspace(0, np.pi, mid))[:, None]
        w = np.vstack([w, np.sin(np.linspace(0, np.pi, len(path) - mid))[:, None]])
        path = path + w * normal * rng.normal(0, 0.035)
        seg = segments(path)
        for lw, alpha, colour in [(1.8, 0.05, GOLD), (0.9, 0.12, GOLD), (0.45, 0.9, gold_line)]:
            ax.add_collection(LineCollection(seg, colors=colour, linewidths=lw, alpha=alpha,
                                             zorder=7, capstyle="round", joinstyle="round"))
        for L in LAYERS:
            ax.scatter(*pt(L, a), s=16, color=GOLD, alpha=0.25, linewidths=0, zorder=8)
            ax.scatter(*pt(L, a), s=5, color=gold_line, linewidths=0, zorder=9)

    # ------------------------------------------------------------ labels
    if labels:
        for L in LAYERS:
            nn, ne = stats[L]
            y = C[L][1] - R[L] - 0.12
            ax.text(C[L][0], y, f"{L} network", ha="center", va="top", fontsize=11,
                    color=ink, fontweight="bold", zorder=10)
            ax.text(C[L][0], y - 0.11, f"{nn:,} actors · {ne:,} ties", ha="center", va="top",
                    fontsize=8.5, color=sub_ink, zorder=10)
        n_pairs = {k: len(v) for k, v in pairs.items()}
        handles = [
            Line2D([], [], color="#c98f00" if light else GOLD, lw=2.2,
                   label=f"Actor present in all three layers (n = {len(triple)})"),
        ]
        for (A, B), label in [(("Macro", "Meso"), "Macro–Meso"), (("Meso", "Micro"), "Meso–Micro"),
                              (("Macro", "Micro"), "Macro–Micro")]:
            handles.append(Line2D([], [], color=PAIR_COLOURS[(A, B)], lw=1.4, alpha=0.8,
                                  label=f"{label} only (n = {n_pairs.get((A, B), 0)})"))
        leg = ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8.5,
                        labelcolor=ink, handlelength=2.6, borderaxespad=1.4)
        leg.set_zorder(10)

    for ext in ("png", "pdf"):
        fig.savefig(f"{out}.{ext}", dpi=dpi, facecolor=bg)
    plt.close(fig)
    return stats, triple, pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="layered edge list (CSV)")
    ap.add_argument("-o", "--out", default="multilayer_network", help="output path without extension")
    ap.add_argument("--light", action="store_true", help="white background for print")
    ap.add_argument("--no-labels", action="store_true", help="omit layer captions and legend")
    ap.add_argument("--seed", type=int, default=7, help="random seed for node placement and flow jitter")
    ap.add_argument("--dpi", type=int, default=600)
    args = ap.parse_args()
    stats, triple, pairs = render(args.csv, args.out, args.light, not args.no_labels, args.seed, args.dpi)
    for L, (nn, ne) in stats.items():
        print(f"{L:6s} {nn:6,} actors {ne:6,} ties")
    print(f"all three layers: {len(triple)}")
    for (A, B), v in pairs.items():
        print(f"{A}-{B} only: {len(v)}")


if __name__ == "__main__":
    main()
