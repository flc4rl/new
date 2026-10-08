#!/usr/bin/env python3
"""Extract everything on an Obsidian canvas into one Markdown (or JSON) file.

For every card on the canvas this collects, in reading order and grouped by
the canvas groups:

- text cards and notes: their full Markdown text (plus front matter);
- images: the text inside them (OCR), the date taken and camera (EXIF),
  and optionally GPS coordinates;
- videos: duration, recording date, on-screen text from sampled frames (OCR)
  and, optionally, a transcript of the speech;
- PDFs: the full text of every page (OCR for scanned pages);
- web links: the URL;
- images and notes embedded inside text cards and notes;
- connections (arrows) between cards with their labels;
- a timeline of every card whose name contains a date.

OCR uses Apple's Vision framework on macOS (the engine behind Live Text) and
falls back to Tesseract elsewhere. Results are cached, so re-runs are fast.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from canvas_export import (
    EMBED, FRONTMATTER, IMAGE_EXTS, MD_IMAGE, VIDEO_EXTS, Vault, card_name, extract_subpath,
    find_ffmpeg, find_vault_root,
)

Image.MAX_IMAGE_PIXELS = None

COLOUR_NAMES = {"1": "red", "2": "orange", "3": "yellow", "4": "green", "5": "cyan", "6": "purple"}

DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)"), ("y", "m", "d")),
    (re.compile(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](\d{4})(?!\d)"), ("d", "m", "y")),
    (re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"), ("y", "m", "d")),
]


def find_date(text: str) -> dt.date | None:
    for pattern, order in DATE_PATTERNS:
        for m in pattern.finditer(text):
            parts = dict(zip(order, (int(g) for g in m.groups())))
            try:
                return dt.date(parts["y"], parts["m"], parts["d"])
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# OCR engines
# --------------------------------------------------------------------------

class OCR:
    """Text recognition: Apple Vision on macOS, otherwise Tesseract."""

    def __init__(self, engine: str = "auto", languages: str = "en,fr,it,pt,es,de"):
        self.languages = [l.strip() for l in languages.split(",") if l.strip()]
        self.name = None
        if engine in ("auto", "vision") and sys.platform == "darwin":
            try:
                import Vision  # noqa: F401  (pyobjc-framework-Vision)
                self.name = "vision"
            except ImportError:
                if engine == "vision":
                    raise SystemExit("Apple Vision needs: python -m pip install pyobjc-framework-Vision")
        if self.name is None and engine in ("auto", "tesseract"):
            try:
                import pytesseract
                available = set(pytesseract.get_languages(config=""))
                iso3 = {"de": "deu", "en": "eng", "fr": "fra", "it": "ita", "es": "spa", "nl": "nld",
                        "pt": "por", "pl": "pol", "tr": "tur", "ru": "rus", "ar": "ara"}
                langs = [iso3.get(l, l) for l in self.languages if iso3.get(l, l) in available]
                self.tess_lang = "+".join(langs or ["eng"])
                self.name = "tesseract"
            except Exception:
                if engine == "tesseract":
                    raise SystemExit("Tesseract not found: install it (brew install tesseract tesseract-lang) "
                                     "and python -m pip install pytesseract")
        if self.name is None and engine != "none":
            print("WARNING: no OCR engine found; text inside images will not be extracted.\n"
                  "  macOS: python -m pip install pyobjc-framework-Vision", file=sys.stderr)

    def __call__(self, path: Path) -> str:
        if self.name is None:
            return ""
        try:
            if self.name == "vision":
                return self._vision(path)
            return self._tesseract(path)
        except Exception as e:  # one unreadable image must not stop the run
            print(f"  OCR failed for {path.name}: {e}", file=sys.stderr)
            return ""

    def _vision(self, path: Path) -> str:
        import Vision
        from Foundation import NSURL

        def run(p: Path, with_languages: bool = True):
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
                NSURL.fileURLWithPath_(str(p)), {})
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            req.setUsesLanguageCorrection_(True)
            if with_languages:
                langs = [{"de": "de-DE", "en": "en-US", "fr": "fr-FR", "it": "it-IT", "es": "es-ES",
                          "pt": "pt-BR", "nl": "nl-NL"}.get(l, l) for l in self.languages]
                req.setRecognitionLanguages_(langs)
                # macOS 13+: pick the language per image instead of assuming the first one.
                if hasattr(req, "setAutomaticallyDetectsLanguage_"):
                    req.setAutomaticallyDetectsLanguage_(True)
            ok, _ = handler.performRequests_error_([req], None)
            return list(req.results() or []) if ok else None

        results = run(path)
        if results is None:  # older macOS may reject a language: let Vision choose
            results = run(path, with_languages=False)
        if results is None:  # a format Vision cannot read: convert it first
            with tempfile.TemporaryDirectory() as tmp:
                png = Path(tmp) / "img.png"
                with Image.open(path) as im:
                    im.convert("RGB").save(png)
                results = run(png) or run(png, with_languages=False) or []
        lines = []
        for obs in results:
            cand = obs.topCandidates_(1)
            if not cand or cand[0].confidence() < 0.3:
                continue
            box = obs.boundingBox()  # normalised, origin bottom-left
            lines.append((1 - (box.origin.y + box.size.height / 2), box.origin.x,
                          box.size.height, str(cand[0].string())))
        return join_lines(lines)

    def _tesseract(self, path: Path) -> str:
        import pytesseract
        from PIL import ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if max(im.size) < 1400:  # small screenshots read better enlarged
                f = 1400 / max(im.size)
                im = im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
            text = pytesseract.image_to_string(im, lang=self.tess_lang)
            # Tesseract favours the first language; re-read with the detected one first,
            # which restores accents (à, ù, ü) that a mixed first pass tends to drop.
            langs = self.tess_lang.split("+")
            best = guess_language(text)
            if best and best in langs and best != langs[0]:
                text = pytesseract.image_to_string(im, lang="+".join([best] + [l for l in langs if l != best]))
        return clean_text(text)


STOPWORDS = {
    "eng": "the and of to in is that for on with you this are it be at we our not your",
    "fra": "le la les des et est une un pour dans que qui sur pas nous vous du au avec ce",
    "ita": "il di che la per non una sono della del le gli con ma anche più è al nel",
    "por": "de que não uma os as para com por mais nossa nosso dos das ao é à também",
    "spa": "el la los las que de del por para una con es no y en se lo más al está",
    "deu": "der die das und ist nicht ein eine einen zu mit für auf den dem wir sie ich im am "
           "ohne von zum zur auch sich um bei nach aus über",
}
STOPWORDS = {k: set(v.split()) for k, v in STOPWORDS.items()}


def guess_language(text: str) -> str | None:
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < 3:
        return None
    scores = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else None


def join_lines(lines: list[tuple[float, float, float, str]]) -> str:
    """Order recognised text boxes top-to-bottom, left-to-right, merging rows."""
    lines.sort()
    rows: list[list[tuple]] = []
    for item in lines:
        if rows and abs(item[0] - rows[-1][0][0]) < max(item[2], rows[-1][0][2]) * 0.5:
            rows[-1].append(item)
        else:
            rows.append([item])
    return clean_text("\n".join("  ".join(t[3] for t in sorted(r, key=lambda t: t[1])) for r in rows))


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Cache (OCR and transcription are slow; re-runs reuse earlier results)
# --------------------------------------------------------------------------

class Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def key(self, kind: str, file: Path, extra: str = "") -> str:
        st = file.stat()
        return hashlib.sha1(f"{kind}|{file}|{st.st_size}|{st.st_mtime_ns}|{extra}".encode()).hexdigest()

    def get_or(self, kind: str, file: Path, extra: str, fn):
        try:
            k = self.key(kind, file, extra)
        except OSError:
            return fn()
        if k not in self.data:
            self.data[k] = fn()
            self.save()
        return self.data[k]

    def save(self):
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)


# --------------------------------------------------------------------------
# Per-file extractors
# --------------------------------------------------------------------------

def image_metadata(path: Path, gps: bool) -> dict:
    meta = {}
    try:
        with Image.open(path) as im:
            meta["size"] = f"{im.width}x{im.height}"
            exif = im.getexif()
            sub = exif.get_ifd(0x8769)
            taken = sub.get(0x9003) or exif.get(0x0132)
            if taken:
                meta["taken"] = str(taken).replace(":", "-", 2)
            camera = " ".join(str(v).strip() for v in (exif.get(0x010F), exif.get(0x0110)) if v)
            if camera:
                meta["camera"] = camera
            if gps:
                g = exif.get_ifd(0x8825)
                if g.get(2) and g.get(4):
                    def deg(v, ref):
                        d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                        return -d if ref in ("S", "W") else d
                    meta["gps"] = f"{deg(g[2], g.get(1)):.6f}, {deg(g[4], g.get(3)):.6f}"
    except Exception:
        pass
    return meta


def video_info(path: Path) -> dict:
    exe = find_ffmpeg()
    if not exe:
        return {}
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       errors="replace", timeout=60)
    info = {}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if m:
        info["duration_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"creation_time\s*:\s*(\S+)", r.stderr)
    if m:
        info["recorded"] = m.group(1).replace("T", " ").rstrip("Z").split(".")[0]
    info["has_audio"] = "Audio:" in r.stderr
    return info


def video_frames_text(path: Path, duration: float, n: int, ocr: OCR) -> str:
    """OCR n evenly spaced frames and keep each distinct line once, in order."""
    exe = find_ffmpeg()
    if not exe or not ocr.name or n <= 0:
        return ""
    times = [duration * (i + 0.5) / n for i in range(n)] if duration else [0.0]
    seen, out = set(), []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            frame = Path(tmp) / f"f{i}.png"
            subprocess.run([exe, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            str(frame)], capture_output=True, timeout=120)
            if not frame.exists():
                continue
            for line in ocr(frame).splitlines():
                norm = re.sub(r"\W+", "", line.lower())
                if norm and norm not in seen:
                    seen.add(norm)
                    out.append(line)
    return "\n".join(out)


class Transcriber:
    def __init__(self, model: str, language: str | None):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise SystemExit("Transcription needs: python -m pip install faster-whisper")
        print(f"Loading speech model '{model}' (downloaded once on first use)...", file=sys.stderr)
        self.model = WhisperModel(model, device="cpu", compute_type="int8")
        self.language = language

    def __call__(self, path: Path) -> str:
        try:
            segments, info = self.model.transcribe(str(path), language=self.language, vad_filter=True)
            lines = []
            for s in segments:
                m, sec = divmod(int(s.start), 60)
                lines.append(f"[{m:02d}:{sec:02d}] {s.text.strip()}")
            return "\n".join(lines)
        except Exception as e:
            print(f"  transcription failed for {path.name}: {e}", file=sys.stderr)
            return ""


def pdf_text(path: Path, ocr: OCR) -> list[str]:
    import pymupdf
    pages = []
    with pymupdf.open(path) as doc:
        for page in doc:
            text = page.get_text().strip()
            if len(text) < 20 and ocr.name:  # scanned page: OCR it
                with tempfile.TemporaryDirectory() as tmp:
                    png = Path(tmp) / "p.png"
                    page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(png)
                    text = ocr(png) or text
            pages.append(clean_text(text))
    return pages


# --------------------------------------------------------------------------
# Canvas walk
# --------------------------------------------------------------------------

def centre(n):
    return n["x"] + n["width"] / 2, n["y"] + n["height"] / 2


def contains(g, n):
    cx, cy = centre(n)
    return g["x"] <= cx <= g["x"] + g["width"] and g["y"] <= cy <= g["y"] + g["height"]


def card_title(n: dict) -> str:
    t = n.get("type", "text")
    if t == "file":
        return Path(n.get("file", "")).name or "(file)"
    if t == "group":
        return n.get("label") or "(unnamed group)"
    if t == "link":
        return n.get("url", "(link)")
    text = FRONTMATTER.sub("", n.get("text", "")).strip()
    first = next((l for l in text.splitlines() if l.strip()), "")
    first = re.sub(r"^#+\s*|[*_`>\[\]!]", "", first).strip()
    return (first[:80] + "…") if len(first) > 80 else (first or "(empty text card)")


class Extractor:
    def __init__(self, args, vault: Vault):
        self.args, self.vault = args, vault
        self.ocr = OCR(args.ocr, args.languages)
        cache_path = None if args.no_cache else Path(args.output).with_suffix(".cache.json")
        self.cache = Cache(cache_path)
        self.transcriber = None
        if args.transcribe:
            try:
                self.transcriber = Transcriber(args.whisper_model, args.speech_language)
            except SystemExit:
                raise
            except Exception as e:
                print(f"WARNING: could not load the speech model ({e}); continuing without transcripts.",
                      file=sys.stderr)

    def ocr_cached(self, path: Path) -> str:
        return self.cache.get_or("ocr", path, f"{self.ocr.name}|{self.args.languages}", lambda: self.ocr(path))

    def image(self, path: Path) -> dict:
        d = {"kind": "image", "file": self.rel(path), **image_metadata(path, self.args.gps)}
        d["text_in_image"] = self.ocr_cached(path)
        return d

    def video(self, path: Path) -> dict:
        info = video_info(path)
        d = {"kind": "video", "file": self.rel(path), **{k: v for k, v in info.items() if k != "has_audio"}}
        d["on_screen_text"] = self.cache.get_or(
            "frames", path, f"{self.ocr.name}|{self.args.languages}|{self.args.video_frames}",
            lambda: video_frames_text(path, info.get("duration_s", 0), self.args.video_frames, self.ocr))
        if self.transcriber and info.get("has_audio", True):
            d["transcript"] = self.cache.get_or(
                "speech", path, f"{self.args.whisper_model}|{self.args.speech_language}",
                lambda: self.transcriber(path))
        return d

    def pdf(self, path: Path) -> dict:
        return {"kind": "pdf", "file": self.rel(path),
                "pages": self.cache.get_or("pdf", path, f"{self.ocr.name}", lambda: pdf_text(path, self.ocr))}

    def note_text(self, text: str, base: Path | None, depth: int = 0) -> dict:
        """Markdown plus everything it embeds (images, videos, PDFs, other notes)."""
        d: dict = {}
        fm = FRONTMATTER.match(text)
        if fm:
            d["properties"] = fm.group(0).strip().strip("-").strip()
        d["text"] = FRONTMATTER.sub("", text).strip()
        embeds = []
        targets = [(m.group(1), m.group(2) or "") for m in EMBED.finditer(text)]
        targets += [(m.group(2).replace("%20", " "), "") for m in MD_IMAGE.finditer(text)]
        for target, sub in targets:
            path = self.vault.resolve(target) if target else base
            if path is None or path == base:
                continue
            e = self.file(path, sub, depth + 1)
            if e:
                embeds.append(e)
        if embeds:
            d["embedded"] = embeds
        return d

    def file(self, path: Path, subpath: str = "", depth: int = 0) -> dict | None:
        ext = path.suffix.lower()
        try:
            if ext in IMAGE_EXTS or ext in {".heic", ".heif", ".tif", ".tiff"}:
                return self.image(path)
            if ext in VIDEO_EXTS:
                return self.video(path)
            if ext == ".pdf":
                return self.pdf(path)
            if ext == ".md":
                if depth > 2:
                    return {"kind": "note", "file": self.rel(path)}
                text = extract_subpath(path.read_text(encoding="utf-8", errors="replace"), subpath)
                return {"kind": "note", "file": self.rel(path), **self.note_text(text, path, depth)}
            return {"kind": ext.lstrip(".") or "file", "file": self.rel(path)}
        except Exception as e:
            print(f"  could not read {path.name}: {e}", file=sys.stderr)
            return {"kind": "unreadable", "file": self.rel(path), "error": str(e)}

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.vault.root))
        except ValueError:
            return str(path)

    def card(self, n: dict) -> dict:
        t = n.get("type", "text")
        d = {"id": n.get("id"), "type": t, "title": card_title(n)}
        if n.get("color"):
            d["colour"] = COLOUR_NAMES.get(str(n["color"]), n["color"])
        date = find_date(d["title"])
        if date:
            d["date"] = date.isoformat()
        if t == "text":
            d.update(self.note_text(n.get("text", ""), None))
        elif t == "link":
            d["url"] = n.get("url", "")
        elif t == "file":
            path = self.vault.resolve(n.get("file", ""))
            if path is None:
                d["missing"] = n.get("file", "")
            else:
                d["content"] = self.file(path, n.get("subpath", ""))
        return d


def extract(args) -> dict:
    canvas_path = Path(args.canvas).expanduser().resolve()
    vault = Vault(Path(args.vault).expanduser() if args.vault else find_vault_root(canvas_path))
    canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
    nodes, edges = canvas.get("nodes") or [], canvas.get("edges") or []
    if args.exclude:
        prefixes = tuple(p.lower() for p in args.exclude)
        dropped = {n.get("id") for n in nodes if card_name(n).lower().startswith(prefixes)}
        nodes = [n for n in nodes if n.get("id") not in dropped]
        edges = [e for e in edges if e.get("fromNode") not in dropped and e.get("toNode") not in dropped]
        print(f"Excluded {len(dropped)} cards", file=sys.stderr)
    print(f"Canvas: {canvas_path}\nVault:  {vault.root}", file=sys.stderr)

    ex = Extractor(args, vault)
    print(f"OCR engine: {ex.ocr.name or 'none'}", file=sys.stderr)

    # Group hierarchy: each node belongs to the smallest group containing its centre.
    groups = [n for n in nodes if n.get("type") == "group"]
    by_area = sorted(groups, key=lambda g: g["width"] * g["height"])
    parent = {}
    for n in nodes:
        parent[n["id"]] = next((g["id"] for g in by_area if g is not n and contains(g, n)
                                and g["width"] * g["height"] > n["width"] * n["height"]), None)
    children: dict = {}
    for n in nodes:
        children.setdefault(parent[n["id"]], []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: (round(n["y"] / 50), n["x"]))  # reading order, rows of ~50 units

    titles = {n["id"]: card_title(n) for n in nodes}
    total = sum(1 for n in nodes if n.get("type") != "group")
    done = [0]

    def walk(group_id):
        out = []
        for n in children.get(group_id, []):
            if n.get("type") == "group":
                g = {"id": n["id"], "type": "group", "title": titles[n["id"]], "cards": walk(n["id"])}
                if n.get("color"):
                    g["colour"] = COLOUR_NAMES.get(str(n["color"]), n["color"])
                out.append(g)
            else:
                done[0] += 1
                print(f"  [{done[0]}/{total}] {titles[n['id']]}", file=sys.stderr)
                c = ex.card(n)
                c["links_to"] = [{"to": titles.get(e.get("toNode"), "?"), **({"label": e["label"]} if e.get("label") else {})}
                                 for e in edges if e.get("fromNode") == n["id"]]
                c["linked_from"] = [{"from": titles.get(e.get("fromNode"), "?"), **({"label": e["label"]} if e.get("label") else {})}
                                    for e in edges if e.get("toNode") == n["id"]]
                for k in ("links_to", "linked_from"):
                    if not c[k]:
                        del c[k]
                out.append(c)
        return out

    tree = walk(None)
    ex.cache.save()
    return {
        "canvas": canvas_path.stem,
        "source": str(canvas_path),
        "extracted": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ocr_engine": ex.ocr.name,
        "counts": {
            "cards": total, "groups": len(groups), "connections": len(edges),
        },
        "cards": tree,
        "connections": [{"from": titles.get(e.get("fromNode"), "?"), "to": titles.get(e.get("toNode"), "?"),
                         **({"label": e["label"]} if e.get("label") else {})} for e in edges],
    }


# --------------------------------------------------------------------------
# Markdown output
# --------------------------------------------------------------------------

def quote(text: str) -> str:
    return "\n".join("> " + l if l.strip() else ">" for l in text.splitlines())


def fmt_duration(s: float) -> str:
    m, s = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def md_content(c: dict, level: int) -> list[str]:
    """Markdown for a file's content (image/video/pdf/note), used for cards and embeds."""
    out = []
    kind = c.get("kind")
    meta = []
    for key, label in (("file", "File"), ("taken", "Taken"), ("recorded", "Recorded"), ("camera", "Camera"),
                       ("gps", "GPS"), ("size", "Size")):
        if c.get(key):
            meta.append(f"- **{label}:** {c[key]}")
    if c.get("duration_s"):
        meta.append(f"- **Duration:** {fmt_duration(c['duration_s'])}")
    if c.get("error"):
        meta.append(f"- **Error:** {c['error']}")
    out += meta
    if kind == "image":
        out += ["", "**Text in image:**", "", quote(c["text_in_image"]) if c.get("text_in_image") else "_(no text found)_"]
    elif kind == "video":
        out += ["", "**On-screen text:**", "", quote(c["on_screen_text"]) if c.get("on_screen_text") else "_(no text found)_"]
        if "transcript" in c:
            out += ["", "**Transcript:**", "", quote(c["transcript"]) if c["transcript"] else "_(no speech found)_"]
    elif kind == "pdf":
        for i, page in enumerate(c.get("pages", []), 1):
            out += ["", f"**Page {i}:**", "", quote(page) if page else "_(empty)_"]
    elif kind == "note":
        out += md_note(c, level)
    return out


