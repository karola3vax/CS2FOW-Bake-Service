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

The image defaults to the CS2FOW `0.2.0-preview` Linux package and verifies its exact SHA-256 checksum.

```sh
docker build -t cs2fow-bake-service .
```

To use another package, override both `CS2FOW_RELEASE_URL` and `CS2FOW_RELEASE_SHA256` as Docker build arguments. Render may provide those overrides through its environment, as described in its [Docker documentation](https://render.com/docs/docker). Mount a persistent disk at `/var/lib/cs2fow-results` if completed downloads must survive a service restart.

```sh
docker run --rm -p 7860:7860 cs2fow-bake-service
```

The server listens on port `7860` by default. `PORT`, `MAX_QUEUED_JOBS`, and `RESULTS_DIR` may be changed through environment variables.
