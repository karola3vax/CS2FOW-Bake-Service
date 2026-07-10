# CS2FOW Bake Service

This small website turns a Counter-Strike 2 Workshop map into files that CS2FOW can read.

The visitor pastes a Workshop link or item ID. The service downloads the map with SteamCMD, gives its VPK directly to the CS2FOW C++ baker, and returns a ZIP containing only:

```text
addons/cs2fow/data/maps/<map>.bvh8
addons/cs2fow/data/maps/<map>.json
```

It never puts the Workshop VPK or other Valve files in the result.

## How requests are handled

One map is baked at a time. Two more requests may wait. If a visitor submits the same item twice, both visits point to the same job instead of downloading and baking it twice.

Finished jobs and ZIP files are kept for two hours. This is intentionally a small public service, not a permanent file store.

## Run the tests

```sh
python -m unittest -v test_service.py
```

The tests use fake bake commands. They do not download Steam content.

## Build the container

The image uses the pinned 0.2.0-preview Linux release and its exact SHA-256 checksum by default. This prevents a changed or incomplete download from silently entering the service image. Render can build the image without any extra Docker settings.

```sh
docker build -t cs2fow-bake-service .
```

To test another archive, pass both its URL and checksum:

```sh
docker build \
  --build-arg CS2FOW_ARCHIVE_URL=<url> \
  --build-arg CS2FOW_SHA256=<archive-sha256> \
  -t cs2fow-bake-service .
```

Render automatically makes service environment variables available as Docker build arguments. You can override CS2FOW_ARCHIVE_URL and CS2FOW_SHA256 in the service settings when testing a different archive.

```sh
docker run --rm -p 7860:7860 cs2fow-bake-service
```

The server listens on port `7860` by default. `PORT`, `MAX_QUEUED_JOBS`, and `RESULTS_DIR` may be changed through environment variables.
