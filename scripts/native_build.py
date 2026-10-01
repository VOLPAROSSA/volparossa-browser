#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Plan by default; explicitly fetch exact inputs, then build Gecko offline in the workspace."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import signal
import subprocess
import tarfile
import urllib.request

import prepare_compute_source as compute
import prepare_network_ech as ech
import prepare_network_source as network

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "patches/firefox-native-build.json"
GIB = 1024 ** 3


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_root(raw):
    path = Path(raw).absolute()
    require(path == path.resolve() and path.is_relative_to(ROOT / "build")
            and path != ROOT / "build", "build root must be a nonsymlink child of this checkout's build/")
    return path


def run(argv, cwd, *, env=None, seconds=3600, capture=False):
    """Join the process group on interruption/timeout; never leave a detached build."""
    child = subprocess.Popen([str(x) for x in argv], cwd=cwd, env=env,
                             stdout=subprocess.PIPE if capture else None,
                             start_new_session=True)
    try:
        output, _ = child.communicate(timeout=seconds)
        require(child.returncode == 0, f"command failed ({child.returncode}): {argv[0]}")
        return output.decode().strip() if capture else None
    except BaseException:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        raise


def git_environment():
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


def git(argv, cwd, capture=False):
    # No user credentials, hooks, filters or global configuration in this separate upstream tree.
    return run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "credential.helper=", "-c", "protocol.file.allow=never", *argv],
               cwd, env=git_environment(), capture=capture)


def fetch_source(root, pins):
    source = root / "source"
    if not source.exists():
        source.mkdir()
        git(["init", "--quiet"], source)
    require((source / ".git").is_dir() and not (source / ".git").is_symlink(), "foreign source directory")
    # A missing fetch can be resumed; an existing different HEAD is never reset or replaced.
    head = subprocess.run(["/usr/bin/git", "rev-parse", "--verify", "HEAD"], cwd=source,
                          env=git_environment(), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=False)
    if head.returncode:
        git(["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules",
             pins["source"]["origin"], pins["source"]["revision"]], source)
        git(["checkout", "--detach", "FETCH_HEAD"], source)
    require(git(["rev-parse", "HEAD"], source, True) == pins["source"]["revision"], "wrong Firefox commit")
    require(git(["rev-parse", "HEAD^{tree}"], source, True) == pins["source"]["tree"], "wrong Firefox tree")


def check_source_changes(root, pins):
    source = root / "source"
    require(git(["rev-parse", "HEAD"], source, True) == pins["source"]["revision"], "wrong Firefox commit")
    require(git(["rev-parse", "HEAD^{tree}"], source, True) == pins["source"]["tree"], "wrong Firefox tree")
    changed = set(filter(None, git(["diff", "--name-only", "HEAD"], source, True).splitlines()))
    extra = set(filter(None, git(["ls-files", "--others", "--exclude-standard"], source, True).splitlines()))
    receipt = root / "overlay.json"
    allowed = set(json.loads(receipt.read_text())["files"]) if receipt.exists() else set()
    require((changed | extra) <= allowed, "unexpected source changes; never reset or overwrite them")


def artifact_url(pins, item):
    return (pins["artifact_origin"] + item["task"] + "/runs/" + str(item["run"])
            + "/artifacts/" + item["artifact"])


def fetch_artifact(root, pins, item):
    path = root / "downloads" / (item["name"] + ".tar.zst")
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        partial = path.with_suffix(".partial")
        require(not partial.exists(), "partial download retained; inspect it before retrying: " + str(partial))
        count, sha = 0, hashlib.sha256()
        with urllib.request.urlopen(artifact_url(pins, item), timeout=30) as response, partial.open("xb") as out:
            require(response.geturl().startswith("https://"), "insecure artifact redirect")
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                require(count <= item["bytes"], "artifact exceeds exact bound")
                sha.update(chunk)
                out.write(chunk)
        require(count == item["bytes"] and sha.hexdigest() == item["sha256"], "artifact size/hash mismatch")
        partial.rename(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == item["bytes"]
            and digest(path) == item["sha256"], "cached artifact size/hash mismatch")
    return path


