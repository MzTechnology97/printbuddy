#!/bin/sh
# Printbuddy's additions to the slicer-api sidecar source: apply.sh <sidecar source dir> [this dir]
# Copies the /arrange route (and its test) in and registers it in src/index.ts; fails if the source has moved on so
# the registration no longer fits (the build then stops instead of shipping a sidecar without /arrange).
set -eu
src="$1"
here="${2:-$(dirname "$0")}"
mkdir -p "$src/src/routes/arrange" "$src/tests/e2e"
cp "$here/src/routes/arrange/route.ts" "$src/src/routes/arrange/route.ts"
cp "$here/tests/e2e/arrange.spec.ts" "$src/tests/e2e/arrange.spec.ts"
index="$src/src/index.ts"
grep -q 'routes/arrange/route' "$index" || sed -i \
  -e 's#^import slicing from "./routes/slicing/route";#&\nimport arrange from "./routes/arrange/route";#' \
  -e 's#^  app.use("/slice-async", asyncSlicing);#&\n  app.use("/arrange", arrange);#' \
  "$index"
grep -q '^import arrange from "./routes/arrange/route";' "$index"
grep -q '^  app.use("/arrange", arrange);' "$index"
echo "Printbuddy: /arrange added to $index"
