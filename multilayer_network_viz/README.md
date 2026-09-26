# Macro, meso and micro networks as linked spheres

Draws the three layers of the layered edge list as fuzzy 3D blobs of actors,
joined by thin flows for the actors that appear in more than one layer.

- **Blue**: macro network. **Green**: meso network. **Pink**: micro network.
- **Gold lines**: actors flagged `Triple_layer = yes`. Each one runs through
  that actor in all three blobs.
- **Faint lines**: actors flagged in two layers only (`Intersection Type`).
  Teal is macro–meso, orange is meso–micro and violet is macro–micro. The
  violet lines arc over the meso blob.

## How it is built

1. Node names lose their layer suffix (`_Macro`, `_Macro Network` and so on),
   so the same actor matches across layers. One spelling variant is merged:
   *Alliance of Internationalist Feminists* becomes *Alliance International
   Feminist*. Rows with an empty target are kept as isolated actors.
2. Each layer's actors are scattered at random in a ball, densest at the
   centre. There is no network layout, so **positions carry no meaning**.
   Actors that bridge layers are kept on the half facing the viewer so their
   lines stay visible.
3. Actor size and brightness scale with degree within the layer. Depth cueing
   makes actors at the back of a blob smaller and dimmer.
4. Blob radius scales with the cube root of the actor count, so blob volume is
   proportional to network size.
5. Each flow is a thin, gently bowed curve from an actor in one blob to the
   same actor in another. The lines are not bundled, so they cross and weave
   into a web between the blobs.

## Use

```bash
python3 -m pip install -r requirements.txt
python3 render_layers.py edges.csv -o figures/multilayer_network_dark
python3 render_layers.py edges.csv -o figures/multilayer_network_light --light
```

Each run writes a PNG (600 dpi by default) and a PDF. The dense node and edge
layers in the PDF are rasterised, and the text and flows stay as vectors.
A run takes a few seconds. `--seed` gives a different but reproducible
placement. `--no-labels` leaves out the captions and legend.
