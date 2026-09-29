#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage both exact-pinned source overlays; no automatic network attachment or full build."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path

import prepare_compute_source as compute

ROOT = Path(__file__).resolve().parents[1]
MODULE = "VolparossaNetwork.sys.mjs"
PATH = compute.PREFIX + "moz.build"
ANCHOR = '    "VolparossaComputePanel.sys.mjs",\n'


def transform(source):
    if source.count(ANCHOR) != 1 or MODULE in source:
        raise ValueError("network source overlay requires the exact single compute source hook")
    return source.replace(ANCHOR, ANCHOR + f'    "{MODULE}",\n', 1)


def prepare(output):
    staged = compute.prepare(output)
    source_path = staged / "patched" / PATH
    source = source_path.read_text()
    patched = transform(source)
    patch = "".join(difflib.unified_diff(source.splitlines(keepends=True),
        patched.splitlines(keepends=True), fromfile="a/" + PATH, tofile="b/" + PATH))
    source_path.write_text(patched)
    module = (ROOT / "integration" / MODULE).read_bytes()
    (staged / "patched" / compute.PREFIX / MODULE).write_bytes(module)
    (staged / "network-gateway.patch").write_text(patch)
    record = json.loads((staged / "source.json").read_text())
    record["network_gateway"] = {"module_sha256": hashlib.sha256(module).hexdigest(),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "automatic_attachment": False, "full_browser_killswitch": False}
    (staged / "source.json").write_text(json.dumps(record, indent=2) + "\n")
    return staged


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    print(prepare(parser.parse_args().output))
