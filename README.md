---
title: CS2FOW Bake Service
emoji: 🧱
colorFrom: gray
colorTo: green
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# CS2FOW Bake Service

Paste a Counter-Strike 2 Workshop map link or item ID. The Space downloads the mounted map package with SteamCMD, runs the CS2FOW baker, and returns a zip containing only:

```text
addons/cs2fow/data/maps/<map>.bvh8
addons/cs2fow/data/maps/<map>.json
```

No Workshop map VPKs or Valve assets are included in the output.
