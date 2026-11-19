#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Exact built Firefox + builtin modules + ordinary tab; synthetic gateway, no core proof.

Separate from the installed-ESR validator. No build, packaging, download, source
rewrite or compatibility module is performed. The unpacked native resources stay
read-only; a fresh private HOME and profile contain all runtime writes.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import sys

import native_build
import smoke_network
from stage_firefox import ROOT, build_path, digest

BUILD_RECEIPT_SHA256 = "4be952653fe30d9516dde25c4ccd1cf870a6ab87514f8ba6a0961a7f4d62485d"
PLUGIN_CONTAINER_SHA256 = "8e3d79d8bea685047c94b72bdbc8ee2dbecd9781905483a6cc6237530e477eec"
MODULES = ("VolparossaNetwork.sys.mjs", "VolparossaBrowserNetwork.sys.mjs")
CONTROLLER = "VolparossaBrowserNetwork.sys.mjs"
CONTROLLER_SOURCE = "browser/components/genai/" + CONTROLLER
# Explicit product-JS revision on the original native build, not an ESR shim.
# Both sides are pinned: a later product edit requires an intentional new receipt.
JAVASCRIPT_OVERLAY = dict(version=1, kind="builtin-product-javascript-resource-overlay",
    original_build_receipt_sha256=BUILD_RECEIPT_SHA256,
    source_path=CONTROLLER_SOURCE,
    original_sha256="d2b0a5507b7fcc2b212d68ab344f4c7ae9364bd1fe146bf2963c91d4263d57cf",
    runtime_sha256="fcc7eb622a1ff54e13e7ed40d5625e44f155c03b71fc0af9867bca6334379228",
    modification="DOM ownerDocument.defaultView lookup; unchanged parent/top-context guards",
    native_rebuild=False, compatibility_rewrite=False, original_build_modified=False)


def require(condition):
    if not condition:
        raise ValueError("native_network_fixture_failed")


def validate_native_runtime(root, *, javascript_overlay=False, mounted=False):
    root = native_build.checked_root(root)
    receipt = root / "build-result.json"
    require(receipt.is_file() and not receipt.is_symlink()
            and digest(receipt) == BUILD_RECEIPT_SHA256)
    record = json.loads(receipt.read_text())
    pins = json.loads(native_build.PINS.read_text())
    require(record["source"] == pins["source"] and record["native_build_proven"] is True
            and record["build_network_disabled"] is True and record["system_installation"] is False)
    require(record["output"] == str(root))
    for name, sha in record["outputs"].items():
        path = root / name
        require(path.resolve().is_relative_to(root) and digest(path) == sha)
    # A runtime validator must not call prepare_overlay: that helper writes a new
    # source overlay when its receipt is absent. Only inspect existing inputs here.
    overlay_path = root / "overlay.json"
    require(overlay_path.is_file() and not overlay_path.is_symlink())
    overlay = json.loads(overlay_path.read_text())
    require(overlay == record["overlay"] and digest(native_build.ech.PATCH) == overlay["native_patch_sha256"])
    require(not mounted or javascript_overlay)
    if javascript_overlay:
        require(overlay["files"][CONTROLLER_SOURCE] == JAVASCRIPT_OVERLAY["original_sha256"]
                and overlay["integration"][CONTROLLER] == JAVASCRIPT_OVERLAY["original_sha256"]
                and digest(ROOT / "integration" / CONTROLLER) == JAVASCRIPT_OVERLAY["runtime_sha256"])
    for name, sha in overlay["files"].items():
        path = root / "source" / name
        expected = JAVASCRIPT_OVERLAY["runtime_sha256"] if mounted and name == CONTROLLER_SOURCE else sha
        require(path.resolve().is_relative_to(root / "source") and digest(path) == expected)
        if mounted and name == CONTROLLER_SOURCE:
            require(os.statvfs(path).f_flag & os.ST_RDONLY)
    for name, sha in overlay["integration"].items():
        expected = JAVASCRIPT_OVERLAY["runtime_sha256"] if javascript_overlay and name == CONTROLLER else sha
        require(digest(ROOT / "integration" / name) == expected)
    stage = root / "obj/dist/bin"
    require(digest(stage / "plugin-container") == PLUGIN_CONTAINER_SHA256)
    modules = {}
    for name in MODULES:
        source = root / "source/browser/components/genai" / name
        installed = stage / "browser/modules" / name
        expected = JAVASCRIPT_OVERLAY["runtime_sha256"] if mounted and name == CONTROLLER else record["overlay"]["integration"][name]
        require(installed.resolve(strict=True) == source
                and digest(installed) == expected)
        modules[name] = digest(installed)
    return dict(kind="exact-native-build-not-esr", native_build=str(root),
        version="157.0.1", source_stamp=record["source"]["revision"], executable="firefox",
        build_receipt_sha256=BUILD_RECEIPT_SHA256, native_patch_sha256=record["overlay"]["native_patch_sha256"],
        runtime_sha256=record["outputs"], plugin_container_sha256=PLUGIN_CONTAINER_SHA256,
        builtin_modules_sha256=modules, native_ech_wire_proven=False,
        javascript_overlay=dict(JAVASCRIPT_OVERLAY) if javascript_overlay else None,
        javascript_overlay_mounted=mounted)


