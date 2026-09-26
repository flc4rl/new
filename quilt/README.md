# Climate Colonialism Quilt

A colour-coded patchwork layout for image collections. Each image becomes a
square patch, is filed into a colour family by its dominant hue, and is pieced
into a quilt top in one of three patterns:

- **Sampler blocks**: one block per colour family (3x3, 4x4 or 5x5 patches), set with sashing.
- **Trip around the world**: concentric diamonds, colour-sorted outward from the centre.
- **Strips**: one row per colour family.

Dominant colour is computed from a 40x40 downsample, weighting each pixel by
vividness so a red banner outweighs a white margin; mostly-grey images fall
into Black, Grey or Paper.

## Run

The page reads pixels with canvas, so serve it rather than opening the file:

    cd quilt && python3 -m http.server 8000
    # open http://localhost:8000

Click **Add images** (or drop files on the page) to replace the sample with
your own set. Images stay in the browser.

`sample/` holds the five source collages; the demo cuts each into an 8x6 grid.
