#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run the pinned DeGoog index producer and an independent, offline consumer."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def run(bun, upstream, data, args, marker, failure=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(data.parent), "TMPDIR": str(data.parent),
           "LANG": "C.UTF-8", "DEGOOG_DATA_DIR": str(data), "BUN_CONFIG_NO_INSTALL": "1"}
    result = subprocess.run([bun, "--no-install", "volparossa-index.ts", *args], cwd=upstream,
                            env=env, capture_output=True,
                            input=marker + b"\n" if args[0] == "restore" and failure is None else b"", timeout=90)
    require(marker not in result.stdout + result.stderr, "query marker reached a process log")
    if failure is not None:
        require(result.returncode != 0 and failure.encode() in result.stderr, "expected rejection missing")
        return
    if result.returncode:
        # The only query is a synthetic marker, already checked above.
        sys.stderr.buffer.write(result.stderr[-3000:])
        raise ValueError("upstream DeGoog process failed")
    return json.loads(result.stdout.splitlines()[-1])


def main():
    require(len(sys.argv) in (4, 5), "expected Bun, upstream, documents and an optional export path")
    bun, upstream, docs = (Path(arg).resolve(strict=True) for arg in sys.argv[1:4])
    require(os.geteuid() != 0 and [name for _, name in socket.if_nameindex()] == ["lo"],
            "trial requires an unprivileged network namespace with loopback only")
    marker = ("private-probe-" + secrets.token_hex(16)).encode()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        snapshot = root / "public-index.db"
        with tempfile.TemporaryDirectory(dir=root) as producer:
            produced = run(bun, upstream, Path(producer) / "data", ["build", docs, snapshot], marker)
        require(not Path(producer).exists(), "producer state remains")
        sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        require(produced == {"documents": 3, "sha256": sha}, "unexpected producer result")

        # A changed byte sequence must never reach the upstream importer.
        bad = root / "changed.db"
        bad.write_bytes(snapshot.read_bytes() + b"x")
        run(bun, upstream, root / "rejected", ["restore", bad, sha], marker, "public index digest differs")
        require(not (root / "rejected" / "indexer" / "index-web.db").exists(),
                "an unapproved copy initialized the index")
        restored = run(bun, upstream, root / "restored", ["restore", snapshot, sha], marker)
        require(restored == {"restored": {"urls": 3, "hits": 3}, "known_queries": 3,
                             "unmatched_queries": 1, "external_requests": 0}, "unexpected recovery result")
        for path in root.rglob("*"):
            if path.is_file():
                require(marker not in path.read_bytes(), "query marker persisted in the trial directory")
        if len(sys.argv) == 5:
            # Only the public producer export crosses the boundary. The consumer has finished.
            with Path(sys.argv[4]).open("xb") as output:
                output.write(snapshot.read_bytes())
        print(json.dumps(dict(restored, snapshot_sha256=sha, snapshot_bytes=snapshot.stat().st_size,
                              corrupt_copy_rejected=True, producer_state_removed=True,
                              source_revision="1f7d2f172aad3ed5e5a69aa227b384573203742c")))


if __name__ == "__main__":
    main()
