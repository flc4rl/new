# Figure 3.y: Operationalising relations

Comparative schematic in small multiples (2 rows × 5 columns). It contrasts the
typical operationalisation in social movement network analysis (row A) with the
approach of this thesis (row B).

![Figure 3.y](output/fig3y_operationalising_relations.png)

## Files

| File | Use |
|---|---|
| `output/fig3y_operationalising_relations.pdf` | vector, for LaTeX (`\includegraphics`) |
| `output/fig3y_operationalising_relations.svg` | vector, editable; text is kept as text |
| `output/fig3y_operationalising_relations.png` | 300 dpi raster (1890 × 1122 px), for Word |
| `make_figure.py` | regenerates all three |

```bash
pip install -r requirements.txt
python make_figure.py            # writes into ./output
```

The script fails if a text block overlaps another, runs past the canvas or is
set below 8 pt, so any wording change is checked on the next run.

## Specification as built

- Print size 16 cm × 9.5 cm, designed on a 1600 × 950 px canvas
  (1 px = 0.1 mm), so stroke widths in the spec (1, 3, 6, 3.5, 3, 1.2 px) are
  taken as fractions of a millimetre at print size.
- Liberation Sans, which is metrically identical to Arial. The SVG asks for
  Arial first, and the PDF embeds the font.
- Text sizes at print size: cell labels and footer 8 pt, row labels 8.5 pt
  bold, column 5 text 9 pt, column headers 9.5 pt bold.
- Row A is greyscale only (#D9DDE1, #9AA0A6 fills, #7A8088 ties, black
  outlines). Row B uses generative #1F6F78 solid 3.5 px, extractive #C4622D
  dashed 9/6 px at 3 px, not qualified #A3A8AE 1.2 px, band #E9EDF1.
- The key to the row B encodings sits in the row label margin under
  "This thesis", so column 5 stays text only.

## Caption

> Figure 3.y. Operationalising relations. In the typical operationalisation of
> social movement network analysis (top row), nodes are pre-given units with
> attributes, ties are weighted by the frequency of joint participation, the
> quality of relations is established alongside the network, and comparisons
> across time hold units constant. In this thesis (bottom row), nodes are
> structured tendencies identified through instances, ties are qualified by the
> relational functions they carry, categories enter as operations that
> relations reproduce or refuse, and what must persist for a claim to hold is
> the relational function. Schematic illustration, not a plot of the data.

## Alt text

> Two rows of small diagrams compare how networks are operationalised. The top
> row shows fixed nodes with category fills, ties of varying thickness by
> frequency, and identical nodes at two time points. The bottom row shows nodes
> as clusters of instances, ties typed as generative or extractive, a band
> marking assignment by the order that some ties cross and others reproduce,
> and a recurring pattern of generative ties across changing nodes.

The alt text is also stored in the SVG `<desc>`, the PDF subject and the PNG
metadata.

## Placement

In 3.2, directly after the paragraph on tie strength and brokerage. The
relevance, qualification and significance figure stays in 3.6, so the two work
as a pair.

### LaTeX

```latex
\begin{figure}[tbp]
  \centering
  \includegraphics[width=16cm]{fig3y_operationalising_relations.pdf}
  \caption[Operationalising relations]{Operationalising relations. In the
    typical operationalisation of social movement network analysis (top row),
    nodes are pre-given units with attributes, ties are weighted by the
    frequency of joint participation, the quality of relations is established
    alongside the network, and comparisons across time hold units constant.
    In this thesis (bottom row), nodes are structured tendencies identified
    through instances, ties are qualified by the relational functions they
    carry, categories enter as operations that relations reproduce or refuse,
    and what must persist for a claim to hold is the relational function.
    Schematic illustration, not a plot of the data.}
  \label{fig:operationalising-relations}
\end{figure}
```

Let LaTeX number it (it prints "Figure 3.y" as the next number in chapter 3),
and refer to both figures by label, for example
`Figure~\ref{fig:operationalising-relations}` here and
`Figure~\ref{fig:relevance-significance}` (or whatever label the 3.6 figure
has) in 3.6. Since this figure comes first, it will take the lower number, so
check any hard-coded "Figure 3.x" in the text around 3.6.

### Word

Insert the PNG at 16 cm width (Layout → Size, lock aspect ratio), add the
caption with References → Insert Caption so numbering stays automatic, and
paste the alt text under Format Picture → Alt Text. For vector output in
Word, insert the SVG instead (Word 2016 and later).

## Checks before inserting

| Check | Status |
|---|---|
| 1. Each A/B pair differs in one respect | Col 1: node as unit vs tendency (same three positions). Col 2: same nodes and pairs; only where tie quality sits (box beside the network vs typed ties with source glyphs). Col 3: identical six nodes; only category encoding (fills and a counted bracket vs a band that ties cross or reproduce; the new node is what the crossing produces). Col 4: identical T1 layout; A holds units and changes ties, B holds the teal chain and changes units. Across all columns, row A nodes carry a grey fill and row B nodes are unfilled, as the colour spec requires; in columns 2 and 4 row A uses a single fill, so the category attribute appears only where it is the point. |
| 2. A3 and B3 node positions identical | Yes, both use the shared `C3` coordinates. |
| 3. Labels legible at 16 cm | No text below 8 pt at print size (enforced by the script). |
| 4. Greyscale separates generative from extractive | Yes: solid vs dashed, 3.5 vs 3 px, so the two stay distinct in greyscale and for colour-blind readers. |
| 5. Numbering and cross-references | Use `\label`/`\ref` as above; this figure precedes the 3.6 figure. |

Guardrails: no named study, no hubs, stars or brokers, a single band, no
revealing metaphors, one unqualified grey tie in B2 and in B3, the scope note
and the schematic line printed under the figure.
