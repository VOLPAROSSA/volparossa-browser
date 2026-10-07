#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stage a pinned WebRequest admission overlay from local source; no download/build."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parents[1]
REVISION = "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1"
PREFIX = "toolkit/components/extensions/webrequest/"
REQUEST = PREFIX + "WebRequest.sys.mjs"
BUILD = PREFIX + "moz.build"
MODULES = ("Admission.sys.mjs", "WebRequestAdmission.sys.mjs")
MAX_SOURCE = 2 * 1024 * 1024

EDITS = {
    REQUEST: [
        ('  WebRequestUpload: "resource://gre/modules/WebRequestUpload.sys.mjs",\n',
         '  WebRequestUpload: "resource://gre/modules/WebRequestUpload.sys.mjs",\n'
         '  WebRequestAdmission: "resource://gre/modules/volparossa/WebRequestAdmission.sys.mjs",\n'),
        ('''          let result = callback(data);

          if (typeof result === "object" && opts.blocking) {
            handlerResults.push({ opts, result });
          }
''', '''          // Parent-owned membership only; ordinary addon callbacks stay unchanged.
          const admission = opts.blocking
            ? lazy.WebRequestAdmission.start(opts.policy, callback, data)
            : null;
          let result = admission ? admission.result : callback(data);

          if (typeof result === "object" && opts.blocking) {
            handlerResults.push({ opts, result, admission });
          }
'''),
        ('''    if (this.dnrActive && lazy.ExtensionDNR.handleRequest(channel, kind)) {
      return;
    }
''', '''    if (this.dnrActive && lazy.ExtensionDNR.handleRequest(channel, kind)) {
      for (const { admission } of handlerResults) {
        admission?.discard();
      }
      return;
    }
'''),
        ('''      for (let { opts, result } of handlerResults) {
        if (isThenable(result)) {
''', '''      for (let { opts, result, admission } of handlerResults) {
        if (admission) {
          try {
            channel.suspend(markerText);
            result = await result;
            // Single-use final check immediately before native result handling.
            admission.validate();
          } catch {
            // Do not fall through the ordinary extension-error "continue" path:
            // a stale combined uBO result cannot be treated as an allow decision.
            // A failed Suspend/Resume must not bypass the cancellation attempt.
            // If Cancel itself fails, never release a still-suspended channel.
            shouldResume = false;
            channel.cancel(
              Cr.NS_ERROR_ABORT,
              Ci.nsILoadInfo.BLOCKING_REASON_EXTENSION_WEBREQUEST
            );
            channel.resume();
            return;
          }
          if (!result || typeof result !== "object") {
            continue;
          }
        } else if (isThenable(result)) {
'''),
    ],
    BUILD: [
        ('UNIFIED_SOURCES += [\n', '''# Browser-owned modules; not a WebExtension API or default enrollment.
EXTRA_JS_MODULES["volparossa"] += [
    "volparossa/Admission.sys.mjs",
    "volparossa/WebRequestAdmission.sys.mjs",
]

UNIFIED_SOURCES += [
'''),
    ],
}

# Match the end of applyChanges specifically, not another listener's catch.
FINAL_ANCHOR = '''    } catch (e) {
      Cu.reportError(e);
    }

    // Only resume the channel if it was suspended by this call.
'''
FINAL_REPLACEMENT = '''    } catch (e) {
      Cu.reportError(e);
    } finally {
      // Includes operations not reached after another addon cancels/redirects.
      for (const { admission } of handlerResults) {
        admission?.discard();
      }
    }

    // Only resume the channel if it was suspended by this call.
'''


def transform(path, source):
    if path not in EDITS or "WebRequestAdmission" in source:
        raise ValueError("unsupported pinned source path")
    for old, new in EDITS[path]:
        if source.count(old) != 1:
            raise ValueError("pinned source anchor changed or ambiguous")
        source = source.replace(old, new, 1)
    if path == REQUEST:
        if source.count(FINAL_ANCHOR) != 1:
            raise ValueError("pinned final cleanup anchor changed or ambiguous")
        source = source.replace(FINAL_ANCHOR, FINAL_REPLACEMENT, 1)
    return source


def bounded_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE:
        raise ValueError("source must be a bounded regular file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_SOURCE + 1)
    if len(raw) > MAX_SOURCE:
        raise ValueError("oversized source")
    return raw


def render_patch(originals, changed):
    return "".join(line for path, original in originals.items()
                   for line in difflib.unified_diff(original.splitlines(keepends=True),
                       changed[path].splitlines(keepends=True),
                       fromfile="a/" + path, tofile="b/" + path)).encode("utf-8")


def prepare(source, output):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "build") or output == ROOT / "build" or output.exists():
        raise ValueError("output must be a fresh child of this checkout's build/")
    pins = json.loads((ROOT / "patches/firefox-filter-admission.json").read_text())
    if pins["upstream_revision"] != REVISION or set(pins["upstream_sha256"]) != set(EDITS):
        raise ValueError("unexpected source inventory")

    # Read and validate everything before creating the fresh output directory.
    originals, changed, modules = {}, {}, {}
    for path in EDITS:
        raw = bounded_file(source / path)
        if hashlib.sha256(raw).hexdigest() != pins["upstream_sha256"][path]:
            raise ValueError("pinned upstream source hash differs")
        originals[path] = raw.decode("utf-8", "strict")
        changed[path] = transform(path, originals[path])
    for name in MODULES:
        modules[name] = bounded_file(ROOT / "integration/filters" / name)
    patch_bytes = render_patch(originals, changed)
    if patch_bytes != bounded_file(ROOT / "patches/0003-filter-admission.patch"):
        raise ValueError("transformation differs from committed native patch")

    output.mkdir(parents=True)
    for path, original in originals.items():
        for kind, text in (("original", original), ("patched", changed[path])):
            destination = output / kind / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8", newline="")
    for name, raw in modules.items():
        destination = output / "patched" / PREFIX / "volparossa" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    (output / "filter-admission.patch").write_bytes(patch_bytes)
    (output / "source.json").write_text(json.dumps({
        **pins,
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "patched_sha256": {path: hashlib.sha256(text.encode()).hexdigest()
                           for path, text in changed.items()},
        "module_sha256": {name: hashlib.sha256(raw).hexdigest() for name, raw in modules.items()},
        "native_build_proven": False,
        "default_enrollment": False,
        "publication_owner_integrated": False,
    }, indent=2) + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="local exact-pinned Firefox source tree (read only)")
    parser.add_argument("--output", required=True, help="new output under this checkout's build/")
    args = parser.parse_args()
    print(prepare(args.source, args.output))
