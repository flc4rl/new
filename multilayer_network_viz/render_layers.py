#!/usr/bin/env python3
"""Render the macro, meso and micro social networks as three glowing spheres
joined by flows for the actors that appear in more than one layer.

Each layer is laid out with a 3D force-directed algorithm, fitted into a ball
and drawn in perspective with depth cueing. Actors flagged in all three layers
(Triple_layer = yes) are drawn as bright gold flows through all three spheres.
Actors flagged in two layers only are drawn as faint flows between those two.

Usage:
    python3 render_layers.py edges.csv -o figure            # dark background
    python3 render_layers.py edges.csv -o figure --light    # white background
"""

import argparse
import os
import random
import re
from collections import defaultdict

import igraph as ig
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

def rotation_to(v, target):
    """Rotation matrix taking unit vector v onto unit vector target."""
    v, target = v / np.linalg.norm(v), target / np.linalg.norm(target)
    axis = np.cross(v, target)
    s, c = np.linalg.norm(axis), float(np.dot(v, target))
    if s < 1e-9:
        return np.eye(3)
    k = axis / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * K @ K


def ball_layout(nodes, edges, anchors, face, seed, cache=None):
    """3D force layout fitted to a unit ball. The layout's orientation is
    arbitrary, so it is rotated to turn the bridging actors towards `face`."""
    rng = np.random.default_rng(seed)
    ig.set_random_number_generator(random.Random(seed))
    idx = {n: i for i, n in enumerate(nodes)}
    g = ig.Graph(n=len(nodes), edges=[(idx[a], idx[b]) for a, b in edges])
    deg = np.array(g.degree(), float)
    pos = np.zeros((len(nodes), 3))

    connected = np.where(deg > 0)[0]
    sub = g.induced_subgraph(connected)
    key = f"{len(nodes)}_{len(edges)}_{seed}"
    if cache is not None and key in cache:
        xyz = cache[key]
    else:
        # Fruchterman-Reingold gives the organic hub-and-fan structure; the
        # macro network (~10k actors) takes about 1.5 minutes.
        niter = 300 if sub.vcount() > 2000 else 1500
        xyz = np.array(sub.layout_fruchterman_reingold(dim=3, niter=niter).coords)
        if cache is not None:
            cache[key] = xyz
    xyz = xyz - np.median(xyz, axis=0)
    # Whiten along the principal axes so a flattened layout still fills a ball.
    _, _, vt = np.linalg.svd(xyz - xyz.mean(axis=0), full_matrices=False)
    xyz = xyz @ vt.T
    xyz /= np.quantile(np.abs(xyz), 0.9, axis=0) + 1e-9
    r = np.linalg.norm(xyz, axis=1) + 1e-9
    # Keep the direction of every node and remap its radius by rank, so dense
    # hubs and their fans stay intact while the whole network fills the ball.
    rank = (np.argsort(np.argsort(r)) + 0.5) / len(r)
    new_r = 0.95 * (0.65 * rank ** (1 / 3) + 0.35 * np.clip(r / np.quantile(r, 0.99), 0, 1))
    pos[connected] = xyz / r[:, None] * new_r[:, None]

    # Actors with no ties in this layer sit on a thin outer shell.
    iso = np.where(deg == 0)[0]
    v = rng.normal(size=(len(iso), 3))
    pos[iso] = v / np.linalg.norm(v, axis=1)[:, None] * rng.uniform(0.9, 1.0, (len(iso), 1))

    a = [idx[n] for n in anchors if n in idx]
    if a:
        pos = pos @ rotation_to(pos[a].mean(axis=0), np.asarray(face, float)).T
    return {n: pos[i] for n, i in idx.items()}, deg, g


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

