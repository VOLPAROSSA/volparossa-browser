# SPDX-License-Identifier: GPL-3.0-only
"""Offline preparation tests. These do not compile Gecko or execute a downloaded binary."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_build as build


class NativeBuildTests(unittest.TestCase):
    def test_default_plan_does_not_fetch_execute_or_write(self):
        with mock.patch.object(build.urllib.request, "urlopen", side_effect=AssertionError("network")), \
             mock.patch.object(build.subprocess, "Popen", side_effect=AssertionError("process")), \
             mock.patch.object(build.Path, "mkdir", side_effect=AssertionError("mkdir")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            build.main([])
        plan = json.loads(out.getvalue())
        self.assertEqual(plan["toolchain_download_bytes"], 572580353)
        self.assertIsNone(plan["source_download_bytes"])
        self.assertFalse(plan["native_build_proven"])
        self.assertFalse(plan["network_fetch_requested"])

    def test_pins_are_immutable_runs_not_floating_indices(self):
        pins = json.loads(build.PINS.read_text())
        self.assertEqual(pins["source"]["revision"], build.ech.REVISION)
        self.assertEqual(len(pins["toolchains"]), 7)
        for item in pins["toolchains"]:
            self.assertEqual(len(item["sha256"]), 64)
            self.assertIn("/runs/0/artifacts/public/build/", build.artifact_url(pins, item))
            self.assertNotIn("latest", build.artifact_url(pins, item))

    def test_root_cannot_be_workspace_or_escaped_symlink(self):
        for path in (ROOT, ROOT / "build", ROOT / "../outside"):
            with self.assertRaises(ValueError):
                build.checked_root(path)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            link = Path(directory) / "link"
            link.symlink_to("/tmp", target_is_directory=True)
            with self.assertRaises(ValueError):
                build.checked_root(link / "native")

    def test_archive_rejects_escape_devices_and_foreign_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("../outside", "/outside", "wrong/file", "clang/../../outside"):
                with self.assertRaises(ValueError):
                    build.safe_member(tarfile.TarInfo(name), directory, "clang")
            member = tarfile.TarInfo("clang/tool")
            member.type = tarfile.CHRTYPE
            with self.assertRaises(ValueError):
                build.safe_member(member, directory, "clang")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            with self.assertRaises(tarfile.FilterError):
                build.safe_member(member, directory, "clang")
            member = tarfile.TarInfo("clang/LICENSE.TXT")
            member.size = 12
            self.assertEqual(build.safe_member(member, directory, "clang").name, member.name)

    def test_native_build_preserves_real_backend_and_network_isolation(self):
        root = ROOT / "build/example"
        pins = json.loads(build.PINS.read_text())
        tools = {item["name"]: root / "tools" / item["name"] / item["root"] for item in pins["toolchains"]}
        config, command = build.build_command(root, tools, Path("/existing/rust"))
        self.assertIn("--disable-bootstrap", config)
        self.assertNotIn("artifact-build", config)
        self.assertNotIn("--disable-tests", config)
        self.assertNotIn("--without-wasm", config)
        self.assertIn("--unshare-net", command)
        self.assertIn("--die-with-parent", command)
        self.assertIn("--clearenv", command)
        self.assertIn(["--bind", str(root / "tmp"), "/tmp"], [command[i:i+3] for i in range(len(command)-2)])
        self.assertIn(["--setenv", "TMPDIR", "/tmp"], [command[i:i+3] for i in range(len(command)-2)])
        self.assertEqual(command[-3:], ["/usr/bin/python3", "-B", "mach"])
        for variable in ("CARGO_NET_OFFLINE", "PIP_NO_INDEX", "MACH_BUILD_PYTHON_NATIVE_PACKAGE_SOURCE"):
            self.assertIn(variable, command)

    def test_reused_toolchain_is_rejected_if_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = {"name": "clang", "root": "clang", "sha256": "a" * 64}
            parent = root / "tools/clang"
            tool = parent / "clang"
            tool.mkdir(parents=True)
            binary = tool / "clang"
            binary.write_bytes(b"fixture, never executed")
            receipt = {"artifact": item, "files": build.tool_inventory(tool)}
            (parent / "extracted.json").write_text(json.dumps(receipt))
            with mock.patch.object(build.subprocess, "Popen", side_effect=AssertionError("process")):
                self.assertEqual(build.unpack(root, item, root / "unused.tar.zst"), tool)
                binary.write_bytes(b"changed")
                with self.assertRaises(ValueError):
                    build.unpack(root, item, root / "unused.tar.zst")

    def test_offline_overlay_uses_exact_reviewed_originals(self):
        originals = ROOT / "build/network-source-native-ech-02/original"
        if not originals.is_dir():
            self.skipTest("optional exact-original staging is unavailable; no download in this test")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            shutil.copytree(originals, source, dirs_exist_ok=True)
            with mock.patch.object(build.urllib.request, "urlopen", side_effect=AssertionError("network")):
                result = build.overlay_files(source)
            native = "netwerk/protocol/http/HttpBaseChannel.cpp"
            self.assertIn(b"HttpBaseChannel::SetAllowECH", result[native])
            self.assertIn(b"VolparossaNetwork.sys.mjs", result[build.network.PATH])
            block = result[build.network.PATH].decode().split("EXTRA_JS_MODULES += [", 1)[1].split("]", 1)[0]
            modules = [line.strip().strip(',"') for line in block.splitlines() if line.strip()]
            self.assertEqual(modules, sorted(modules), "Gecko mozbuild rejects unsorted module lists")
            bad = source / native
            bad.write_text("unexpected source")
            with self.assertRaises(ValueError):
                build.overlay_files(source)


if __name__ == "__main__":
    unittest.main()
