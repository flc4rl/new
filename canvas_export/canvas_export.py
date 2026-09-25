#!/usr/bin/env python3
"""Export arbitrarily large Obsidian canvases (.canvas / JSON Canvas) to PNG.

Why the in-app exporters collapse: they rasterise the whole canvas into one
browser <canvas>/image, and Chromium caps that at roughly 16k px per side and
~268 megapixels in total. Past that they silently produce blank output, run
out of memory or crash Obsidian.

This tool avoids the limit entirely:

1. It reads the .canvas file (plain JSON) plus the vault files it references
   and builds a standalone HTML replica of the canvas (cards, notes, images,
   groups, edges, labels, colours, Markdown, callouts, maths, Mermaid).
2. A headless Chromium renders that page one viewport-sized tile at a time.
3. Tiles are stitched row-strip by row-strip and streamed straight into a PNG
   encoder, so memory use is bounded by a single strip, not the whole image.

The result can be tens of thousands of pixels per side. A downscaled preview
and, optionally, the individual tiles are written as well.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

import markdown
from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # we deliberately handle very large images

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".avif"}
KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"

# Obsidian's preset canvas colours "1".."6" (default theme).
PRESET_COLOURS = {
    "1": "#fb464c",  # red
    "2": "#e9973f",  # orange
    "3": "#e0de71",  # yellow
    "4": "#44cf6e",  # green
    "5": "#53dfdd",  # cyan
    "6": "#a882ff",  # purple
}

THEMES = {
    "light": {
        "bg": "#ffffff", "card": "#ffffff", "text": "#222222", "muted": "#6c6c6c",
        "border": "#c8c8c8", "edge": "#a3a3a3", "code": "#f3f3f3", "link": "#7852ee",
        "dots": "#e2e2e2",
    },
    "dark": {
        "bg": "#1e1e1e", "card": "#262626", "text": "#dadada", "muted": "#9a9a9a",
        "border": "#4a4a4a", "edge": "#6f6f6f", "code": "#2f2f2f", "link": "#a88bfa",
        "dots": "#2e2e2e",
    },
}


# --------------------------------------------------------------------------
# Vault / file resolution
# --------------------------------------------------------------------------

class Vault:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._index: dict[str, list[Path]] | None = None

    def _build_index(self) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                for key in {name.lower(), p.stem.lower()}:
                    index.setdefault(key, []).append(p)
        return index

    def resolve(self, target: str) -> Path | None:
        """Resolve a canvas path or a [[wikilink]] target to a file."""
        target = target.strip()
        if not target:
            return None
        direct = self.root / target
        if direct.is_file():
            return direct
        if (self.root / (target + ".md")).is_file():
            return self.root / (target + ".md")
        if self._index is None:
            self._index = self._build_index()
        name = Path(target).name.lower()
        hits = self._index.get(name) or self._index.get(name + ".md") or []
        if not hits:
            return None
        # Prefer Markdown notes for extensionless links, then shortest path.
        hits = sorted(hits, key=lambda p: (p.suffix.lower() != ".md", len(str(p))))
        return hits[0]


def find_vault_root(canvas: Path) -> Path:
    for parent in [canvas.parent, *canvas.parent.parents]:
        if (parent / ".obsidian").is_dir():
            return parent
    return canvas.parent


# --------------------------------------------------------------------------
# Obsidian-flavoured Markdown -> HTML
# --------------------------------------------------------------------------

CODE_SPLIT = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]+`)", re.S)
FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*(\n|\Z)", re.S)
MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.S)
MATH_INLINE = re.compile(r"(?<![\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)")
EMBED = re.compile(r"!\[\[([^\]|#^]*)([#^][^\]|]*)?(?:\|([^\]]*))?\]\]")
WIKILINK = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
MD_IMAGE = re.compile(r"!\[([^\]]*)\]\((?!https?:|data:|file:)([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HIGHLIGHT = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
COMMENT = re.compile(r"%%.*?%%", re.S)
TAG = re.compile(r"(?<![\w&/#])#([A-Za-z_][\w/-]*)")


class ImageCache:
    """Downscaled copies of vault images.

    Photos and screenshots are often several thousand pixels wide but shown in
    a card a few hundred units across. Embedding the originals makes PDFs
    balloon (Chromium stores them uncompressed) and slows PNG rendering, so
    each image is shrunk once to at most `max_px` on its longest side.
    """

    def __init__(self, max_px: int, force_jpeg: bool = False):
        self.max_px = max_px
        # For PDFs: Chromium embeds JPEGs as they are but stores every other
        # format (WebP, AVIF, PNG photos, GIF) as raw pixels, often 10x larger.
        self.force_jpeg = force_jpeg
        self.dir = Path(tempfile.mkdtemp(prefix="canvas_export_img_"))
        self.cache: dict[Path, Path] = {}
        self.failed: list[Path] = []

    def uri(self, path: Path) -> str:
        return self.get(path).as_uri()

    def get(self, path: Path) -> Path:
        ext = path.suffix.lower()
        if not (self.max_px or self.force_jpeg) or ext == ".svg":
            return path
        if path in self.cache:
            return self.cache[path]
        result = path
        try:
            from PIL import ImageOps
            with Image.open(path) as im:
                needs_rotation = im.getexif().get(0x0112, 1) != 1
                big = bool(self.max_px) and max(im.size) > self.max_px
                convert = self.force_jpeg and ext not in {".jpg", ".jpeg"}
                if big or needs_rotation or convert:
                    im.seek(0)  # first frame of animations
                    im = ImageOps.exif_transpose(im)
                    if self.max_px:
                        im.thumbnail((self.max_px, self.max_px), Image.LANCZOS)
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGBA")
                    transparent = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255
                    out = self.dir / f"{len(self.cache):05d}{'.png' if transparent else '.jpg'}"
                    if transparent:
                        im.save(out, optimize=True)
                    else:
                        im.convert("RGB").save(out, quality=85, optimize=True)
                    result = out
        except Exception:
            self.failed.append(path)  # unreadable by Pillow (e.g. HEIC): let the browser try
        self.cache[path] = result
        return result

    def cleanup(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class Renderer:
    def __init__(self, vault: Vault, images: ImageCache, max_embed_depth: int = 2):
        self.vault = vault
        self.images = images
        self.max_embed_depth = max_embed_depth
        self.placeholders: dict[str, str] = {}
        self.uses_math = False
        self.uses_mermaid = False

    def _hold(self, html_fragment: str) -> str:
        key = f"XPHX{len(self.placeholders)}XPHX"
        self.placeholders[key] = html_fragment
        return key

    def _restore(self, text: str) -> str:
        # Placeholders can nest (embeds inside embeds), so loop until stable.
        for _ in range(10):
            new = re.sub(r"XPHX\d+XPHX", lambda m: self.placeholders.get(m.group(0), m.group(0)), text)
            if new == text:
                break
            text = new
        return text

    def image_tag(self, path: Path, size: str | None = None, alt: str = "") -> str:
        style = ""
        if size:
            m = re.fullmatch(r"\s*(\d+)\s*(?:x\s*(\d+))?\s*", size)
            if m:
                style = f' style="width:{m.group(1)}px;' + (f'height:{m.group(2)}px;' if m.group(2) else "") + '"'
        return f'<img src="{html.escape(self.images.uri(path))}" alt="{html.escape(alt)}"{style}>'

    def _embed(self, m: re.Match, depth: int, base: Path | None) -> str:
        target, sub, alias = m.group(1), (m.group(2) or ""), m.group(3)
        path = self.vault.resolve(target) if target else base
        if path is None:
            return self._hold(f'<span class="unresolved">![[{html.escape(target + sub)}]]</span>')
        if path.suffix.lower() in IMAGE_EXTS:
            return self._hold(self.image_tag(path, alias, alt=path.name))
        if path.suffix.lower() == ".md" and depth < self.max_embed_depth:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            text = extract_subpath(text, sub)
            inner = self.markdown_to_html(text, depth + 1, path)
            return self._hold(
                f'<div class="embed"><div class="embed-title">{html.escape(path.stem + sub)}</div>{inner}</div>'
            )
        return self._hold(f'<span class="internal-link">{html.escape(alias or path.name)}</span>')

    def _prose(self, seg: str, depth: int, base: Path | None) -> str:
        seg = COMMENT.sub("", seg)

        def block_math(m):
            self.uses_math = True
            return self._hold(f'<div class="math" data-display="1" data-tex="{html.escape(m.group(1).strip())}">'
                              f'$${html.escape(m.group(1))}$$</div>')

        def inline_math(m):
            self.uses_math = True
            return self._hold(f'<span class="math" data-tex="{html.escape(m.group(1))}">${html.escape(m.group(1))}$</span>')

        seg = MATH_BLOCK.sub(block_math, seg)
        seg = MATH_INLINE.sub(inline_math, seg)
        seg = EMBED.sub(lambda m: self._embed(m, depth, base), seg)

        def md_image(m):
            p = self.vault.resolve(m.group(2).replace("%20", " "))
            return self._hold(self.image_tag(p, alt=m.group(1))) if p else m.group(0)

        seg = MD_IMAGE.sub(md_image, seg)

        def wikilink(m):
            target, alias = m.group(1), m.group(2)
            label = alias or target
            return self._hold(f'<span class="internal-link">{html.escape(label)}</span>')

        seg = WIKILINK.sub(wikilink, seg)
        seg = HIGHLIGHT.sub(lambda m: f"<mark>{m.group(1)}</mark>", seg)
        seg = TAG.sub(lambda m: self._hold(f'<span class="tag">#{html.escape(m.group(1))}</span>'), seg)
        return seg

    def markdown_to_html(self, text: str, depth: int = 0, base: Path | None = None) -> str:
        text = FRONTMATTER.sub("", text)
        parts = CODE_SPLIT.split(text)
        text = "".join(p if i % 2 else self._prose(p, depth, base) for i, p in enumerate(parts))
        out = markdown.markdown(
            text,
            extensions=["extra", "sane_lists", "nl2br", "fenced_code"],
            output_format="html",
        )
        out = self._restore(out)
        out = postprocess_html(out)
        if 'class="language-mermaid"' in out:
            self.uses_mermaid = True
            out = re.sub(
                r'<pre><code class="language-mermaid">(.*?)</code></pre>',
                lambda m: f'<pre class="mermaid">{m.group(1)}</pre>',
                out, flags=re.S,
            )
        return out


CALLOUT_ICONS = {
    "note": "#086ddd", "info": "#086ddd", "todo": "#086ddd", "abstract": "#00bfbc",
    "summary": "#00bfbc", "tldr": "#00bfbc", "tip": "#00bfbc", "hint": "#00bfbc",
    "important": "#00bfbc", "success": "#08b94e", "check": "#08b94e", "done": "#08b94e",
    "question": "#ec7500", "help": "#ec7500", "faq": "#ec7500", "warning": "#ec7500",
    "caution": "#ec7500", "attention": "#ec7500", "failure": "#e93147", "fail": "#e93147",
    "missing": "#e93147", "danger": "#e93147", "error": "#e93147", "bug": "#e93147",
    "example": "#7852ee", "quote": "#9e9e9e", "cite": "#9e9e9e",
}


def postprocess_html(out: str) -> str:
    # Task lists.
    out = re.sub(r"<li>\s*\[ \]\s*", '<li class="task">☐ ', out)
    out = re.sub(r"<li>\s*\[[xX]\]\s*", '<li class="task done">☑ ', out)
    out = re.sub(r"<li>\s*<p>\s*\[ \]\s*", '<li class="task"><p>☐ ', out)
    out = re.sub(r"<li>\s*<p>\s*\[[xX]\]\s*", '<li class="task done"><p>☑ ', out)

    # Callouts: > [!type]± Title
    def callout(m):
        kind, title, rest = m.group(1).lower(), m.group(3), m.group(4)
        colour = CALLOUT_ICONS.get(kind, "#086ddd")
        title = (title or "").strip() or kind.capitalize()
        rest = re.sub(r"^\s*<br\s*/?>\s*", "", rest)
        return (f'<div class="callout" style="--c:{colour}"><div class="callout-title">{title}</div>'
                f'<div class="callout-body"><p>{rest}</div></div>')

    out = re.sub(
        r"<blockquote>\s*<p>\[!(\w+)\]([+-]?)([^\n<]*)(.*?)</blockquote>",
        callout, out, flags=re.S,
    )
    return out


def extract_subpath(text: str, sub: str) -> str:
    """Return the section under '#Heading' (or the whole text)."""
    if not sub or not sub.startswith("#"):
        return text
    heading = sub.lstrip("#").strip().lower()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and m.group(2).strip().lower() == heading:
            level = len(m.group(1))
            end = len(lines)
            for j in range(i + 1, len(lines)):
                n = re.match(r"^(#{1,6})\s", lines[j])
                if n and len(n.group(1)) <= level:
                    end = j
                    break
            return "\n".join(lines[i:end])
    return text


# --------------------------------------------------------------------------
# Canvas -> HTML page
# --------------------------------------------------------------------------

def colour_of(value: str | None, default: str) -> str:
    if not value:
        return default
    return PRESET_COLOURS.get(str(value), value)


def canvas_bbox(nodes: list[dict]) -> tuple[float, float, float, float]:
    xs0 = [n["x"] for n in nodes]
    ys0 = [n["y"] for n in nodes]
    xs1 = [n["x"] + n["width"] for n in nodes]
    ys1 = [n["y"] + n["height"] for n in nodes]
    # Group labels sit just above the group box.
    top_extra = 40 if any(n.get("type") == "group" for n in nodes) else 0
    return min(xs0), min(ys0) - top_extra, max(xs1), max(ys1)


def anchor(node: dict, side: str) -> tuple[float, float]:
    x, y, w, h = node["x"], node["y"], node["width"], node["height"]
    return {
        "top": (x + w / 2, y),
        "bottom": (x + w / 2, y + h),
        "left": (x, y + h / 2),
        "right": (x + w, y + h / 2),
    }[side]


def guess_sides(a: dict, b: dict) -> tuple[str, str]:
    ax, ay = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    bx, by = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    dx, dy = bx - ax, by - ay
    if abs(dx) * max(a["height"], 1) > abs(dy) * max(a["width"], 1):
        return ("right", "left") if dx > 0 else ("left", "right")
    return ("bottom", "top") if dy > 0 else ("top", "bottom")


NORMALS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}


def arrow_head(tip, frm, colour, size=12):
    dx, dy = tip[0] - frm[0], tip[1] - frm[1]
    d = math.hypot(dx, dy) or 1
    ux, uy = dx / d, dy / d
    bx, by = tip[0] - ux * size, tip[1] - uy * size
    px, py = -uy * size * 0.55, ux * size * 0.55
    pts = f"{tip[0]:.1f},{tip[1]:.1f} {bx + px:.1f},{by + py:.1f} {bx - px:.1f},{by - py:.1f}"
    return f'<polygon points="{pts}" fill="{colour}"/>'


def render_edges(edges, nodes_by_id, ox, oy, theme) -> tuple[str, str]:
    paths, labels = [], []
    for e in edges:
        a, b = nodes_by_id.get(e.get("fromNode")), nodes_by_id.get(e.get("toNode"))
        if not a or not b:
            continue
        gs = guess_sides(a, b)
        fs, ts = e.get("fromSide") or gs[0], e.get("toSide") or gs[1]
        p0, p3 = anchor(a, fs), anchor(b, ts)
        p0 = (p0[0] - ox, p0[1] - oy)
        p3 = (p3[0] - ox, p3[1] - oy)
        dist = math.hypot(p3[0] - p0[0], p3[1] - p0[1])
        k = max(40.0, min(dist * 0.5, 250.0))
        n0, n3 = NORMALS[fs], NORMALS[ts]
        p1 = (p0[0] + n0[0] * k, p0[1] + n0[1] * k)
        p2 = (p3[0] + n3[0] * k, p3[1] + n3[1] * k)
        colour = colour_of(e.get("color"), theme["edge"])
        paths.append(
            f'<path d="M{p0[0]:.1f},{p0[1]:.1f} C{p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f} '
            f'{p3[0]:.1f},{p3[1]:.1f}" stroke="{colour}" stroke-width="3" fill="none"/>'
        )
        if e.get("toEnd", "arrow") == "arrow":
            paths.append(arrow_head(p3, p2, colour))
        if e.get("fromEnd", "none") == "arrow":
            paths.append(arrow_head(p0, p1, colour))
        if e.get("label"):
            mx = 0.125 * p0[0] + 0.375 * p1[0] + 0.375 * p2[0] + 0.125 * p3[0]
            my = 0.125 * p0[1] + 0.375 * p1[1] + 0.375 * p2[1] + 0.125 * p3[1]
            labels.append(
                f'<div class="edge-label" style="left:{mx:.1f}px;top:{my:.1f}px">'
                f'{html.escape(e["label"]).replace(chr(10), "<br>")}</div>'
            )
    return "".join(paths), "".join(labels)


def render_node(n, ox, oy, renderer: Renderer, vault: Vault, theme) -> str:
    kind = n.get("type", "text")
    x, y, w, h = n["x"] - ox, n["y"] - oy, n["width"], n["height"]
    colour = n.get("color")
    style = f"left:{x}px;top:{y}px;width:{w}px;height:{h}px;"
    if colour:
        style += f"--nc:{colour_of(colour, theme['border'])};"
    cls = "node" + (" coloured" if colour else "")

    if kind == "group":
        label = n.get("label") or ""
        bg = ""
        if n.get("background"):
            p = vault.resolve(n["background"])
            if p:
                size = {"cover": "cover", "ratio": "contain", "repeat": "auto"}.get(n.get("backgroundStyle"), "cover")
                rep = "repeat" if n.get("backgroundStyle") == "repeat" else "no-repeat"
                bg = f'background-image:url("{renderer.images.uri(p)}");background-size:{size};background-repeat:{rep};'
        lab = f'<div class="group-label">{html.escape(label)}</div>' if label else ""
        return f'<div class="group{" coloured" if colour else ""}" style="{style}{bg}">{lab}</div>'

    if kind == "text":
        body = renderer.markdown_to_html(n.get("text", ""))
        return f'<div class="{cls}" style="{style}"><div class="content md">{body}</div></div>'

    if kind == "link":
        url = n.get("url", "")
        return (f'<div class="{cls} link-node" style="{style}"><div class="content">'
                f'<div class="link-icon">🔗</div><div class="link-url">{html.escape(url)}</div></div></div>')

    if kind == "file":
        target = n.get("file", "")
        path = vault.resolve(target)
        title = (f'<div class="file-title" style="left:{x}px;top:{y - 26}px;max-width:{w}px">'
                 f'{html.escape(Path(target).stem or target)}</div>')
        if path is None:
            return (f'<div class="{cls}" style="{style}"><div class="content md">'
                    f'<p class="unresolved">Missing file: {html.escape(target)}</p></div></div>')
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS:
            return (f'<div class="{cls} image-node" style="{style}">'
                    f'<img src="{html.escape(renderer.images.uri(path))}" alt=""></div>')
        if ext == ".md":
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            text = extract_subpath(text, n.get("subpath", ""))
            body = renderer.markdown_to_html(text, 0, path)
            return f'{title}<div class="{cls}" style="{style}"><div class="content md">{body}</div></div>'
        if ext == ".canvas":
            label = "Canvas"
        elif ext == ".pdf":
            label = "PDF"
        else:
            label = ext.lstrip(".").upper() or "File"
        return (f'<div class="{cls} link-node" style="{style}"><div class="content">'
                f'<div class="link-icon">📄 {label}</div><div class="link-url">{html.escape(target)}</div></div></div>')

    return f'<div class="{cls}" style="{style}"></div>'


CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); overflow: hidden; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue",
       Arial, "Noto Sans", sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji";
       color: var(--text); font-size: 16px; line-height: 1.5; -webkit-font-smoothing: antialiased; }
#world { position: absolute; left: 0; top: 0; transform-origin: 0 0; background: var(--bg); }
#world.dots { background-image: radial-gradient(var(--dots) 1.5px, transparent 1.5px);
              background-size: 20px 20px; }
.group, .node { position: absolute; border-radius: 10px; }
.group { border: 3px solid var(--border); background: color-mix(in srgb, var(--border) 8%, transparent); }
.group.coloured { border-color: var(--nc); background-color: color-mix(in srgb, var(--nc) 10%, transparent); }
.group-label { position: absolute; left: 0; bottom: 100%; margin-bottom: 6px; padding: 2px 10px;
               font-size: 22px; font-weight: 600; border-radius: 6px; white-space: nowrap;
               background: color-mix(in srgb, var(--nc, var(--border)) 25%, var(--bg)); }
svg#edges { position: absolute; left: 0; top: 0; overflow: visible; }
.node { background: var(--card); border: 2px solid var(--border); overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.node.coloured { border-color: var(--nc);
                 background: color-mix(in srgb, var(--nc) 9%, var(--card)); }
.node .content { width: 100%; height: 100%; overflow: hidden; padding: 6px 20px; }
.image-node { padding: 0; background: transparent; border: none; box-shadow: none; }
.image-node img { width: 100%; height: 100%; object-fit: contain; display: block; border-radius: 8px; }
.link-node .content { display: flex; flex-direction: column; justify-content: center; gap: 4px; }
.link-icon { font-weight: 600; color: var(--muted); }
.link-url { color: var(--link); word-break: break-all; }
.file-title { position: absolute; color: var(--muted); font-size: 16px; white-space: nowrap;
              overflow: hidden; text-overflow: ellipsis; }
.edge-label { position: absolute; transform: translate(-50%, -50%); background: var(--bg);
              padding: 2px 8px; border-radius: 6px; font-size: 15px; color: var(--text);
              text-align: center; max-width: 320px; }
.md h1 { font-size: 1.8em; margin: .5em 0 .3em; } .md h2 { font-size: 1.5em; margin: .5em 0 .3em; }
.md h3 { font-size: 1.3em; margin: .5em 0 .3em; } .md h4, .md h5, .md h6 { font-size: 1.1em; margin: .5em 0 .3em; }
.md p { margin: .5em 0; } .md ul, .md ol { margin: .4em 0; padding-left: 1.6em; }
.md li.task { list-style: none; margin-left: -1.2em; } .md li.task.done { color: var(--muted); text-decoration: line-through; }
.md img { max-width: 100%; }
.md code { background: var(--code); padding: .1em .3em; border-radius: 4px; font-size: .88em;
           font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
.md pre { background: var(--code); padding: 10px 12px; border-radius: 6px; overflow: hidden; white-space: pre-wrap; }
.md pre code { background: none; padding: 0; }
.md pre.mermaid { background: none; text-align: center; }
.md blockquote { margin: .5em 0; padding: 0 0 0 1em; border-left: 3px solid var(--link); color: var(--muted); }
.md table { border-collapse: collapse; margin: .5em 0; } .md th, .md td { border: 1px solid var(--border); padding: 3px 8px; }
.md hr { border: none; border-top: 1px solid var(--border); }
.md a, .internal-link { color: var(--link); text-decoration: none; }
.md mark { background: #fff35c99; color: inherit; }
.tag { color: var(--link); background: color-mix(in srgb, var(--link) 12%, transparent); padding: 0 .4em; border-radius: 1em; font-size: .9em; }
.unresolved { color: var(--muted); font-style: italic; }
.embed { border-left: 3px solid var(--link); padding-left: 12px; margin: .5em 0; }
.embed-title { font-weight: 600; color: var(--muted); font-size: .9em; }
.callout { border-radius: 6px; padding: 8px 14px; margin: .6em 0; background: color-mix(in srgb, var(--c) 10%, transparent); }
.callout-title { font-weight: 600; color: var(--c); }
.callout-body p { margin: .3em 0; }
.math[data-display="1"] { text-align: center; margin: .5em 0; }
"""


