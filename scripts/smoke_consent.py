#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic Consent-O-Matic/uBO cooperation in an isolated, pinned Firefox ESR.

Run only in the disposable browser test environment. This wrapper enforces a
fresh profile, read-only host/runtime mounts and a loopback-only network. It
downloads nothing and never changes signed XPIs. Only the fixture profile's
ordinary extension UIs receive a local rule list and a normal uBO subscription.
This is not upstream-rule coverage, live-site compatibility or overlay proof.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode

from bundle_extensions import LOCK, load_lock, verify
from consent_fixture import PURPOSES, serve_fixture, validate_recorded_decision, values
from smoke_browser_startup import remove_profile
from smoke_compute_model import private_directory, validate_stage
from smoke_privacy import extension_snapshot, run_browser, snapshot
from stage_firefox import ROOT, build_path, digest, isolated_browser_home, load_defaults, validate_isolated_browser_home

CONSENT = "gdpr@cavi.au.dk"
UBLOCK = "uBlock0@raymondhill.net"
FIXTURE_DIR = ROOT / "tests/fixtures/consent"


def require(condition, message="consent_smoke_check_failed"):
    if not condition:
        raise ValueError(message)


def validate_bundle(stage, metadata):
    lock = load_lock()
    require(metadata.get("extensions", {}).get("lock_sha256") == digest(LOCK),
            "consent_smoke_stale_extension_lock")
    require(verify(stage / "distribution/extensions", lock) == metadata["extensions"]["packages"],
            "consent_smoke_stale_extension_packages")


def validate_isolation(stage, work, host_netns):
    require(host_netns and os.readlink("/proc/self/ns/net") != host_netns,
            "consent_smoke_requires_disposable_network")
    require(all(os.statvfs(path).f_flag & os.ST_RDONLY for path in (Path("/"), ROOT, stage)),
            "consent_smoke_requires_read_only_host")
    private_directory(work)
    validate_isolated_browser_home(work)
    require(not os.statvfs(work).f_flag & os.ST_RDONLY, "consent_smoke_requires_writable_fixture")
    links = json.loads(subprocess.check_output(["/usr/bin/ip", "-j", "link", "show"], text=True))
    require([link["ifname"] for link in links] == ["lo"], "consent_smoke_requires_loopback_only")


