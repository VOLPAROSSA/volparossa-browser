#!/usr/bin/env python3
"""Maintainer exports using Pillow 12.3.0; never required by the site build."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/browser'
# All coordinates are in untouched master pixels. No generated letters or UI.
COMPOSITIONS = {
 'hero-station': {'master':'hero-station','desktop':((0,0,1774,887),[960,1440,1774]),'mobile':((810,0,1774,887),[480,768])},
 'firefox-station-surround': {'master':'firefox-station-surround','desktop':((0,0,1774,887),[960,1440,1774])},
 'shared-foundation-station': {'master':'shared-foundation-station','desktop':((0,0,1983,793),[960,1440,1983])},
 'core-network': {'master':'shared-foundation-station','desktop':((0,210,660,793),[480,660])},
 'core-content': {'master':'shared-foundation-station','desktop':((660,210,1320,793),[480,660])},
 'core-brain': {'master':'shared-foundation-station','desktop':((1230,210,1983,793),[480,753])},
 'connection-flexible': {'master':'connection-routes','desktop':((0,0,1086,724),[480,768,1086])},
 'connection-required': {'master':'connection-routes','desktop':((1086,0,2172,724),[480,768,1086])},
 'closing-station': {'master':'closing-station','desktop':((0,0,1942,809),[960,1440,1942]),'mobile':((820,0,1942,809),[480,768])}
}


def inventory():
    manifest = {'compositions': COMPOSITIONS, 'social_crop': [84, 0, 1774, 887], 'files': {}}
    for directory in [ROOT / 'masters', ROOT / 'assets']:
        for p in sorted(directory.rglob('*')):
            if not p.is_file():
                continue
            record = {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
            if p.suffix in ['.png', '.jpg', '.webp', '.avif']:
                with Image.open(p) as im:
                    record['width'], record['height'] = im.size
            manifest['files'][p.relative_to(ROOT).as_posix()] = record
    (ROOT / 'asset-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def export():
    OUT.mkdir(parents=True, exist_ok=True)
    # Remove only obsolete variants listed by our previous export manifest.
    previous = json.loads((OUT / 'variants.json').read_text()) if (OUT / 'variants.json').exists() else {}
    planned = {f'{name}-{kind}-{width}' for name, plan in COMPOSITIONS.items()
               for kind in ['desktop', 'mobile'] if kind in plan for width in plan[kind][1]}
    for composition in previous.values():
        for entries in composition.values():
            for entry in entries:
                stem = entry['stem']
                if stem not in planned and Path(stem).name == stem:
                    for suffix in ['.avif', '.webp', '.jpg']:
                        (OUT / (stem + suffix)).unlink(missing_ok=True)
    variants = {}
    for name, plan in COMPOSITIONS.items():
        source = Image.open(ROOT / 'masters' / (plan['master'] + '.png')).convert('RGB')
        variants[name] = {}
        for kind in ['desktop', 'mobile']:
            if kind not in plan:
                continue
            box, widths = plan[kind]
            crop = source.crop(box)
            variants[name][kind] = []
            for width in widths:
                assert width <= crop.width, 'Do not enlarge source pixels'
                size = (width, round(width * crop.height / crop.width))
                image = crop.resize(size, Image.Resampling.LANCZOS)
                stem = f'{name}-{kind}-{width}'
                image.save(OUT / (stem + '.avif'), quality=57, speed=6)
                image.save(OUT / (stem + '.webp'), quality=83, method=6)
                image.save(OUT / (stem + '.jpg'), quality=84, optimize=True, progressive=True)
                variants[name][kind].append({'stem': stem, 'width': size[0], 'height': size[1]})
    hero = Image.open(ROOT / 'masters/hero-station.png').convert('RGB').crop((84, 0, 1774, 887))
    hero.resize((1200, 630), Image.Resampling.LANCZOS).save(OUT / 'social.jpg', quality=85, optimize=True)
    # A real isolated Firefox UI capture, kept distinct from generated scenery.
    interface = Image.open(ROOT / 'masters/firefox-interface.png').convert('RGB')
    for width in [640, 1280]:
        interface.resize((width, round(interface.height * width / interface.width)), Image.Resampling.LANCZOS).save(ROOT / f'assets/upstream/firefox-interface-{width}.webp', quality=88, method=6)
    interface.save(ROOT / 'assets/upstream/firefox-interface-1280.jpg', quality=88, optimize=True, progressive=True)
    (OUT / 'variants.json').write_text(json.dumps(variants, indent=2) + '\n')
    inventory()
    print('Exported responsive scenes and registered dimensions, crops and hashes.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory-only', action='store_true')
    args = p.parse_args()
    inventory() if args.inventory_only else export()
