# -*- coding: utf-8 -*-
"""Company OS Supabase adapter for HeightCue commerce workflow.

Fail-closed by design: US commerce never falls back to a local product registry.
Credentials are read from the existing Company OS env file and never logged.
"""
import json
import math
import os
import socket
import urllib.error
import urllib.request


ENV_PATH = os.path.expanduser("~/.config/lifoli/companyos.env")
DEFAULT_OWNER = "yujin-threads-us"
USD_PRICE_BANDS = ((15, "US_UNDER_15"), (30, "US_15_30"),
                   (50, "US_30_50"), (math.inf, "US_50_PLUS"))


class CompanyOSError(RuntimeError):
    pass


def price_band(price_info, explicit=None):
    """Preserve an explicit band or derive one from a typed USD amount."""
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if not isinstance(price_info, dict) or price_info.get("currency") != "USD":
        raise CompanyOSError("Company OS claim contract requires USD price_info or price_band")
    amount = price_info.get("amount")
    if (isinstance(amount, bool) or not isinstance(amount, (int, float))
            or not math.isfinite(float(amount)) or amount < 0):
        raise CompanyOSError("Company OS claim contract requires typed USD price amount")
    return next(label for ceiling, label in USD_PRICE_BANDS if amount < ceiling)


def normalize_product(row):
    """Return a Company OS product with one authoritative price-band rule."""
    normalized = dict(row)
    normalized["price_band"] = price_band(
        normalized.get("price_info"), normalized.get("price_band")
    )
    return normalized


def _load_credentials(path=ENV_PATH):
    url = os.environ.get("COMPANYOS_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = os.environ.get("COMPANYOS_SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return url.rstrip("/"), key
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                env_key, value = line.split("=", 1)
                values[env_key] = value.strip()
    except OSError as exc:
        raise CompanyOSError(f"Company OS credentials unavailable: {path}") from exc
    url = values.get("SUPABASE_URL", "").rstrip("/")
    key = values.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise CompanyOSError("Company OS Supabase URL/service key missing")
    return url, key


def _env(path=ENV_PATH):
    """Backward-compatible alias for callers and tests."""
    return _load_credentials(path)


def rpc(function, payload, timeout=30):
    url, key = _env()
    request = urllib.request.Request(
        f"{url}/rest/v1/rpc/{function}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout) as exc:
        detail = ""
        if isinstance(exc, urllib.error.HTTPError):
            try:
                detail = exc.read().decode("utf-8")[:500]
            except Exception:
                detail = ""
        raise CompanyOSError(f"Company OS RPC {function} failed: {getattr(exc, 'code', '')} {detail}".strip()) from exc


def _call(transport, function, payload):
    return (transport or rpc)(function, payload)


def get_product(product_key, transport=None):
    """Return the currently approved evidence packet for generation provenance."""
    row = _call(transport, "hc_get_approved_product_input", {"p_product_key": product_key})
    if isinstance(row, list):
        row = row[0] if row else None
    if not isinstance(row, dict) or row.get("product_key") != product_key:
        raise CompanyOSError("approved Company OS product input unavailable")
    return normalize_product(row)


def claim_us_product(cfg, owner=DEFAULT_OWNER, transport=None):
    lease = int((cfg.get("mode") or {}).get("us_product_lease_seconds", 1800))
    row = _call(transport, "hc_claim_active_product", {
        "p_market": "US", "p_owner": owner, "p_lease_seconds": lease,
    })
    if not row:
        return None
    if isinstance(row, list):
        row = row[0] if row else None
    if not row:
        return None
    required = ("product_key", "product_name", "workflow_id", "claim_token",
                "evidence_revision", "landing_url", "offer_id", "product_id")
    missing = [key for key in required if not row.get(key)]
    if missing:
        raise CompanyOSError("Company OS claim contract missing: " + ", ".join(missing))
    row = normalize_product(row)
    price_info = row.get("price_info") or {}
    return {
        "product_key": row["product_key"], "country": "US",
        "category": row.get("category") or "nutrition",
        "product_name": row["product_name"],
        "friction_id": row.get("friction_id"),
        "source_pointers": row.get("source_pointers") or [],
        "mechanism": row.get("mechanism"),
        "failure_mode": row.get("failure_mode"),
        "skip_if": row.get("skip_if"),
        "formfactor_id": row.get("formfactor_id"),
        "ux_grade": row.get("ux_grade"),
        "is_food": bool(row.get("is_food", False)),
        "is_certified_health_food": bool(row.get("is_certified_health_food", False)),
        "approved_claims": row.get("approved_claims") or [],
        "price_info": price_info,
        "price_band": row["price_band"],
        "review_count": row.get("review_count"),
        "review_rating": row.get("review_rating"),
        "review_quotes": row.get("review_quotes") or [],
        "spec_facts": row.get("spec_facts") or [],
        "compared_candidates": row.get("compared_candidates") or [],
        "rejected_candidates": row.get("rejected_candidates") or [],
        "claim_boundary": row.get("claim_boundary") or {},
        "link": row["landing_url"],
        "affiliate_url": row.get("affiliate_url"),
        "sub_id": row.get("sub_id") or row.get("tracking_key"),
        "link_mode": "site",
        "_workflow": {key: row.get(key) for key in (
            "workflow_id", "claim_token", "lease_expires_at", "evidence_revision",
            "offer_id", "product_id", "tracking_key")},
    }


def release_product_claim(product, outcome, metadata=None, transport=None):
    workflow = product.get("_workflow") or {}
    token = workflow.get("claim_token")
    if not token or not product.get("product_key"):
        raise CompanyOSError("cannot release product without product_key and claim_token")
    return _call(transport, "hc_release_product_claim", {
        "p_product_key": product["product_key"], "p_claim_token": token,
        "p_outcome": str(outcome), "p_metadata": metadata or {},
    })


def record_product_publication(product, *, media_id, publication_url, text,
                               tracking_key, sub_id, readback_verified,
                               transport=None):
    workflow = product.get("_workflow") or {}
    if not readback_verified:
        raise CompanyOSError("publication requires coupled remote readback")
    if not workflow.get("claim_token") or not media_id or not publication_url:
        raise CompanyOSError("publication requires claim token, media id and verified permalink")
    payload = {
        "external_media_id": str(media_id), "publication_url": publication_url or "",
        "text": text, "readback_verified": True,
        "tracking_key": tracking_key or workflow.get("tracking_key") or "",
        "sub_id": sub_id or "", "channel": "threads", "country_code": "US",
    }
    return _call(transport, "hc_record_product_publication", {
        "p_product_key": product.get("product_key"),
        "p_claim_token": workflow["claim_token"],
        "p_publication": payload,
    })


def workflow_health(transport=None):
    return _call(transport, "hc_product_workflow_health", {})
