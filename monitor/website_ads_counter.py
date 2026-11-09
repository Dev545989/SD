from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _get_nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list):
            try:
                index = int(key)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _find_items(payload: Any, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    candidates: List[Any] = []
    if isinstance(payload, list):
        candidates.append(payload)
    elif isinstance(payload, dict):
        for key in config.get("items_keys", ["items", "results", "ads", "listings", "data", "records"]):
            value = payload.get(key)
            if value is not None:
                candidates.append(value)
        if not candidates:
            candidates.append(payload)
    else:
        return []

    items: List[Dict[str, Any]] = []
    for candidate in candidates:
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    items.append(item)
        elif isinstance(candidate, dict):
            items.append(candidate)
    return items


def _normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        try:
            import datetime as _dt

            return _dt.datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d")
        except Exception:
            return None
    text = _coerce_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            import datetime as _dt

            return _dt.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if "T" in text:
        text = text.split("T", 1)[0]
    if len(text) >= 10:
        return text[:10]
    return None


def _get_public_id(item: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
    for field in config.get("id_fields", ["id", "listing_id", "listingId", "objectID", "externalID", "external_id"]):
        value = _get_nested(item, *field.split(".")) if "." in field else item.get(field)
        if value is None and isinstance(item, dict):
            for alt in (field.lower(), field.upper(), field.replace("_", ""), field.replace("_", "-")):
                value = item.get(alt)
                if value is not None:
                    break
        if value is not None and _coerce_text(value):
            return _coerce_text(value)
    return None


def _matches_listing_date(item: Dict[str, Any], listing_date: str, config: Dict[str, Any]) -> bool:
    date_fields = config.get("date_fields", ["date_published", "datePublished", "published_at", "publishedAt", "createdAt", "created_at", "date"])
    for field in date_fields:
        value = _get_nested(item, *field.split(".")) if "." in field else item.get(field)
        if value is None and isinstance(item, dict):
            lowered = {str(k).lower(): k for k in item.keys()}
            for key, original in lowered.items():
                if key == field.lower():
                    value = item[original]
                    break
        if value is not None:
            normalized = _normalize_date(value)
            if normalized == listing_date:
                return True
    return False


def _build_page_specs(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if config.get("pages"):
        pages = config["pages"]
        if isinstance(pages, list):
            return [dict(page) for page in pages]
    if config.get("endpoints"):
        endpoints = config["endpoints"]
        if isinstance(endpoints, list):
            return [dict(endpoint) for endpoint in endpoints]
    if config.get("categories"):
        categories = config["categories"]
        if isinstance(categories, list):
            specs: List[Dict[str, Any]] = []
            for category in categories:
                if isinstance(category, str):
                    specs.append({"url": category})
                elif isinstance(category, dict):
                    specs.append(dict(category))
            if specs:
                return specs
    return [dict(config)]


def _fetch_page(page_spec: Dict[str, Any], http_get: Optional[Callable[..., Any]] = None) -> Any:
    getter = http_get or requests.get
    url = page_spec.get("url") or page_spec.get("api_url") or page_spec.get("endpoint")
    if not url:
        raise ValueError("website source page is missing a URL")
    kwargs = dict(page_spec.get("request_kwargs") or {})
    timeout = kwargs.pop("timeout", page_spec.get("timeout", 20))
    kwargs.setdefault("timeout", timeout)
    if "headers" in page_spec and "headers" not in kwargs:
        kwargs["headers"] = page_spec["headers"]
    response = getter(url, **kwargs)
    if hasattr(response, "status_code") and response.status_code not in (200, 201, 202):
        raise ValueError(f"HTTP {response.status_code} from {url}")
    if hasattr(response, "json"):
        try:
            return response.json()
        except Exception:
            return response.text
    if hasattr(response, "text"):
        return response.text
    return response


def _parse_text_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return payload
    return payload


def _expand_paginated_pages(config: Dict[str, Any], default_page_size: int = 1) -> List[Dict[str, Any]]:
    specs = _build_page_specs(config)
    expanded: List[Dict[str, Any]] = []
    for spec in specs:
        page_count = int(spec.get("page_count") or spec.get("max_pages") or 1)
        if page_count <= 1:
            expanded.append(spec)
            continue
        template = spec.get("url_template") or spec.get("page_template") or spec.get("url")
        if not template:
            expanded.append(spec)
            continue
        page_param = spec.get("page_param", "page")
        for page_no in range(1, page_count + 1):
            if "{" in template and "}" in template:
                page_url = template.format(page=page_no)
            elif "?" in template:
                page_url = f"{template}&{page_param}={page_no}"
            else:
                page_url = f"{template}?{page_param}={page_no}"
            clone = dict(spec)
            clone["url"] = page_url
            clone["page_number"] = page_no
            expanded.append(clone)
    return expanded or specs


def count_website_published_ads(site_or_source: Dict[str, Any], listing_date: str, http_get: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """
    Count ads whose public website publication date matches the listing date.

    The source configuration can be placed under any of these site keys:
      website_source, website_monitor, website_public_source, website_public_api

    Supported config patterns:
      - direct JSON payloads from a list of URLs / category endpoints
      - dicts with a "pages" / "endpoints" list, where each page has a URL and
        JSON payload shape such as {"items": [...], "results": [...], "ads": [...]}
      - a simple dict with an "items" or nested "data" list, plus optional
        pagination fields like "page_count" / "page_param" / "url_template"
    """
    source = site_or_source
    if not isinstance(source, dict):
        return {
            "website_published_ads": None,
            "website_published_ads_date": listing_date,
            "website_published_ads_source": "invalid",
            "website_published_ads_status": "error",
            "website_published_ads_error": "website source configuration must be a dict",
            "website_published_ads_diagnostics": {"source_keys": []},
        }

    config = (
        source.get("website_source")
        or source.get("website_monitor")
        or source.get("website_public_source")
        or source.get("website_public_api")
    )
    if config is None and any(key in source for key in ("pages", "endpoints", "categories", "source_name", "api_url", "url_template", "url")):
        config = source
    if not config:
        return {
            "website_published_ads": None,
            "website_published_ads_date": listing_date,
            "website_published_ads_source": "none",
            "website_published_ads_status": "error",
            "website_published_ads_error": "no website source configured",
            "website_published_ads_diagnostics": {"page_count": 0, "item_count": 0},
        }

    config = dict(config)
    source_name = config.get("source_name") or config.get("name") or "website"
    date_fields = config.get("date_fields", ["date_published", "datePublished", "published_at", "publishedAt", "createdAt", "created_at", "date"])
    id_fields = config.get("id_fields", ["id", "listing_id", "listingId", "objectID", "externalID", "external_id"])
    config.setdefault("date_fields", date_fields)
    config.setdefault("id_fields", id_fields)

    if config.get("enabled") is False:
        return {
            "website_published_ads": None,
            "website_published_ads_date": listing_date,
            "website_published_ads_source": source_name,
            "website_published_ads_status": "error",
            "website_published_ads_error": "website source is disabled",
            "website_published_ads_diagnostics": {"page_count": 0, "item_count": 0},
        }

    seen_ids: set[str] = set()
    total_items = 0
    pages_seen = 0
    failed_page: Optional[str] = None
    matching_ids: List[str] = []

    try:
        for page_spec in _expand_paginated_pages(config):
            pages_seen += 1
            payload = _fetch_page(page_spec, http_get=http_get)
            payload = _parse_text_payload(payload)
            items = _find_items(payload, config)
            total_items += len(items)

            for item in items:
                if not isinstance(item, dict):
                    continue
                if not _matches_listing_date(item, listing_date, config):
                    continue
                item_id = _get_public_id(item, config)
                if item_id is None:
                    continue
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    matching_ids.append(item_id)
    except Exception as exc:  # pragma: no cover - exercised via tests
        return {
            "website_published_ads": None,
            "website_published_ads_date": listing_date,
            "website_published_ads_source": source_name,
            "website_published_ads_status": "error",
            "website_published_ads_error": str(exc),
            "website_published_ads_diagnostics": {
                "page_count": pages_seen,
                "item_count": total_items,
                "failed_page": failed_page,
            },
        }

    return {
        "website_published_ads": len(matching_ids),
        "website_published_ads_date": listing_date,
        "website_published_ads_source": source_name,
        "website_published_ads_status": "ok",
        "website_published_ads_error": None,
        "website_published_ads_diagnostics": {
            "page_count": pages_seen,
            "item_count": total_items,
            "deduplicated_ids": len(matching_ids),
        },
    }
