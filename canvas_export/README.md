# Obsidian canvas to high-resolution PNG

Exports Obsidian `.canvas` files of any size to one PNG. It handles canvases
with thousands of cards that make in-app exporters (Simple Canvas Exporter,
Canvas HTML Exporter, screenshots) fail.

## Why other exporters fail

They draw the whole canvas into one browser image. Chromium, which Obsidian
runs on, limits a single canvas/bitmap to about 16,384 px per side and about
268 megapixels. A large canvas at a readable zoom goes past that limit, so you
get a blank image, a cropped image, an out-of-memory error or an Obsidian crash.

## How this tool works

1. It reads the `.canvas` JSON and the notes and images it references straight
   from your vault. Obsidian does not need to be open.
2. It builds an HTML copy of the canvas. This includes text cards, embedded
   notes (including `#heading` subpaths), images, links, groups (labels,
   colours, background images), edges (sides, arrows, labels, colours), preset
   and hex colours, and Obsidian Markdown: wikilinks, `![[embeds]]`,
   callouts, tasks, tables, `==highlights==`, `#tags`, `%%comments%%`,
   maths (KaTeX) and Mermaid.
3. Headless Chromium renders the page one tile at a time (2048 px by default).
4. Tiles are joined strip by strip and written straight into a PNG encoder.
   Memory use depends on the canvas width, not the total image size.

Tested with 3,024 nodes and 2,736 edges: a 31,160 × 24,000 px image (748 MP)
took 80 s, and peak memory stayed at a few hundred MB.

## Install (once)

```bash
cd canvas_export
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

## Use

```bash
python3 canvas_export.py "/path/to/Vault/My Board.canvas"
```

This writes `My Board.png` next to the canvas, plus `My Board.preview.png`,
a 4000 px overview. The vault root is detected automatically (the nearest
folder containing `.obsidian`). Use `--vault` to override it.

Useful options:

| Option | Meaning |
| --- | --- |
| `-s / --scale 2` | Pixels per canvas unit. `1` = Obsidian at 100 % zoom, `2` (default) = retina-sharp text, `0.5` = smaller file |
| `-o out.png` | Output path |
| `--theme dark` | Dark theme (default `light`) |
| `--dots` | Draw the dotted canvas background |
| `--dry-run` | Only print the final pixel size |
| `--tiles-dir DIR` | Also save each tile separately (useful for print shops or tiling viewers) |
| `--html-only` | Only write the HTML copy, to check the layout in a browser |
| `--offline` | Do not fetch KaTeX/Mermaid from the CDN (maths and diagrams stay as source text) |
| `--compression 1` | Faster, larger PNG |
| `--chromium PATH` | Use a specific Chrome/Chromium binary instead of Playwright's |

Run `--dry-run` first. At `--scale 2` a 30,000-unit-wide canvas becomes
60,000 px wide. The tool handles that, but many image viewers do not.
For very large results, use a viewer built for huge images (for example
IrfanView, XnView, GIMP, or macOS Preview up to a point), or open the PNG in
a browser. Or lower `--scale`.

## Smaller files: vector PDF

A huge PNG stores every pixel, so a large canvas at readable resolution easily
reaches hundreds of MB. Use `--pdf` (or an output name ending in `.pdf`) to get
a single-page **vector PDF** instead:

```bash
python3 canvas_export.py "/path/to/Board.canvas" --theme dark -o board.pdf
```

- Text and shapes are vectors, so they are sharp at any zoom level.
- The file is usually 10 to 40 times smaller than the PNG. For the test canvas
  it was 10 MB, compared with 28 MB for a PNG at only scale 1.
- The text is real, so you can search it (Cmd/Ctrl+F) and copy from it.
- PDF viewers limit a page to 200 × 200 inches, so large canvases are scaled
  down to fit. Zoom in to read them. `--scale`, `--tile` and `--preview` do
  not apply to PDF output.
- `--dots` works but makes the file roughly twice as large.

Open it in Preview, Acrobat, or a browser.

## Limits

- Cards are clipped to their size, like on the canvas. Scroll-hidden content
  inside a card is not shown, which matches what Obsidian shows.
- Styles from plugins or CSS snippets (for example Dataview output, custom
  callouts, Excalidraw embeds) are not reproduced. Dataview blocks appear as
  their source code. Web link cards show the URL, not the live page.
- Edge curves are an approximation of Obsidian's and may differ slightly.
