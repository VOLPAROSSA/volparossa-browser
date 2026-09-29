#!/usr/bin/env python3
"""Apply the exact-pinned Firefox source integration; no full clone or browser install."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REVISION = "47c5f402c8d3a5369f1fb1b6cd61b0bb92725af1"
BASE = f"https://raw.githubusercontent.com/mozilla-firefox/firefox/{REVISION}/"
PREFIX = "browser/components/genai/"

# Each anchor is checked exactly once against the pinned source. Existing MPL notices remain.
EDITS = {
    PREFIX + "GenAI.sys.mjs": [
        ('  chatProviders: new Map([\n', '  chatProviders: new Map([\n    ["volparossa:private", { id: "volparossa", name: "Project VOLPAROSSA (private local)", tooltipId: "volparossa-private-provider-tooltip" }],\n'),
        ('  async handleAskChat(promptObj, context) {\n', '''  async handleAskChat(promptObj, context) {
    // Explicit private actions never enter Smart Window or provider URL/header handling.
    if (lazy.chatProvider === "volparossa:private") {
      const win = context.window?.browsingContext?.topChromeWindow ?? context.window;
      await win.SidebarController.show("viewGenaiChatSidebar");
      const sidebar = win.SidebarController.browser.contentWindow;
      await sidebar.browserPromise;
      await sidebar.volparossaAsk(promptObj, context);
      return;
    }
'''),
        ('  const ordered = lazy.chatProviders.split(",");\n', '  const ordered = ["volparossa", ...lazy.chatProviders.split(",").filter(id => id !== "volparossa")];\n'),
    ],
    PREFIX + "chat.js": [
        ('  GenAI: "resource:///modules/GenAI.sys.mjs",\n', '  GenAI: "resource:///modules/GenAI.sys.mjs",\n  createVolparossaComputePanel: "resource:///modules/VolparossaComputePanel.sys.mjs",\n'),
        ('function request(url = lazy.providerPref) {\n', '''async function volparossaAsk(promptObj, context) {
  await browserPromise;
  request("volparossa:private");
  await node.volparossa.ask(promptObj.label || promptObj.value || "", context.selection || "");
}

function request(url = lazy.providerPref) {
  if (url === "volparossa:private") {
    if (!node.volparossa) {
      // Release any previous provider document before displaying private local context.
      node.chat.fixupAndLoadURIString("about:blank", {
        triggeringPrincipal: Services.scriptSecurityManager.createNullPrincipal({}),
      });
      node.volparossa = lazy.createVolparossaComputePanel(document, document.getElementById("browser-container"));
    }
    node.chat.hidden = true;
    return;
  }
  node.volparossa?.destroy();
  node.volparossa = null;
  node.chat.hidden = false;
'''),
        ('addEventListener("unload", () => {\n', 'addEventListener("unload", () => {\n  node.volparossa?.destroy();\n'),
        ('          request(config.url);\n', '''          if (config.id === "volparossa") {
            request(config.url);
            document.querySelector(".primary").disabled = false;
            document.querySelector(".link-paragraph")?.replaceChildren();
            return;
          }
          request(config.url);
'''),
    ],
    PREFIX + "chat.html": [
        ('    <link rel="stylesheet" href="chrome://browser/content/genai/chat.css" />\n', '    <link rel="stylesheet" href="chrome://browser/content/genai/chat.css" />\n    <link rel="stylesheet" href="chrome://browser/content/genai/volparossa-compute.css" />\n'),
    ],
    PREFIX + "moz.build": [
        ('    "GenAI.sys.mjs",\n', '    "GenAI.sys.mjs",\n    "VolparossaCompute.sys.mjs",\n    "VolparossaComputePanel.sys.mjs",\n'),
    ],
    PREFIX + "jar.mn": [
        ('    content/browser/genai/chat.css\n', '    content/browser/genai/chat.css\n    content/browser/genai/volparossa-compute.css\n'),
    ],
}
LOCALE = "browser/locales/en-US/browser/genai.ftl"


def transform(path, source):
    for old, new in EDITS.get(path, []):
        if source.count(old) != 1:
            raise ValueError(f"pinned source anchor changed or ambiguous: {path}")
        source = source.replace(old, new, 1)
    if path == LOCALE:
        source += "\n# Project VOLPAROSSA local provider; no cloud endpoint.\nvolparossa-private-provider-tooltip = Private local compute through your VOLPAROSSA broker\n"
    return source


def prepare(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "build") or output == ROOT / "build" or output.exists():
        raise ValueError("output must be a new child of this repository's build/")
    output.mkdir(parents=True)
    patches = []
    records = {}
    pinned = json.loads((ROOT / "patches/firefox-source.json").read_text())
    if pinned["upstream_revision"] != REVISION:
        raise ValueError("source revision differs from committed provenance")
    for path in [*EDITS, LOCALE]:
        with urllib.request.urlopen(BASE + path, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024)
            if response.read(1):
                raise ValueError("unexpected oversized pinned upstream file")
        source = raw.decode("utf-8", "strict")
        if hashlib.sha256(raw).hexdigest() != pinned["upstream_sha256"].get(path):
            raise ValueError(f"pinned upstream source hash changed: {path}")
        patched = transform(path, source)
        for kind, data in (("original", source), ("patched", patched)):
            destination = output / kind / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(data)
        patches.extend(difflib.unified_diff(source.splitlines(keepends=True), patched.splitlines(keepends=True), fromfile="a/" + path, tofile="b/" + path))
        records[path] = hashlib.sha256(raw).hexdigest()
    for name in ("VolparossaCompute.sys.mjs", "VolparossaComputePanel.sys.mjs", "volparossa-compute.css"):
        raw = (ROOT / "integration" / name).read_text()
        path = PREFIX + name
        destination = output / "patched" / path
        destination.write_text(raw)
    patch = "".join(patches)
    if patch != (ROOT / "patches/0001-private-compute-sidebar.patch").read_text():
        raise ValueError("source transformation differs from committed patch")
    (output / "private-compute-sidebar.patch").write_text(patch)
    (output / "source.json").write_text(json.dumps({
        "upstream_revision": REVISION, "upstream_sha256": records,
        "kind": "exact-source-overlay-not-a-firefox-build",
    }, indent=2) + "\n")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(prepare(args.output))
