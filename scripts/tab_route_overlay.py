# SPDX-License-Identifier: GPL-3.0-only
"""Apply the reviewed tab-chrome patch to Mozilla's exact source inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request

import prepare_compute_source as compute

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "patches/firefox-tab-route-controls.json"
PATCH = ROOT / "patches/0004-tab-route-controls.patch"
MODULE = "VolparossaTabRoutes.sys.mjs"
HEADER = "xpcom/ioutils/VolparossaGrantFile.h"
MAX_SOURCE = 2 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def apply(source=None, staged=None):
    pins = json.loads(PINS.read_text())
    if pins["revision"] != compute.REVISION or sha(PATCH.read_bytes()) != pins["patch_sha256"]:
        raise ValueError("tab route patch or revision differs")
    originals, result = {}, {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, record in pins["files"].items():
            if source is None:
                with urllib.request.urlopen(compute.BASE + name, timeout=30) as stream:
                    raw = stream.read(MAX_SOURCE + 1)
            else:
                path = source / name
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE:
                    raise ValueError("invalid tab chrome source: " + name)
                raw = path.read_bytes()
            if len(raw) > MAX_SOURCE or sha(raw) != record["upstream"]:
                raise ValueError("tab chrome source differs: " + name)
            original = root / name
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(raw)
            originals[name] = raw
        subprocess.run(["patch", "--silent", "--batch", "--fuzz=0", "--forward", "-p1",
                        "--input", str(PATCH)], cwd=root, check=True)
        for name, record in pins["files"].items():
            raw = (root / name).read_bytes()
            if sha(raw) != record["patched"]:
                raise ValueError("tab chrome patch output differs: " + name)
            result[name] = raw
    if source and (source / HEADER).exists():
        raise ValueError("private grant reader destination already exists")
    result[HEADER] = (ROOT / "integration/VolparossaGrantFile.h").read_bytes()
    if staged:
        for kind, files in (("original", originals), ("patched", result)):
            for name, data in files.items():
                path = staged / kind / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as stream:
                    stream.write(data)
    return result