def safe_member(member, destination, prefix):
    name = PurePosixPath(member.name)
    require(not name.is_absolute() and ".." not in name.parts and name.parts
            and name.parts[0] == prefix, "archive escapes its declared root")
    require(member.isfile() or member.isdir() or member.issym() or member.islnk(), "unexpected archive special file")
    require(not member.mode & 0o6000, "archive set-id bits rejected")
    return tarfile.data_filter(member, destination)


def tool_inventory(root):
    require(root.is_dir() and not root.is_symlink(), "missing or symlink tool root")
    result = {}
    for path in sorted(root.rglob("*")):
        require(path.resolve().is_relative_to(root.parent), "tool symlink escapes extraction")
        name = str(path.relative_to(root))
        if path.is_symlink():
            result[name] = {"link": os.readlink(path)}
        elif path.is_dir():
            result[name] = {"directory": True, "mode": path.stat().st_mode & 0o777}
        else:
            require(path.is_file(), "tool contains a special file")
            result[name] = {"sha256": digest(path), "size": path.stat().st_size,
                            "mode": path.stat().st_mode & 0o777}
    return result


def unpack(root, item, archive):
    parent = root / "tools" / item["name"]
    receipt = parent / "extracted.json"
    if receipt.is_file():
        saved = json.loads(receipt.read_text())
        require(saved["artifact"] == item, "different toolchain already staged")
        require(saved["files"] == tool_inventory(parent / item["root"]), "staged toolchain files changed")
        return parent / item["root"]
    parent.mkdir(parents=True, exist_ok=False)
    child = subprocess.Popen(["zstd", "--decompress", "--stdout", str(archive)], stdout=subprocess.PIPE,
                             start_new_session=True)
    try:
        with tarfile.open(fileobj=child.stdout, mode="r|") as stream:
            total, count = 0, 0
            for member in stream:
                total += member.size
                count += 1
                require(total <= 8 * GIB and count <= 250000, "toolchain unpack bound")
                stream.extract(member, parent, filter=lambda m, d: safe_member(m, d, item["root"]))
        require(child.wait(timeout=15) == 0, "zstd failed")
    finally:
        child.stdout.close()
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    with receipt.open("x") as out:
        json.dump({"artifact": item, "files": tool_inventory(parent / item["root"])}, out, indent=2)
    return parent / item["root"]


def overlay_files(source):
    """Reuse reviewed transformations offline, after matching every affected original."""
    upstream = json.loads((ROOT / "patches/firefox-source.json").read_text())
    native = json.loads(ech.PIN.read_text())
    require(upstream["upstream_revision"] == native["upstream_revision"] == ech.REVISION,
            "overlay revision mismatch")
    result = {}
    for pin, transform in ((upstream, compute.transform), (native, ech.transform)):
        for relative, sha in pin["upstream_sha256"].items():
            path = source / relative
            require(path.is_file() and not path.is_symlink() and digest(path) == sha,
                    "source differs before overlay: " + relative)
            result[relative] = transform(relative, path.read_text()).encode()
    result[network.PATH] = network.transform(result[network.PATH].decode()).encode()
    for name in ("VolparossaCompute.sys.mjs", "VolparossaComputePanel.sys.mjs", "volparossa-compute.css",
                 network.MODULE, network.BROWSER_MODULE):
        relative = compute.PREFIX + name
        require(not (source / relative).exists(), "integration destination already exists")
        result[relative] = (ROOT / "integration" / name).read_bytes()
    require(digest(ech.PATCH) == native["patch_sha256"], "native patch identity changed")
    return result