def until(client, script, args=(), seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = client.script(script, args)
        if result:
            return result
        time.sleep(0.1)
    raise ValueError("consent_fixture_condition_timeout")


def navigate(client, url):
    client.command("Marionette:SetContext", {"value": "content"})
    client.command("WebDriver:Navigate", {"url": url})


def addon_page(client, identifier, path):
    client.command("Marionette:SetContext", {"value": "chrome"})
    url = client.script("return WebExtensionPolicy.getByID(arguments[0]).getURL(arguments[1]);",
                        [identifier, path])
    require(url.startswith("moz-extension://"), "consent_fixture_extension_url")
    navigate(client, url)


def all_addons_active(client):
    client.command("Marionette:SetContext", {"value": "chrome"})
    entries = load_lock()["extensions"]
    result = extension_snapshot(client, [entry["id"] for entry in entries])
    require(result["signatureEnforcement"], "consent_fixture_signature_enforcement")
    for entry in entries:
        addon = result["addons"].get(entry["id"])
        require(addon and addon["active"] and addon["signatureAccepted"]
                and addon["version"] == entry["version"] and not addon["appDisabled"]
                and not addon["userDisabled"], "consent_fixture_addon_not_active")
    return result


def set_control_addons(client, enabled):
    navigate(client, "about:blank")
    client.command("Marionette:SetContext", {"value": "chrome"})
    result = client.command("WebDriver:ExecuteAsyncScript", {
        "script": """
          const [ids, enabled, done] = arguments;
          (async () => {
            const {AddonManager} = ChromeUtils.importESModule(
              "resource://gre/modules/AddonManager.sys.mjs");
            for (const id of ids) {
              const addon = await AddonManager.getAddonByID(id);
              if (!addon) throw new Error("Missing fixture extension");
              if (enabled) await addon.enable(); else await addon.disable();
              if (addon.isActive !== enabled) throw new Error("Unexpected extension state");
            }
            return true;
          })().then(done, () => done(false));
        """, "args": [[CONSENT, UBLOCK], enabled], "newSandbox": True, "sandbox": "system",
    })["value"]
    require(result is True, "consent_fixture_control_addon_transition")


def read_purposes(client):
    return until(client, """
      const inputs = Array.from(document.querySelectorAll('.categorylist input'));
      return inputs.length === 6 && Object.fromEntries(inputs.map(input => [input.id, input.checked]));
    """)


def configure_consent(client, origin):
    addon_page(client, CONSENT, "options.html")
    require(read_purposes(client) == values(), "consent_fixture_unexpected_upstream_defaults")
    until(client, "return document.querySelector('.rulelist a') !== null;")
    # Use the extension's ordinary list UI. No consent/debug values are rewritten.
    for _ in range(4):
        count = client.script("return document.querySelectorAll('.rulelist button').length;")
        if count == 0:
            break
        client.script("document.querySelector('.rulelist button').click(); return true;")
        until(client, "return document.querySelectorAll('.rulelist button').length < arguments[0];", [count])
    require(client.script("return document.querySelectorAll('.rulelist button').length;") == 0)
    client.script("""
      document.querySelector('#newurl').value = arguments[0];
      document.querySelector('#urladd').click(); return true;
    """, [origin + "/rules.json"])
    until(client, """
      const links = Array.from(document.querySelectorAll('.rulelist a'));
      return links.length === 1 && links[0].href === arguments[0];
    """, [origin + "/rules.json"])
    require(read_purposes(client) == values(), "consent_fixture_changed_defaults")


def configure_ubo(client, origin):
    selected_before = ubo_selection(client)
    path = "asset-viewer.html?" + urlencode({"url": origin + "/filters.txt",
        "title": "VOLPAROSSA synthetic probe only", "subscribe": "1"})
    addon_page(client, UBLOCK, path)
    until(client, """
      return document.querySelector('#subscribeButton') !== null
        && !document.body.classList.contains('loading');
    """)
    client.script("document.querySelector('#subscribeButton').click(); return true;")
    until(client, "return document.querySelector('#subscribe').classList.contains('hide');")
    selected_after = ubo_selection(client, required=origin + "/filters.txt")
    require(set(selected_after) == set(selected_before) | {origin + "/filters.txt"},
            "consent_fixture_replaced_ubo_selection")


def ubo_selection(client, required=None):
    addon_page(client, UBLOCK, "3p-filters.html")
    return until(client, """
      const entries = Array.from(document.querySelectorAll('#lists .listEntry.checked[data-role="leaf"]'));
      const selected = entries.map(entry => entry.dataset.key).sort();
      return selected.length > 0 && (!arguments[0] || selected.includes(arguments[0])) && selected;
    """, [required])


def page_snapshot(client):
    return client.script("""
      const body = document.body, cmp = document.querySelector('#volparossa-cmp');
      const raw = localStorage.getItem('volparossa-fixture-' + body.dataset.case);
      return {stored: raw === null ? null : JSON.parse(raw), saves: body.dataset.saves,
        essential: body.dataset.essential, adProbe: body.dataset.adProbe,
        visible: cmp.getClientRects().length !== 0 && getComputedStyle(cmp).visibility !== 'hidden'};
    """)


def load_case(client, origin, case):
    navigate(client, origin + "/cmp.html?" + urlencode({"case": case}))
    until(client, """
      return document.body.dataset.essential === 'loaded'
        && ['loaded', 'blocked'].includes(document.body.dataset.adProbe);
    """)


def recorded_case(client, state, case, expected, blocked):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        page, network = page_snapshot(client), state.snapshot(case)
        try:
            validate_recorded_decision(page, network, expected, blocked)
            # A late duplicate save or delayed probe must not immediately pass.
            time.sleep(0.5)
            page, network = page_snapshot(client), state.snapshot(case)
            validate_recorded_decision(page, network, expected, blocked)
            return {"page": page, "network": network}
        except ValueError:
            time.sleep(0.1)
    raise ValueError("consent_fixture_recorded_decision_timeout")


def exercise(client, origin, state, progress):
    progress["phase"] = "signed-addon-and-callback-checks"
    addons = all_addons_active(client)
    navigation = client.script((FIXTURE_DIR / "navigation-reset.js").read_text())
    require(navigation == {"reproduced": True, "fixed": True, "unrelatedPreserved": True},
            "upstream_navigation_callback_regression_failed")
    initial = snapshot(client, list(load_defaults()) + ["privacy.trackingprotection.enabled"])
    require(all(initial[key]["effective"] == expected for key, expected in load_defaults().items()))
    require(initial["privacy.trackingprotection.enabled"]["effective"] is True)
    progress["phase"] = "configure-local-consent-rules"
    configure_consent(client, origin)
    progress["phase"] = "subscribe-local-ubo-probe"
    configure_ubo(client, origin)

    # First prove the page and all three local endpoints work without either
    # actor under test. A blocked or inert fixture must not pass as a refusal.
    progress["phase"] = "positive-baseline"
    set_control_addons(client, False)
    load_case(client, origin, "baseline")
    require(page_snapshot(client)["stored"] is None)
    client.script("document.querySelector('#save-consent').click(); return true;")
    baseline = recorded_case(client, state, "baseline", values(PURPOSES), False)

    progress["phase"] = "recorded-refusal-with-all-four-addons"
    set_control_addons(client, True)
    all_addons_active(client)
    load_case(client, origin, "refusal")
    refusal = recorded_case(client, state, "refusal", values(), True)

    progress["phase"] = "unsupported-cmp"
    load_case(client, origin, "unsupported")
    time.sleep(6)  # Exceeds the upstream engine's ordinary five-second search.
    unsupported, unsupported_network = page_snapshot(client), state.snapshot("unsupported")
    require(unsupported["stored"] is None and unsupported["saves"] == "0"
            and unsupported["visible"] and unsupported_network["decisions"] == []
            and unsupported_network["optional"] == 0 and unsupported_network["ad-probe"] == 0)

    progress["phase"] = "hidden-only-negative-control"
    load_case(client, origin, "hidden")
    hidden, hidden_network = page_snapshot(client), state.snapshot("hidden")
    require(not hidden["visible"] and hidden["stored"] is None and hidden_network["decisions"] == [])
    rejected = False
    try:
        validate_recorded_decision(hidden, hidden_network, values(), True)
    except ValueError:
        rejected = True
    require(rejected, "hidden_banner_falsely_counted_as_refusal")

    # Simulate a user's actual preference change through the extension UI, only
    # in this disposable profile. This must not be overwritten by packaging.
    progress["phase"] = "preserved-user-preference"
    addon_page(client, CONSENT, "options.html")
    require(read_purposes(client) == values())
    client.script("document.querySelector('#F').closest('li').querySelector('h2').click(); return true;")
    until(client, "return document.querySelector('#F').checked;")
    navigate(client, "about:blank")
    addon_page(client, CONSENT, "options.html")
    require(read_purposes(client) == values(("F",)), "consent_fixture_user_choice_not_saved")
    load_case(client, origin, "preference")
    preference = recorded_case(client, state, "preference", values(("F",)), True)

    progress["phase"] = "final-privacy-and-addon-checks"
    all_addons_active(client)
    final = snapshot(client, list(initial))
    require(final == initial, "consent_fixture_changed_browser_privacy_defaults")
    require(state.rule_reads > 0 and state.filter_reads > 0, "fixture_lists_not_consumed")
    return {"all_four_signed_active": addons, "baseline": baseline, "refusal": refusal,
        "unsupported": {"page": unsupported, "network": unsupported_network},
        "hidden_only_rejected": rejected, "user_preference": preference,
        "strict_tracking_protection_preserved": True, "rule_reads": state.rule_reads,
        "filter_reads": state.filter_reads, "upstream_callback_fixture": navigation}


def inside(stage, work, metadata, host_netns):
    validate_isolation(stage, work, host_netns)
    for name in ("profile", "config", "cache", "runtime", "tmp"):
        (work / name).mkdir(mode=0o700)
    (work / "profile/prefs.js").write_text('user_pref("remote.prefs.recommended", false);\n')
    report = {"schema": 1, "passed": False,
        "scope": "synthetic CMP with fixture rules and custom uBO probe; not upstream-rule, real-site or overlay proof",
        "runtime_sha256": metadata["runtime_sha256"], "extension_lock_sha256": digest(LOCK),
        "fixture_sha256": {name: digest(ROOT / name) for name in (
            "scripts/consent_fixture.py", "scripts/smoke_consent.py", "scripts/smoke_privacy.py",
            "tests/fixtures/consent/cmp.html", "tests/fixtures/consent/navigation-reset.js")},
        "host_read_only": True, "interfaces": ["lo"], "exercise": None,
        "progress": {"phase": "browser-start"}}
    try:
        with serve_fixture() as (origin, state):
            result = run_browser(stage, work, work / "profile", "consent",
                exercise=lambda client: exercise(client, origin, state, report["progress"]))
            report["exercise"] = result["exercise"]
        report["passed"] = True
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report["failure"] = type(error).__name__
    finally:
        report["temporary_profile_removed"] = remove_profile(work)
        log = work / "consent.log"
        if log.exists():
            require(not log.is_symlink())
            log.unlink()
        report["passed"] = report["passed"] and report["temporary_profile_removed"]
        with (work / "report.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    require(report["passed"], "consent_smoke_failed_see_report")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host-netns", help=argparse.SUPPRESS)
    args = parser.parse_args()
    stage, work = build_path(args.stage), build_path(args.output)
    require(not stage.is_relative_to(work) and not work.is_relative_to(stage))
    metadata = validate_stage(stage)
    validate_bundle(stage, metadata)
    if args.inside:
        inside(stage, work, metadata, args.host_netns)
        return
    require(not work.exists() and not work.is_symlink())
    work.mkdir(mode=0o700)
    mounts = isolated_browser_home(work)
    print(f"Consent smoke changes only {work}; host read-only; fresh loopback-only network.", flush=True)
    subprocess.run([
        "/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-net",
        "--unshare-pid",
        "--ro-bind", "/", "/", *mounts, "--bind", str(work), str(work),
        "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev", "--",
        sys.executable, "-B", str(Path(__file__).resolve()), "--stage", str(stage),
        "--output", str(work), "--inside", "--host-netns", os.readlink("/proc/self/ns/net"),
    ], check=True, timeout=210)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f"consent smoke failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
