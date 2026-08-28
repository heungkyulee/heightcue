#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 진입점 — 승인된 상품의 공식 이미지를 truth layer 로 스테이징한다.

`product_assets.acquire_product_assets()` 를 **실제 fetcher** 로 구동한다.
가드는 하나도 우회하지 않는다 — provenance 는 이 파일에서 명시적으로 적어 넣고,
호스트 allowlist·매직 바이트·크기·해시 검증은 전부 모듈이 수행한다.

사용:
    ../.venv/bin/python stage_assets.py <spec.json>

spec.json 은 acquire_product_assets 가 받는 product dict 그대로다.
"""

from __future__ import annotations

import json
import sys

import product_assets


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        product = json.load(fh)
    workspace = product.pop("_workspace")
    manifest = product_assets.acquire_product_assets(product, workspace)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
