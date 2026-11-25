#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cd -- "$1"
test "$(git rev-parse HEAD)" = 1f7d2f172aad3ed5e5a69aa227b384573203742c
curl --fail --location --retry 2 --max-time 90 --output "$RUNNER_TEMP/bun.zip" \
  https://github.com/oven-sh/bun/releases/download/bun-v1.4.4/bun-linux-x64.zip
echo "bf8ccbfb2b0b80159e0f9a6d15c55317add8077534dbc6130a3edb52fccb395c  $RUNNER_TEMP/bun.zip" | sha256sum --check
unzip -q "$RUNNER_TEMP/bun.zip" bun-linux-x64/bun -d "$RUNNER_TEMP"
test "$("$RUNNER_TEMP/bun-linux-x64/bun" --version)" = 1.4.4
"$RUNNER_TEMP/bun-linux-x64/bun" install --frozen-lockfile --ignore-scripts
git diff --exit-code
cp -- "$source_dir/public-index.ts" volparossa-index.ts