def build_html(canvas: dict, vault: Vault, theme_name: str, padding: int, dots: bool,
               allow_remote: bool, images: ImageCache) -> tuple[str, int, int]:
    nodes = canvas.get("nodes") or []
    edges = canvas.get("edges") or []
    if not nodes:
        raise SystemExit("The canvas has no nodes.")
    theme = THEMES[theme_name]
    x0, y0, x1, y1 = canvas_bbox(nodes)
    ox, oy = x0 - padding, y0 - padding
    width = int(math.ceil(x1 - x0 + 2 * padding))
    height = int(math.ceil(y1 - y0 + 2 * padding))

    renderer = Renderer(vault, images)
    nodes_by_id = {n["id"]: n for n in nodes if "id" in n}
    # Obsidian paints groups underneath everything else; larger groups first.
    groups = sorted((n for n in nodes if n.get("type") == "group"),
                    key=lambda n: -n["width"] * n["height"])
    others = [n for n in nodes if n.get("type") != "group"]

    t0 = time.time()
    group_html = "".join(render_node(n, ox, oy, renderer, vault, theme) for n in groups)
    node_parts = []
    for i, n in enumerate(others, 1):
        node_parts.append(render_node(n, ox, oy, renderer, vault, theme))
        if i % 500 == 0:
            print(f"  rendered {i}/{len(others)} cards to HTML", file=sys.stderr)
    node_html = "".join(node_parts)
    edge_svg, edge_labels = render_edges(edges, nodes_by_id, ox, oy, theme)
    print(f"  built HTML for {len(nodes)} nodes and {len(edges)} edges in {time.time() - t0:.1f}s",
          file=sys.stderr)

    vars_css = ":root{" + "".join(f"--{k}:{v};" for k, v in theme.items()) + "}"
    head = [f"<style>{vars_css}{CSS}</style>"]
    scripts = []
    if allow_remote and renderer.uses_math:
        head.append(f'<link rel="stylesheet" href="{KATEX}katex.min.css">')
        scripts.append(f'<script src="{KATEX}katex.min.js"></script>')
    if allow_remote and renderer.uses_mermaid:
        scripts.append(f'<script src="{MERMAID}"></script>')
    scripts.append(f"""<script>
window.__ready = (async () => {{
  try {{
    if (window.katex) document.querySelectorAll('.math').forEach(el => {{
      try {{ katex.render(el.dataset.tex, el, {{displayMode: el.dataset.display === '1', throwOnError: false}}); }} catch (e) {{}}
    }});
    if (window.mermaid) {{
      mermaid.initialize({{startOnLoad: false, theme: '{"dark" if theme_name == "dark" else "default"}'}});
      try {{ await mermaid.run({{querySelector: 'pre.mermaid'}}); }} catch (e) {{}}
    }}
    await Promise.all([...document.images].map(img => img.complete ? null :
      new Promise(r => {{ img.onload = img.onerror = r; }})));
    if (document.fonts) await document.fonts.ready;
  }} catch (e) {{}}
  return true;
}})();
</script>""")

    page = (
        "<!doctype html><html><head><meta charset='utf-8'>" + "".join(head) + "</head><body>"
        f'<div id="world" class="{"dots" if dots else ""}" style="width:{width}px;height:{height}px">'
        f"{group_html}"
        f'<svg id="edges" width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">{edge_svg}</svg>'
        f"{node_html}{edge_labels}</div>"
        + "".join(scripts) + "</body></html>"
    )
    return page, width, height