def prepare_overlay(root):
    receipt = root / "overlay.json"
    source = root / "source"
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        require(saved["native_patch_sha256"] == digest(ech.PATCH), "native patch changed; choose a new build root")
        for relative, sha in saved["files"].items():
            require(digest(source / relative) == sha, "prepared source changed: " + relative)
        for name, sha in saved["integration"].items():
            require(digest(ROOT / "integration" / name) == sha, "integration changed; choose a new build root")
        return saved
    files = overlay_files(source)
    saved = {"revision": ech.REVISION, "native_patch_sha256": digest(ech.PATCH),
             "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
             "integration": {Path(name).name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()
                             if Path(name).name.startswith("Volparossa") or name.endswith("volparossa-compute.css")}}
    for name, raw in files.items():
        (source / name).write_bytes(raw)
    with receipt.open("x") as out:
        json.dump(saved, out, indent=2)
    return saved


def verify_rust(stage, pin):
    stage = Path(stage).resolve(strict=True)
    report = stage / "TOOLCHAIN_REPORT.json"
    require(digest(report) == pin["report_sha256"], "Rust provenance report differs")
    record = json.loads(report.read_text())
    for name, entry in record["files"].items():
        path = stage / name
        require(path.resolve().is_relative_to(stage), "Rust stage escapes workspace stage")
        if path.is_symlink():
            raw = os.readlink(path).encode()
            size, sha = len(raw), hashlib.sha256(raw).hexdigest()
        else:
            size, sha = path.stat().st_size, digest(path)
        require(size == entry["size"] and sha == entry["sha256"],
                "Rust stage file differs: " + name)
    return stage / "toolchain"