def md_note(c: dict, level: int) -> list[str]:
    out = []
    if c.get("properties"):
        out += ["", "**Properties:**", "", "```yaml", c["properties"], "```"]
    if c.get("text"):
        out += ["", quote(c["text"])]
    for e in c.get("embedded", []):
        out += ["", f"{'#' * min(level + 1, 6)} Embedded: {Path(e.get('file', '')).name}", ""]
        out += md_content(e, level + 1)
    return out


def to_markdown(data: dict) -> str:
    out = [f"# {data['canvas']}", "",
           f"Extracted from `{data['source']}` on {data['extracted']}. "
           f"{data['counts']['cards']} cards, {data['counts']['groups']} groups, "
           f"{data['counts']['connections']} connections. OCR: {data['ocr_engine'] or 'none'}.", ""]

    def card(c: dict, level: int):
        h = "#" * min(level, 6)
        if c["type"] == "group":
            out.extend([f"{h} Group: {c['title']}", ""])
            if c.get("colour"):
                out.extend([f"- **Colour:** {c['colour']}", ""])
            for k in c["cards"]:
                card(k, level + 1)
            return
        out.append(f"{h} {c['title']}")
        out.append("")
        meta = [f"- **Type:** {c['type'] if c['type'] != 'file' else (c.get('content') or {}).get('kind', 'file')}"]
        if c.get("date"):
            meta.append(f"- **Date:** {c['date']}")
        if c.get("colour"):
            meta.append(f"- **Colour:** {c['colour']}")
        if c.get("url"):
            meta.append(f"- **URL:** {c['url']}")
        if c.get("missing"):
            meta.append(f"- **Missing file:** {c['missing']}")
        for e in c.get("links_to", []):
            meta.append(f"- **→ {e['to']}**" + (f" ({e['label']})" if e.get("label") else ""))
        for e in c.get("linked_from", []):
            meta.append(f"- **← {e['from']}**" + (f" ({e['label']})" if e.get("label") else ""))
        out.extend(meta)
        if c["type"] == "text":
            out.extend(md_note(c, level))
        elif c.get("content"):
            content = dict(c["content"])
            out.extend(md_content(content, level))
        out.append("")

    for c in data["cards"]:
        card(c, 2)

    dated = []

    def collect(cs):
        for c in cs:
            if c["type"] == "group":
                collect(c["cards"])
            elif c.get("date"):
                dated.append(c)
    collect(data["cards"])
    if dated:
        out += ["## Timeline", ""]
        for c in sorted(dated, key=lambda c: c["date"]):
            out.append(f"- **{c['date']}**: {c['title']}")
        out.append("")
    if data["connections"]:
        out += ["## All connections", ""]
        for e in data["connections"]:
            out.append(f"- {e['from']} → {e['to']}" + (f" ({e['label']})" if e.get("label") else ""))
        out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("canvas", help="path to the .canvas file")
    ap.add_argument("-o", "--output", help="output .md (default) or .json; default: next to this tool")
    ap.add_argument("--vault", help="vault root (default: nearest folder containing .obsidian)")
    ap.add_argument("--ocr", choices=["auto", "vision", "tesseract", "none"], default="auto")
    ap.add_argument("--languages", default="en,fr,it,pt,es,de",
                    help="OCR languages, most common first (en, fr, it, pt, es, de, nl, ...)")
    ap.add_argument("--video-frames", type=int, default=6,
                    help="frames per video to read on-screen text from (0 = none)")
    ap.add_argument("--transcribe", action="store_true",
                    help="also transcribe speech in videos (needs: pip install faster-whisper)")
    ap.add_argument("--whisper-model", default="small",
                    help="speech model: tiny, base, small, medium, large-v3 (bigger = better, slower)")
    ap.add_argument("--speech-language", default=None, help="e.g. de; default: detect per video")
    ap.add_argument("--gps", action="store_true", help="include GPS coordinates from photos")
    ap.add_argument("--exclude", action="append", default=[], metavar="PREFIX",
                    help="leave out cards whose file name starts with PREFIX")
    ap.add_argument("--no-cache", action="store_true", help="do not reuse earlier OCR/transcription results")
    args = ap.parse_args(argv)
    if not args.output:
        args.output = Path(args.canvas).stem + " - extracted.md"
    out = Path(args.output).expanduser().resolve()
    args.output = str(out)

    data = extract(args)
    if out.suffix.lower() == ".json":
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        out.write_text(to_markdown(data), encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size / 1e6:,.1f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