# --------------------------------------------------------------------------
# Streaming PNG writer (never holds the full image in memory)
# --------------------------------------------------------------------------

class StreamingPNG:
    def __init__(self, path: Path, width: int, height: int, level: int = 6):
        self.f = open(path, "wb")
        self.width, self.height = width, height
        self.rows_written = 0
        self.z = zlib.compressobj(level)
        self.f.write(b"\x89PNG\r\n\x1a\n")
        self._chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))  # 8-bit RGB

    def _chunk(self, tag: bytes, data: bytes):
        self.f.write(struct.pack(">I", len(data)))
        self.f.write(tag)
        self.f.write(data)
        self.f.write(struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    def write_strip(self, img: Image.Image):
        assert img.width == self.width and img.mode == "RGB"
        raw = img.tobytes()
        stride = self.width * 3
        buf = bytearray()
        for r in range(img.height):
            buf += b"\x00"  # filter type: none
            buf += raw[r * stride:(r + 1) * stride]
            if len(buf) > 8 << 20:
                self._flush(self.z.compress(bytes(buf)))
                buf.clear()
        if buf:
            self._flush(self.z.compress(bytes(buf)))
        self.rows_written += img.height

    def _flush(self, data: bytes):
        if data:
            self._chunk(b"IDAT", data)

    def close(self):
        if self.rows_written != self.height:
            raise RuntimeError(f"wrote {self.rows_written} rows, expected {self.height}")
        self._flush(self.z.flush())
        self._chunk(b"IEND", b"")
        self.f.close()


# --------------------------------------------------------------------------
# Tiled rendering
# --------------------------------------------------------------------------

def render(args) -> None:
    canvas_path = Path(args.canvas).expanduser().resolve()
    vault = Vault(Path(args.vault).expanduser() if args.vault else find_vault_root(canvas_path))
    canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
    print(f"Canvas: {canvas_path}\nVault:  {vault.root}", file=sys.stderr)

    out = Path(args.output).expanduser().resolve() if args.output else canvas_path.with_suffix(".png")
    if args.pdf and out.suffix.lower() != ".pdf":
        out = out.with_suffix(".pdf")
    is_pdf = out.suffix.lower() == ".pdf"

    # The HTML-only replica must keep pointing at the original images.
    shrink = 0 if (args.html_only or args.dry_run or args.keep_html) else args.max_image_px
    images = ImageCache(shrink, force_jpeg=is_pdf and shrink > 0)
    try:
        page, css_w, css_h = build_html(canvas, vault, args.theme, args.padding, args.dots,
                                        not args.offline, images)
        if images.cache:
            changed = sum(1 for k, v in images.cache.items() if k != v)
            print(f"  {len(images.cache)} images, {changed} shrunk or recompressed", file=sys.stderr)
        for p in images.failed[:10]:
            print(f"  could not read image {p.name}; embedded as is", file=sys.stderr)
        scale = args.scale
        out_w, out_h = int(math.ceil(css_w * scale)), int(math.ceil(css_h * scale))
        if is_pdf:
            print(f"Canvas area {css_w}x{css_h} units -> single-page vector PDF", file=sys.stderr)
        else:
            print(f"Canvas area {css_w}x{css_h} units -> PNG {out_w}x{out_h} px "
                  f"({out_w * out_h / 1e6:,.0f} MP) at scale {scale}", file=sys.stderr)

        html_path = out.with_suffix(".html") if args.keep_html else Path(tempfile.mkstemp(suffix=".html")[1])
        html_path.write_text(page, encoding="utf-8")
        if args.html_only:
            print(f"Wrote {html_path}", file=sys.stderr)
            return
        if args.dry_run:
            return
        if is_pdf:
            render_pdf(args, out, html_path, css_w, css_h)
        else:
            render_png(args, out, html_path, css_w, css_h, out_w, out_h)
    finally:
        images.cleanup()


def render_png(args, out: Path, html_path: Path, css_w: int, css_h: int, out_w: int, out_h: int) -> None:
    scale = args.scale
    if max(out_w, out_h) >= 2**31 - 1:
        raise SystemExit("Output exceeds the PNG size limit; lower --scale.")

    from playwright.sync_api import sync_playwright

    tile_css = max(64, int(args.tile // scale))
    cols, rows = math.ceil(css_w / tile_css), math.ceil(css_h / tile_css)
    print(f"Rendering {cols}x{rows} = {cols * rows} tiles of {tile_css} canvas units "
          f"({int(tile_css * scale)} px)", file=sys.stderr)

    tiles_dir = Path(args.tiles_dir).expanduser() if args.tiles_dir else None
    if tiles_dir:
        tiles_dir.mkdir(parents=True, exist_ok=True)

    preview = None
    if args.preview:
        pscale = min(1.0, args.preview / max(out_w, out_h))
        preview = Image.new("RGB", (max(1, round(out_w * pscale)), max(1, round(out_h * pscale))))
        py = 0

    writer = StreamingPNG(out, out_w, out_h, args.compression)
    bg = THEMES[args.theme]["bg"]
    t0 = time.time()
    with sync_playwright() as pw:
        browser = launch_browser(pw, args)
        # Viewport is a little larger than a tile so fractional scales never leave seams.
        ctx = browser.new_context(viewport={"width": tile_css + 4, "height": tile_css + 4},
                                  device_scale_factor=scale)
        pg = ctx.new_page()
        pg.set_default_timeout(args.timeout * 1000)
        pg.goto(html_path.as_uri(), wait_until="load")
        pg.evaluate("window.__ready")
        print(f"  page loaded in {time.time() - t0:.1f}s", file=sys.stderr)

        for r in range(rows):
            y_css = r * tile_css
            y_px = round(y_css * scale)
            strip_h = min(out_h, round((y_css + tile_css) * scale)) - y_px
            strip = Image.new("RGB", (out_w, strip_h), bg)
            for c in range(cols):
                x_css = c * tile_css
                x_px = round(x_css * scale)
                pg.evaluate(
                    "([x, y]) => new Promise(res => {"
                    " document.getElementById('world').style.transform = `translate(${-x}px, ${-y}px)`;"
                    " requestAnimationFrame(() => requestAnimationFrame(res)); })",
                    [x_css, y_css],
                )
                shot = Image.open(io.BytesIO(pg.screenshot(type="png", animations="disabled"))).convert("RGB")
                tile_w = min(out_w, round((x_css + tile_css) * scale)) - x_px
                shot = shot.crop((0, 0, min(tile_w, shot.width), min(strip_h, shot.height)))
                strip.paste(shot, (x_px, 0))
                if tiles_dir:
                    shot.save(tiles_dir / f"tile_r{r:04d}_c{c:04d}.png")
            writer.write_strip(strip)
            if preview is not None:
                ph = round((y_px + strip_h) * pscale) - py
                if ph > 0:
                    preview.paste(strip.resize((preview.width, ph), Image.LANCZOS), (0, py))
                    py += ph
            done = (r + 1) / rows
            eta = (time.time() - t0) / done * (1 - done)
            print(f"  row {r + 1}/{rows} ({done:.0%}), ETA {eta:.0f}s", file=sys.stderr)
        browser.close()

    writer.close()
    if not args.keep_html:
        html_path.unlink(missing_ok=True)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:,.1f} MB) in {time.time() - t0:.0f}s", file=sys.stderr)
    if preview is not None:
        ppath = out.with_name(out.stem + ".preview.png")
        preview.save(ppath, optimize=True)
        print(f"Wrote {ppath} ({preview.width}x{preview.height})", file=sys.stderr)


# PDF viewers (Acrobat, Preview) cap a page at 200 x 200 inches = 19,200 CSS px.
PDF_MAX_CSS = 19200


def launch_browser(pw, args):
    launch = {"args": ["--disable-gpu", "--force-color-profile=srgb", "--allow-file-access-from-files"]}
    if args.chromium:
        launch["executable_path"] = args.chromium
    return pw.chromium.launch(**launch)


def render_pdf(args, out: Path, html_path: Path, css_w: int, css_h: int) -> None:
    """Print the canvas as a single-page vector PDF: sharp at any zoom, small file."""
    from playwright.sync_api import sync_playwright

    fit = min(1.0, PDF_MAX_CSS / max(css_w, css_h))
    page_w, page_h = math.ceil(css_w * fit), math.ceil(css_h * fit)
    if fit < 1:
        print(f"Scaling the page by {fit:.3f} to stay within the 200-inch PDF page limit; "
              f"text is vector, so it stays sharp when you zoom in.", file=sys.stderr)
    t0 = time.time()
    with sync_playwright() as pw:
        browser = launch_browser(pw, args)
        pg = browser.new_page(viewport={"width": 1280, "height": 1024})
        pg.set_default_timeout(args.timeout * 1000)
        pg.goto(html_path.as_uri(), wait_until="load")
        pg.evaluate("window.__ready")
        pg.add_style_tag(content=(
            f"@page {{ size: {page_w}px {page_h}px; margin: 0; }}"
            f"html, body {{ width: {page_w}px; height: {page_h}px; overflow: hidden;"
            f" -webkit-print-color-adjust: exact; print-color-adjust: exact; }}"
            f"#world {{ zoom: {fit}; }}"
        ))
        print(f"  page loaded in {time.time() - t0:.1f}s, printing PDF...", file=sys.stderr)
        # Stream the PDF out through the DevTools protocol. Playwright's page.pdf()
        # returns it as one string, which fails for PDFs over a few hundred MB.
        cdp = pg.context.new_cdp_session(pg)
        res = cdp.send("Page.printToPDF", {
            "paperWidth": page_w / 96, "paperHeight": page_h / 96,
            "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0,
            "printBackground": True, "preferCSSPageSize": True, "pageRanges": "1",
            "transferMode": "ReturnAsStream",
        })
        import base64
        with open(out, "wb") as f:
            while True:
                chunk = cdp.send("IO.read", {"handle": res["stream"], "size": 16 << 20})
                data = chunk.get("data", "")
                f.write(base64.b64decode(data) if chunk.get("base64Encoded") else data.encode("latin-1"))
                if chunk.get("eof"):
                    break
        cdp.send("IO.close", {"handle": res["stream"]})
        browser.close()
    if not args.keep_html:
        html_path.unlink(missing_ok=True)
    print(f"Wrote {out} ({out.stat().st_size / 1e6:,.1f} MB) in {time.time() - t0:.0f}s", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("canvas", help="path to the .canvas file")
    ap.add_argument("-o", "--output", help="output .png or .pdf (default: PNG next to the canvas)")
    ap.add_argument("--vault", help="vault root (default: nearest folder containing .obsidian)")
    ap.add_argument("-s", "--scale", type=float, default=2.0,
                    help="pixels per canvas unit (2 = retina-sharp, 1 = 100%% zoom)")
    ap.add_argument("--pdf", action="store_true",
                    help="write a single-page vector PDF instead of a PNG (also chosen by an -o ending in .pdf); "
                         "text stays sharp at any zoom and the file is far smaller")
    ap.add_argument("--max-image-px", type=int, default=2000,
                    help="shrink embedded images to at most this many pixels on the longest side "
                         "(keeps PDFs small and PNG renders fast; 0 keeps originals)")
    ap.add_argument("--theme", choices=THEMES, default="light")
    ap.add_argument("--padding", type=int, default=80, help="margin around the content, in canvas units")
    ap.add_argument("--dots", action="store_true", help="draw Obsidian's dotted background grid")
    ap.add_argument("--tile", type=int, default=2048, help="tile size in output pixels")
    ap.add_argument("--preview", type=int, default=4000,
                    help="also write a downscaled preview with this longest side (0 disables)")
    ap.add_argument("--tiles-dir", help="also save every rendered tile into this folder")
    ap.add_argument("--compression", type=int, default=6, choices=range(0, 10), metavar="0-9",
                    help="PNG zlib level (lower = faster, larger file)")
    ap.add_argument("--offline", action="store_true",
                    help="do not load KaTeX/Mermaid from the CDN (maths and diagrams stay as source)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds to wait for the page to load")
    ap.add_argument("--chromium", help="path to a Chromium/Chrome executable (default: Playwright's)")
    ap.add_argument("--keep-html", action="store_true", help="keep the generated HTML next to the PNG")
    ap.add_argument("--html-only", action="store_true",
                    help="only write the HTML replica (open it in any browser to check it)")
    ap.add_argument("--dry-run", action="store_true", help="only report the output dimensions")
    args = ap.parse_args(argv)
    if args.html_only:
        args.keep_html = True
    render(args)


if __name__ == "__main__":
    main()
