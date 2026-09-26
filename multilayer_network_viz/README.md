# Macro, meso and micro networks as linked spheres

Draws the three layers of the layered edge list as glowing 3D spheres of
actors and ties, joined by flows for the actors that appear in more than one
layer.

- **Blue**: macro network. **Green**: meso network. **Pink**: micro network.
- **Gold flows**: actors flagged `Triple_layer = yes`. Each one runs through
  its node in all three spheres.
- **Faint flows**: actors flagged in two layers only (`Intersection Type`).
  Teal is macro–meso, orange is meso–micro and violet is macro–micro. The
  violet flows arc over the meso sphere.

## How it is built

1. Node names lose their layer suffix (`_Macro`, `_Macro Network` and so on),
   so the same actor matches across layers. One spelling variant is merged:
   *Alliance of Internationalist Feminists* becomes *Alliance International
   Feminist*. Rows with an empty target are kept as isolated actors.
2. Each layer gets a 3D Fruchterman–Reingold layout. The layout is whitened
   and its radii are remapped so it fills a ball, and each node keeps its
   direction, so hubs and their fans stay intact. Isolated actors sit on a
   thin outer shell.
3. The orientation of a force layout is arbitrary. Each ball is rotated so its
   bridging actors face the viewer and the neighbouring sphere.
4. The balls are drawn in perspective with depth cueing: nodes and ties at
   the back are dimmer and smaller. Nodes are also lit at the rim.
5. Sphere radius scales with the cube root of the actor count, so sphere
   volume is proportional to network size.
6. Flows are centripetal Catmull–Rom splines between an actor's positions in
   each layer. They bundle through a jittered waist between spheres.

Positions inside a sphere show network structure only. The x, y and z axes
have no meaning.

## Use

```bash
python3 -m pip install -r requirements.txt
python3 render_layers.py edges.csv -o figures/multilayer_network_dark --layout-cache layout.npz
python3 render_layers.py edges.csv -o figures/multilayer_network_light --light --layout-cache layout.npz
```

Each run writes a PNG (600 dpi by default) and a PDF. The dense node and edge
layers in the PDF are rasterised, and the text and flows stay as vectors.
`--layout-cache` reuses layouts between runs. The macro layout takes about
1.5 minutes. `--seed` gives a different but reproducible layout. `--no-labels`
leaves out the captions and legend.
