# Figure 3.y: Operationalising relations

One row of four panels that carries a single small network through the four
moves of the method. Panels 1 to 3 share their node positions, so each panel
adds one move and nothing else changes:

1. **What is a node?** Each node is a loose cluster of instances inside a
   soft, dashed, slightly irregular boundary. Its name hangs outside as a grey
   tab: an index, not a property.
2. **What is a tie?** Ties are typed by the relational function they carry:
   generative (teal, solid) or extractive (orange, dashed). Each qualified tie
   carries three small tabs for the interview, ethnographic and event evidence
   that qualifies it. Grey ties stay unqualified.
3. **Where do categories sit?** One band, *assignment by the order*. Two
   generative ties cross it and meet in a new node. One extractive tie runs
   along its edge and another stops at it.
4. **What must persist over time?** The same chain of three generative ties at
   T1 and T2. The instances inside the nodes differ, one node fades and a new
   one joins.

A line under the panels states what can be claimed.

![Figure 3.y](output/fig3y_operationalising_relations.png)

## Files

| File | Use |
|---|---|
| `output/fig3y_operationalising_relations.pdf` | vector, for LaTeX |
| `output/fig3y_operationalising_relations.svg` | vector, editable; text kept as text |
| `output/fig3y_operationalising_relations.png` | 300 dpi raster, for Word |
| `make_figure.py` | regenerates all three |

```bash
pip install -r requirements.txt
python make_figure.py            # writes into ./output
```

Print size 16 × 8 cm (1600 × 800 px design canvas, 1 px = 0.1 mm). Text is
8 pt or larger, and headers are 9.5 pt bold, in Liberation Sans, which has the
same letter widths as Arial. The script fails if any text block overlaps
another, leaves the canvas or drops below 8 pt.

The two generative ties in panel 3 run through the word gaps of the band
label, so the label never covers them. The new node's position and the label
centre (`NEW`, `LABEL_CX`) were found by a small search for the position
where straight ties from the two upper nodes pass through those gaps. If you
move nodes in panel 3, search again for these two values.

## Caption

> Figure 3.y. Operationalising relations. (1) Nodes are structured tendencies
> identified through instances; their names serve only as an index. (2) Ties
> are qualified by the relational function they carry, generative or
> extractive, on the evidence of interviews, ethnography and events; some ties
> remain unqualified. (3) Categories enter as operations: relations cross the
> assignment by the order, reproduce it or stop at it. (4) What must persist
> for a claim to hold is the relational function, while units and their
> instances change. Schematic illustration, not a plot of the data.

## Alt text

> Four panels carry one small network through the steps of the method. First,
> each node is a loose cluster of dots, its instances, inside a soft dashed
> boundary, with its name on a small grey tab outside. Second, ties between
> the clusters are typed: a solid teal generative tie and a dashed orange
> extractive tie each carry a small mark for the interviews, ethnography and
> events that qualify them, and thin grey ties stay unqualified. Third, a
> single shaded band marks assignment by the order: two generative ties cross
> it and meet in a new node, one extractive tie runs along its edge and
> another stops at it. Fourth, the same chain of three generative ties appears
> at two time points while the instances inside the nodes change, one node
> fades and a new node joins.

The alt text is also stored in the metadata of the SVG, PDF and PNG.

## LaTeX

```latex
\begin{figure}[tbp]
  \centering
  \includegraphics[width=16cm]{fig3y_operationalising_relations.pdf}
  \caption[Operationalising relations]{Operationalising relations.
    (1)~Nodes are structured tendencies identified through instances; their
    names serve only as an index. (2)~Ties are qualified by the relational
    function they carry, generative or extractive, on the evidence of
    interviews, ethnography and events; some ties remain unqualified.
    (3)~Categories enter as operations: relations cross the assignment by the
    order, reproduce it or stop at it. (4)~What must persist for a claim to
    hold is the relational function, while units and their instances change.
    Schematic illustration, not a plot of the data.}
  \label{fig:operationalising-relations}
\end{figure}
```
