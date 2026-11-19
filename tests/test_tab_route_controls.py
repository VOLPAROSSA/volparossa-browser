# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "linux", "the native grant reader targets Linux")
class PrivateGrantTests(unittest.TestCase):
    def test_read_only_exact_bounded_owner_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "read-grant"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-x", "c++",
                            "-", "-I", str(ROOT / "integration"), "-o", str(binary)],
                           input=b'''#include "VolparossaGrantFile.h"
int main(int argc, char** argv) {
  volparossa::GrantBytes bytes;
  size_t length = 0;
  if (argc != 2 || !volparossa::ReadPrivateGrant(argv[1], bytes, length)) return 1;
  return std::fwrite(bytes.data(), 1, length, stdout) == length ? 0 : 1;
}
''', check=True)
            def read(path):
                return subprocess.run([binary, path], capture_output=True, timeout=5, check=False)
            home = root / "grants"
            home.mkdir()
            file = home / "grant"
            file.write_bytes(b'{"fixture":true}\n')
            file.chmod(0o600)
            self.assertEqual(read(file).stdout, file.read_bytes())
            file.write_bytes(b"x" * 4096)
            self.assertEqual(read(file).stdout, file.read_bytes())
            file.write_bytes(b"x" * 4097)
            self.assertEqual(read(file).returncode, 1)
            file.write_bytes(b"")
            self.assertEqual(read(file).returncode, 1)
            file.write_bytes(b"fixture")
            file.chmod(0o644)
            self.assertEqual(read(file).returncode, 1)
            file.chmod(0o600)
            os.link(file, home / "second-link")
            self.assertEqual(read(file).returncode, 1)
            (home / "second-link").unlink()
            (home / "alias").symlink_to(file)
            (root / "alias-dir").symlink_to(home, target_is_directory=True)
            os.mkfifo(home / "fifo", mode=0o600)
            for refused in (home / "alias", root / "alias-dir/grant", home / "fifo", home):
                with self.subTest(refused=refused.name):
                    self.assertEqual(read(refused).returncode, 1)
            self.assertEqual(read(file).stdout, b"fixture")
