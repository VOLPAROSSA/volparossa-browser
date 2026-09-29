#!/usr/bin/env python3
"""Stage an already installed Firefox and project defaults; never install or download it."""

import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = ROOT / "defaults/privacy.json"
MARKER = "volparossa-staging.json"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_defaults():
    values = json.loads(DEFAULTS.read_text())
    if not isinstance(values, dict) or not values:
        raise ValueError("privacy defaults must be a nonempty preference object")
    for name, value in values.items():
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]+", name):
            raise ValueError(f"invalid preference name: {name!r}")
        if type(value) not in (bool, int, str):
            raise ValueError(f"unsupported preference type: {name}")
    return values


def render_defaults(values):
    # AutoConfig ignores its first line. Feature settings use defaultPref: never lock or
    # overwrite a user's choice. The native ETP bootstrap below preserves its current choice.
    rendered = "// Project VOLPAROSSA: user-overridable defaults, not a managed policy.\n" + "".join(
        f"defaultPref({json.dumps(name)}, {json.dumps(value)});\n"
        for name, value in sorted(values.items())
    )
    # Firefox's native ContentBlockingPrefs only applies a category with a user value,
    # otherwise it classifies a fresh profile as Standard. Materialize the CURRENT choice,
    # not a forced Strict value: existing Standard/Custom choices therefore remain intact.
    return rendered + (
        '// Bootstrap the current ETP choice; never replace an existing user choice.\n'
        'pref("browser.contentblocking.category", getPref("browser.contentblocking.category"));\n'
    )


def build_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT / "build") or resolved == ROOT / "build":
        raise ValueError("output must be a fresh child directory of this repository's build/")
    return resolved


def stage(binary, output, expected_version, expected_source_stamp):
    executable = Path(binary).resolve(strict=True)
    source = executable.parent
    for required in ("omni.ja", "libxul.so", "application.ini", "browser/omni.ja"):
        if not (source / required).is_file():
            raise ValueError(f"not a resolved Firefox GRE directory: missing {required}")
    application = configparser.ConfigParser()
    application.read(source / "application.ini")
    version = application["App"]["Version"]
    stamp = application["App"]["SourceStamp"]
    if version != expected_version or stamp != expected_source_stamp:
        raise ValueError(f"installed runtime does not match explicit version/source pin: {version}, {stamp}")
    destination = build_path(output)
    if destination.exists():
        raise ValueError("staging refuses to overwrite any existing runtime directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Independent copies, not hard links: later workspace edits cannot change /usr/lib.
    # No machine-specific enterprise distribution policies are imported into the project.
    shutil.copytree(source, destination, symlinks=False, ignore=shutil.ignore_patterns("distribution"))
    configuration = destination / "defaults/pref/volparossa-autoconfig.js"
    if configuration.exists() or (destination / "volparossa.cfg").exists():
        raise ValueError("unexpected project AutoConfig already present in source runtime")
    configuration.write_text(
        '// Project VOLPAROSSA local defaults; no remote AutoConfig.\n'
        'pref("general.config.filename", "volparossa.cfg");\n'
        'pref("general.config.obscure_value", 0);\n'
        '// Retain the initial category choice even when it equals the default.\n'
        'pref("browser.contentblocking.category", "strict", sticky);\n'
    )
    (destination / "volparossa.cfg").write_text(render_defaults(load_defaults()))
    record = {
        "schema": 1,
        "kind": "installed-runtime-privacy-overlay-not-a-firefox-source-build",
        "source_gre": str(source),
        "version": version,
        "source_repository": application["App"]["SourceRepository"],
        "source_stamp": stamp,
        "build_id": application["App"]["BuildID"],
        "executable": executable.name,
        "runtime_sha256": {name: digest(destination / name) for name in (
            executable.name, "libxul.so", "omni.ja", "browser/omni.ja"
        )},
        "defaults_sha256": digest(DEFAULTS),
        "autoconfig_sha256": digest(destination / "volparossa.cfg"),
        "excluded_host_distribution_policies": True,
    }
    (destination / MARKER).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firefox", default="/usr/bin/firefox-esr")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-source-stamp", required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.firefox, args.output, args.expected_version, args.expected_source_stamp), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f"staging failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
