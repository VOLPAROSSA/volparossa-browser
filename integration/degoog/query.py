#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Search the enrolled public Browser documents with the staged DeGoog engine."""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def query(bun, upstream, snapshot, sha256, submitted):
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not 0 < len(submitted) <= 2048:
        raise ValueError("invalid digest or query length")
    with tempfile.TemporaryDirectory(prefix="degoog-query-") as private:
        env = {"PATH": "/usr/bin:/bin", "HOME": private, "TMPDIR": private,
               "DEGOOG_DATA_DIR": str(Path(private) / "data"), "BUN_CONFIG_NO_INSTALL": "1",
               "LOG_LEVEL": "fatal", "LANG": "C.UTF-8"}
        result = subprocess.run([bun, "--no-install", "volparossa-index.ts", "query", snapshot, sha256],
                                cwd=upstream, env=env, input=submitted, capture_output=True, timeout=30)
        if result.returncode or result.stderr or len(result.stdout) > 16384:
            raise ValueError("local search unavailable")
        reply = json.loads(result.stdout)
        if not (isinstance(reply, dict) and set(reply) == {"results"} and isinstance(reply["results"], list)
                and len(reply["results"]) <= 3 and all(isinstance(row, dict) and set(row) == {"url", "title", "snippet"}
                and all(isinstance(value, str) for value in row.values()) for row in reply["results"])):
            raise ValueError("invalid local search response")
        return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bun", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True, help="digest from the independently authorized publication")
    args = parser.parse_args()
    try:
        reply = query(args.bun.resolve(strict=True), args.upstream.resolve(strict=True),
                      args.snapshot.absolute(), args.sha256, sys.stdin.buffer.read(2049))
    except (ValueError, OSError, subprocess.SubprocessError):
        parser.exit(1, "Local public-index search unavailable.\n")
    print(json.dumps(reply, ensure_ascii=False))


if __name__ == "__main__":
    main()