def stage_javascript_overlay(work):
    """Stage exact tracked product bytes in a new disposable directory, never source/obj."""
    source = ROOT / "integration" / CONTROLLER
    require(source.is_file() and not source.is_symlink()
            and digest(source) == JAVASCRIPT_OVERLAY["runtime_sha256"])
    folder = work / "resource-overlay"
    folder.mkdir(mode=0o700)
    with (folder / CONTROLLER).open("xb") as output:
        output.write(source.read_bytes())
    with (folder / "receipt.json").open("x") as output:
        output.write(json.dumps(JAVASCRIPT_OVERLAY, indent=2) + "\n")


def validate_javascript_overlay(work):
    folder = work / "resource-overlay"
    require(folder.is_dir() and not folder.is_symlink()
            and sorted(path.name for path in folder.iterdir()) == sorted([CONTROLLER, "receipt.json"]))
    for name in (CONTROLLER, "receipt.json"):
        require((folder / name).is_file() and not (folder / name).is_symlink())
    require(json.loads((folder / "receipt.json").read_text()) == JAVASCRIPT_OVERLAY
            and digest(folder / CONTROLLER) == JAVASCRIPT_OVERLAY["runtime_sha256"])


def host_snapshot():
    return dict(netns=os.readlink("/proc/self/ns/net"),
        dns=digest(Path("/etc/resolv.conf")), routes=digest(Path("/proc/net/route")),
        routes6=digest(Path("/proc/net/ipv6_route")))


def sandbox_command(work, root, arguments, *, javascript_overlay=False):
    task_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    require(str(task_home) == os.environ.get("HOME") and task_home.parent == Path("/home")
            and task_home.resolve() == task_home)
    # HOME is unchanged. Its private tmpfs contains only the fresh .mozilla mount;
    # no host-home entries or credentials are carried into this native trial.
    home_mounts = ["--tmpfs", str(task_home), "--dir", str(task_home / ".mozilla"),
        "--bind", str(work / "appdata"), str(task_home / ".mozilla"), "--remount-ro", str(task_home)]
    resource_mounts = []
    if javascript_overlay:
        validate_javascript_overlay(work)
        folder = work / "resource-overlay"
        resource_mounts = ["--ro-bind", str(folder), str(folder),
            "--ro-bind", str(folder / CONTROLLER), str(root / "source" / CONTROLLER_SOURCE)]
    return ["/usr/bin/bwrap", "--die-with-parent", "--new-session", "--unshare-user",
        "--unshare-pid", "--unshare-net", "--ro-bind", "/", "/", *home_mounts,
        "--bind", str(work), str(work), *resource_mounts, "--tmpfs", "/tmp", "--tmpfs", "/run",
        "--proc", "/proc", "--dev", "/dev", "--chdir", str(work),
        "--setenv", "MOZ_UPLOAD_DIR", str(work / "upload"),
        "--", "/usr/bin/python3", "-B", str(Path(__file__).resolve()), *arguments,
        "--inside", "--host-netns", os.readlink("/proc/self/ns/net")]


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-build", type=Path, default=ROOT / "build/native-firefox-157")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--origin-port", type=int, choices=(443, 18443), default=18443)
    parser.add_argument("--javascript-overlay", action="store_true",
                        help="Explicit exact-hashed DOM-owner product resource revision; no rebuild or source mutation.")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = native_build.checked_root(args.native_build)
    work = build_path(args.output)
    require(not work.is_relative_to(root) and not root.is_relative_to(work))
    metadata = validate_native_runtime(root, javascript_overlay=args.javascript_overlay,
                                       mounted=args.inside and args.javascript_overlay)
    smoke_network.ORIGIN_PORT = args.origin_port
    if args.inside:
        smoke_network.private_directory(work)
        require(os.readlink("/proc/self/ns/net") != args.host_netns)
        require(sorted(p.name for p in Path.home().iterdir()) == [".mozilla"])
        require(os.environ["MOZ_UPLOAD_DIR"] == str(work / "upload"))
        if args.javascript_overlay:
            validate_javascript_overlay(work)
        smoke_network.inside(args, root / "obj/dist/bin", work, metadata, native=True)
        return

    # Do not run while another native preparation/build can change the inputs.
    with (root / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not work.exists() and not work.is_symlink())
        work.mkdir(parents=True, mode=0o700)
        (work / "appdata").mkdir(mode=0o700)
        for name in ("firefox", "firefox-esr"):
            (work / "appdata" / name).mkdir(mode=0o700)
        (work / "upload").mkdir(mode=0o700)
        arguments = ["--native-build", str(root), "--output", str(work), "--origin-port", str(args.origin_port)]
        if args.javascript_overlay:
            stage_javascript_overlay(work)
            arguments.append("--javascript-overlay")
        before = host_snapshot()
        try:
            native_build.run(sandbox_command(work, root, arguments, javascript_overlay=args.javascript_overlay), work,
                env={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                     "PYTHONDONTWRITEBYTECODE": "1"}, seconds=180)
        finally:
            after = host_snapshot()
            (work / "host-state.json").write_text(json.dumps(dict(before=before, after=after,
                unchanged=before == after), indent=2) + "\n")
            require(before == after)
            # The temporary read-only mount must leave the original source and
            # receipt intact, even if the browser trial failed.
            validate_native_runtime(root, javascript_overlay=args.javascript_overlay)
    print(json.dumps(dict(passed=True, report=str(work / "report.json"))))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError):
        print("native browser network fixture failed; see isolated report", file=sys.stderr)
        raise SystemExit(1) from None
