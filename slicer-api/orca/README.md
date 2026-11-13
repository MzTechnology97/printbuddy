# OrcaSlicer sidecar with `/arrange`

The `orca-slicer-api` image of `../docker-compose.yml` builds from this folder:

- the slicer-api sidecar source, cloned at a pinned commit (`SLICER_API_REPO`, `SLICER_API_COMMIT`);
- Printbuddy's `/arrange` route, added by `apply.sh` (`src/routes/arrange/route.ts`, test in
  `tests/e2e/arrange.spec.ts`);
- the OrcaSlicer CLI (`ORCA_VERSION`), as the fork's own Dockerfile installs it.

Everything else (`/health`, `/profiles`, `/slice`, `/slice-async`) is the fork's, unchanged.

## `POST /arrange`

The 3MF Workspace's **Arrange** (backend `farm/workspace/arrange.py`) sends the project as its draft has it. The
route runs Orca's own arrange and answers the project as Orca's CLI exports it. The backend then reads each
instance's new place back by its `identify_id`.

| Field | Meaning | CLI |
|---|---|---|
| `file` | the 3MF project (`model/3mf`) | the input |
| `plate` | `0` every plate (Arrange all objects), `N` plate N only | `N`: `--slice N` (the CLI arranges a single plate only when it slices it; the G-code is thrown away) |
| `allowRotations` | Auto rotate for arrangement | `--allow-rotations=0/1` |
| `allowMultiMaterial` | Allow multiple materials on same plate (default on) | `--allow-multicolor-oneplate=0/1` |
| `avoidCalibrationRegion` | Avoid extrusion calibration region | `--avoid-extrusion-cali-region=0/1` |
| `alignToYAxis` | Align to Y axis (off while rotating, as Orca's dialog) | `--align-to-y-axis=0/1` |

Orca's dialog also has **Spacing**. Its CLI has no option for it and arranges with 0, "auto spacing".

The answer is `200` with the arranged 3MF. A `422` carries the CLI's own reason (from `result.json`) when it fails.
A `400` means a request without a 3MF or with a plate out of range.

## Build and test locally

```bash
# the route's tests, on a copy of the sidecar source (no slicer needed: a stand-in CLI)
git clone https://github.com/vmhomelab/orca-slicer-api.git /tmp/sidecar
git -C /tmp/sidecar checkout 1415ea2fc02e642b7a9b6b8ce905661de86ac8f6
sh apply.sh /tmp/sidecar .
(cd /tmp/sidecar && npm ci && npx vitest run tests/e2e/arrange.spec.ts)

# the image, then a real arrange
docker build --build-arg ORCA_VERSION=2.5.0 -t orca-arrange-test .
docker run -d --name orca-arrange-test -p 3013:3000 orca-arrange-test
curl -F "file=@project.3mf;type=model/3mf" -F plate=0 -o arranged.3mf http://localhost:3013/arrange
```