def build_command(root, tools, rust):
    clang, sysroot = tools["clang"], tools["sysroot"]
    bindirs = [clang / "bin", rust / "bin", tools["node"] / "bin", tools["nasm"], tools["cbindgen"],
               tools["dump_syms"], Path("/usr/bin"), Path("/bin")]
    # IPDL's multiprocessing.Manager uses AF_UNIX sockets. Keep the namespace path
    # short while binding its backing directory to this workspace, never host /tmp.
    env = {"PATH": ":".join(str(p) for p in bindirs), "LANG": "C.UTF-8", "TMPDIR": "/tmp",
           "MOZBUILD_STATE_PATH": str(root / "state"), "MOZCONFIG": str(root / "mozconfig"),
           "CARGO_HOME": str(root / "cargo"), "CARGO_NET_OFFLINE": "true", "CARGO_BUILD_JOBS": "2",
           "XDG_CACHE_HOME": str(root / "cache"), "MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE": "none",
           "MACH_TELEMETRY_NO_SUBMIT": "1", "MOZ_CRASHREPORTER_DISABLE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PIP_NO_INDEX": "1",
           "CC": str(clang / "bin/clang"), "CXX": str(clang / "bin/clang++"),
           "HOST_CC": str(clang / "bin/clang"), "HOST_CXX": str(clang / "bin/clang++"),
           "RUSTC": str(rust / "bin/rustc"), "CARGO": str(rust / "bin/cargo"),
           "CBINDGEN": str(tools["cbindgen"] / "cbindgen"), "NASM": str(tools["nasm"] / "nasm"),
           "NODEJS": str(tools["node"] / "bin/node"), "DUMP_SYMS": str(tools["dump_syms"] / "dump_syms")}
    config = "\n".join([
        "# Workspace-only native development build; NOT artifact mode.",
        "mk_add_options MOZ_OBJDIR=" + shlex.quote(str(root / "obj")),
        'mk_add_options MOZ_MAKE_FLAGS="-j2"', "ac_add_options --enable-application=browser",
        "ac_add_options --disable-bootstrap", "ac_add_options --disable-debug-symbols",
        "ac_add_options --with-sysroot=" + shlex.quote(str(sysroot)),
        "ac_add_options --with-host-sysroot=" + shlex.quote(str(sysroot)),
        "ac_add_options --with-wasi-sysroot=" + shlex.quote(str(tools["wasi"])),
        "ac_add_options --with-libclang-path=" + shlex.quote(str(clang / "lib")), ""])
    command = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-net",
               "--ro-bind", "/", "/", "--bind", str(root), str(root),
               "--ro-bind", str(root / "tools"), str(root / "tools"),
               "--ro-bind", str(root / "downloads"), str(root / "downloads"),
               "--proc", "/proc", "--dev", "/dev",
               "--bind", str(root / "tmp"), "/tmp", "--chdir", str(root / "source"), "--clearenv"]
    for key, value in env.items():
        command.extend(["--setenv", key, value])
    return config, command + ["/usr/bin/python3", "-B", "mach"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "build/native-firefox-157"))
    parser.add_argument("--fetch", action="store_true", help="explicitly fetch source and 572,580,353 bytes of toolchain archives")
    parser.add_argument("--build", action="store_true", help="explicitly configure and compile the real native browser, offline")
    parser.add_argument("--rust-stage", help="existing exact Stalwart Rust 1.98.1 stage; required only for --build")
    args = parser.parse_args(argv)
    root, pins = checked_root(args.output), json.loads(PINS.read_text())
    require(pins["source"]["revision"] == ech.REVISION, "build source pin differs from native patch")
    plan = {"version": 1, "source": pins["source"], "output": str(root),
            "toolchain_download_bytes": sum(item["bytes"] for item in pins["toolchains"]),
            "source_download_bytes": None, "source_transfer": "depth-one Git fetch; exact byte count unknown until fetched",
            "required_free_bytes": 60 * GIB, "jobs": 2,
            "network_fetch_requested": args.fetch, "native_build_requested": args.build,
            "native_build_proven": False, "native_ech_wire_proven": False}
    print(json.dumps(plan, indent=2), flush=True)
    if not args.fetch and not args.build:
        return
    for tool in ("git", "bwrap", "zstd", "make", "perl", "unzip", "pkg-config"):
        require(shutil.which(tool), "missing existing host utility: " + tool)
    require(shutil.disk_usage(ROOT).free >= 60 * GIB, "native build requires at least 60 GiB free")
    root.mkdir(parents=True, exist_ok=True)
    require(not root.is_symlink(), "symlink build root")
    lock = (root / "build.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.fetch:
        fetch_source(root, pins)
        for item in pins["toolchains"]:
            fetch_artifact(root, pins, item)
    if not args.build:
        return
    require(args.rust_stage, "--build requires --rust-stage; no automatic Rust/system installation")
    rust = verify_rust(args.rust_stage, pins["reused_rust"])
    check_source_changes(root, pins)
    tools = {}
    for item in pins["toolchains"]:
        archive = root / "downloads" / (item["name"] + ".tar.zst")
        require(archive.is_file() and archive.stat().st_size == item["bytes"] and digest(archive) == item["sha256"],
                "missing/changed archive; --build does not fetch: " + item["name"])
        tools[item["name"]] = unpack(root, item, archive)
    overlay = prepare_overlay(root)
    for name in ("tmp", "state", "cargo", "cache"):
        (root / name).mkdir(exist_ok=True)
    config, command = build_command(root, tools, rust)
    target = root / "mozconfig"
    if target.exists():
        require(target.read_text() == config, "mozconfig differs; choose a new build root")
    else:
        with target.open("x") as out:
            out.write(config)
    run(command + ["configure"], root, seconds=600)
    run(command + ["build", "-j2"], root, seconds=6 * 3600)
    outputs = [root / "obj/dist/bin" / name for name in ("firefox", "libxul.so", "xpcshell")]
    require(all(path.is_file() for path in outputs), "build did not produce Firefox, libxul and xpcshell")
    result = {**plan, "native_build_proven": True, "native_ech_wire_proven": False, "overlay": overlay,
              "outputs": {str(path.relative_to(root)): digest(path) for path in outputs},
              "build_network_disabled": True, "system_installation": False}
    receipt = root / "build-result.json"
    require(not receipt.exists(), "previous build receipt preserved")
    with receipt.open("x") as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
