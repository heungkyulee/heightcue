#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the public HeightCue journey from live execution packets."""

from __future__ import annotations

import argparse
from pathlib import Path

import site_journey
from site_packets import load_companyos_packets


DEFAULT_REPO = Path(__file__).resolve().parent.parent
DEFAULT_LIFOLI = Path.home() / "lifoli"
RETIRED_LANDINGS = [
    {"path": "kr/p/153976571-444051272.html", "market": "KR", "title": "에버라스트 인플레이터블 밥 백"},
    {"path": "kr/p/kr-sleepcomfort-junior-milkpillow-plus-55x34x8.html", "market": "KR", "title": "수면공감 주니어용 경추 우유베개 플러스"},
]


def build(output_root, lifoli_root=DEFAULT_LIFOLI, packet_loader=load_companyos_packets):
    packets = packet_loader(Path(lifoli_root))
    return site_journey.build_site(Path(output_root), packets, retired=RETIRED_LANDINGS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--lifoli", type=Path, default=DEFAULT_LIFOLI)
    args = parser.parse_args(argv)
    manifest = build(args.output, args.lifoli)
    print(f"built {len(manifest['routes'])} routes with {len(manifest['products'])} products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