def render(csv, out, light=False, labels=True, seed=7, dpi=600, cache_path=None):
    graphs, triple, pairs = load(csv)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        cache = dict(np.load(cache_path))
    rng = np.random.default_rng(seed)

    bg = "#ffffff" if light else "#111215"
    ink = "#1c1d21" if light else "#e8e9ec"
    sub_ink = "#55575e" if light else "#9a9ca3"

    # Sphere radius scales with the cube root of the actor count (volume ~ n).
    n = {L: len(graphs[L][0]) for L in LAYERS}
    R = {L: (n[L] / n["Macro"]) ** (1 / 3) for L in LAYERS}
    C = {"Macro": np.array([0.0, 0.0]),
         "Meso": np.array([1.0 + R["Meso"] + 0.75, 0.42]),
         "Micro": np.array([1.0 + 2 * R["Meso"] + R["Micro"] + 1.28, 0.85])}

    # Actors each sphere should turn towards the viewer / neighbouring sphere.
    bridging = defaultdict(set)
    for a in triple:
        for L in LAYERS:
            bridging[L].add(a)
    for (A, B), actors in pairs.items():
        bridging[A].update(actors)
        bridging[B].update(actors)
    face = {"Macro": [0.55, 0.15, 0.82], "Meso": [0.0, 0.05, 1.0], "Micro": [-0.55, -0.15, 0.82]}

    fig = plt.figure(figsize=(14.08, 7.68), facecolor=bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(bg)
    x0, x1 = -1.3, C["Micro"][0] + R["Micro"] + 0.55
    half_h = (x1 - x0) / 2 * 7.68 / 14.08
    ax.set_xlim(x0, x1)
    ax.set_ylim(0.1 - half_h, 0.1 + half_h)
    ax.set_aspect("equal")
    ax.axis("off")

    if not light:  # soft vignette
        radial_image(ax, np.array([(ax.get_xlim()[0] + ax.get_xlim()[1]) / 2, 0]), 4.0, "#26282e",
                     lambda r: np.exp(-r ** 2 * 1.3), 1.4, 0.55, 0)

    screen = {}  # projected position of every node, per layer
    stats = {}
    for i, L in enumerate(LAYERS):
        nodes, edges = graphs[L]
        pos, deg, g = ball_layout(nodes, edges, bridging[L], face[L], seed + i, cache)
        P = np.array([pos[v] for v in nodes])
        xy, z = project(P, C[L], R[L])
        screen[L] = {v: (xy[j], z[j]) for j, v in enumerate(nodes)}
        stats[L] = (len(nodes), len(edges))
        col = COLOURS[L]
        depth = (z + 1) / 2  # 0 back, 1 front

        # shadow, halo and body glow
        radial_image(ax, C[L] + np.array([0.08, -0.10]) * R[L], R[L] * 1.02, "#000000",
                     lambda r: np.exp(-np.clip(r - 0.75, 0, None) ** 2 / 0.05) * (r < 1.6),
                     1.6, 0.0 if light else 0.55, 1)
        radial_image(ax, C[L], R[L], col,
                     lambda r: np.where(r < 1, 0.07 + 0.30 * r ** 10, np.exp(-((r - 1) / 0.07) ** 2) * 0.37),
                     1.5, 0.35 if light else 0.9, 2)

        # edges: tinted by depth, the front brighter than the back
        idx = {v: j for j, v in enumerate(nodes)}
        e = np.array([(idx[a], idx[b]) for a, b in edges], int).reshape(-1, 2)
        if len(e):
            ed = (depth[e[:, 0]] + depth[e[:, 1]]) / 2
            order = np.argsort(ed)
            ecol = np.zeros((len(e), 4))
            ecol[:, :3] = mix(col, "#000000" if light else "#ffffff", 0.15 * ed)
            base_a = {"Macro": 0.22, "Meso": 0.55, "Micro": 0.75}[L]
            ecol[:, 3] = base_a * (0.25 + 0.75 * ed ** 1.4)
            lw = {"Macro": 0.16, "Meso": 0.35, "Micro": 0.55}[L]
            lc = LineCollection(np.stack([xy[e[order, 0]], xy[e[order, 1]]], axis=1),
                                colors=ecol[order], linewidths=lw, zorder=3, rasterized=True)
            ax.add_collection(lc)

        # nodes: size by degree, brightness and size by depth, rim light at the silhouette
        rim = np.clip((np.hypot(*(xy - C[L]).T) / R[L] - 0.72) / 0.28, 0, 1)
        hub = np.log1p(deg) / np.log1p(deg.max() if deg.max() > 0 else 1)
        s_base = {"Macro": 0.8, "Meso": 3.0, "Micro": 6.0}[L]
        size = s_base * (0.35 + 0.9 * depth) * (1 + 6 * hub ** 2)
        whiten = np.clip(0.10 + 0.40 * depth + 0.30 * rim + 0.4 * hub, 0, 0.9)
        ncol = np.zeros((len(nodes), 4))
        ncol[:, :3] = mix(col, "#ffffff", whiten * (0.55 if light else 1.0))
        ncol[:, 3] = np.clip(0.18 + 0.72 * depth ** 1.3 + 0.2 * rim, 0, 1)
        order = np.argsort(z)
        ax.scatter(xy[order, 0], xy[order, 1], s=size[order], c=ncol[order], linewidths=0,
                   zorder=4, rasterized=True)

    # ------------------------------------------------------------ flows
    def waist(A, B, spread):
        """Point in the gap between two spheres where flows bundle, with jitter."""
        cA, cB = C[A], C[B]
        d = (cB - cA) / np.linalg.norm(cB - cA)
        gap = cA + d * (R[A] + (np.linalg.norm(cB - cA) - R[A] - R[B]) * 0.5)
        normal = np.array([-d[1], d[0]])
        return gap + normal * rng.normal(0, spread) + d * rng.normal(0, spread * 0.4)

    def pt(L, a):
        return screen[L][a][0]

    def pair_path(A, B, a):
        if (A, B) == ("Macro", "Micro"):
            # Route over the meso sphere.
            side = 1
            d = C["Micro"] - C["Macro"]
            normal = np.array([-d[1], d[0]]) / np.linalg.norm(d)
            via = C["Meso"] + normal * side * R["Meso"] * rng.uniform(1.35, 1.8)
            pts = [pt(A, a), waist(A, "Meso", 0.10) + normal * side * R["Meso"] * 0.6, via,
                   waist("Meso", B, 0.08) + normal * side * R["Meso"] * 0.6, pt(B, a)]
        else:
            pts = [pt(A, a), waist(A, B, 0.16), pt(B, a)]
        return catmull_rom(pts, 50)

    for (A, B), actors in pairs.items():
        for a in actors:
            if a not in screen[A] or a not in screen[B]:
                continue
            path = pair_path(A, B, a)
            seg = segments(path)
            t = np.linspace(0, 1, len(seg))
            c = np.zeros((len(seg), 4))
            c[:, :3] = to_rgb(PAIR_COLOURS[(A, B)])
            c[:, 3] = 0.38 * np.sin(np.pi * np.clip(t * 1.1 - 0.05, 0, 1)) ** 0.35 + 0.12
            ax.add_collection(LineCollection(seg, colors=c, linewidths=0.6, zorder=5,
                                             capstyle="round"))
            for L in (A, B):
                ax.scatter(*pt(L, a), s=5, color=PAIR_COLOURS[(A, B)], linewidths=0,
                           zorder=6, alpha=0.9)

    gold_core = "#fff4c2"
    for a in triple:
        pts = [pt("Macro", a), waist("Macro", "Meso", 0.05), pt("Meso", a),
               waist("Meso", "Micro", 0.04), pt("Micro", a)]
        path = catmull_rom(pts, 70)
        seg = segments(path)
        for lw, alpha, colour in [(3.2, 0.04, GOLD), (1.7, 0.09, GOLD), (0.9, 0.30, GOLD),
                                  (0.45, 0.95, "#c98f00" if light else gold_core)]:
            ax.add_collection(LineCollection(seg, colors=colour, linewidths=lw, alpha=alpha,
                                             zorder=7, capstyle="round", joinstyle="round"))
        for L in LAYERS:
            ax.scatter(*pt(L, a), s=22, color=GOLD, alpha=0.18, linewidths=0, zorder=8)
            ax.scatter(*pt(L, a), s=5, color=gold_core if not light else "#c98f00",
                       linewidths=0, zorder=9)

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

    if cache_path:
        np.savez(cache_path, **cache)
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
    ap.add_argument("--seed", type=int, default=7, help="random seed for layouts and flow jitter")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--layout-cache", help="npz file to reuse layouts between runs")
    args = ap.parse_args()
    stats, triple, pairs = render(args.csv, args.out, args.light, not args.no_labels, args.seed, args.dpi,
                                   args.layout_cache)
    for L, (nn, ne) in stats.items():
        print(f"{L:6s} {nn:6,} actors {ne:6,} ties")
    print(f"all three layers: {len(triple)}")
    for (A, B), v in pairs.items():
        print(f"{A}-{B} only: {len(v)}")


if __name__ == "__main__":
    main()
