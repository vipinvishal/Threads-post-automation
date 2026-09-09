#!/usr/bin/env python3
"""Reconcile published Threads posts and collect first-party post insights.

The publisher currently returns a Buffer ID, not a Threads media ID. This job
matches recent published Threads posts to history by normalized text, then asks
the official Threads Insights API for cumulative metrics. It is deliberately
optional: without THREADS_ACCESS_TOKEN it exits successfully and changes no
history.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "post_history.json"
ACCOUNT_INSIGHTS_PATH = SCRIPT_DIR / "account_insights.json"
API_HOST = os.environ.get("THREADS_API_HOST", "https://graph.threads.net/v1.0").rstrip("/")
POST_METRICS = ("views", "likes", "replies", "reposts", "quotes", "shares")
ACCOUNT_METRICS = ("views", "likes", "replies", "reposts", "quotes", "clicks", "followers_count")


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").lower())[:260]


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_history(path: Path = HISTORY_PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_history(history: list[dict], path: Path = HISTORY_PATH) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def fetch_recent_threads(token: str, days: int = 14, timeout: int = 30) -> list[dict]:
    response = requests.get(
        f"{API_HOST}/me/threads",
        params={
            "fields": "id,text,timestamp,permalink,media_type",
            "limit": 100,
            "access_token": token,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [
        item for item in response.json().get("data", [])
        if (_parse_time(item.get("timestamp")) or cutoff) >= cutoff
    ]


def match_thread(entry: dict, threads: list[dict]) -> dict | None:
    """Match on normalized opening text and a loose publish-time window."""
    wanted = _normalize(entry.get("post_text") or entry.get("hook", ""))
    if len(wanted) < 16:
        return None
    entry_time = _parse_time(entry.get("timestamp", ""))
    candidates = []
    for item in threads:
        actual = _normalize(item.get("text", ""))
        if not actual or not (actual.startswith(wanted[:80]) or wanted.startswith(actual[:80])):
            continue
        item_time = _parse_time(item.get("timestamp", ""))
        distance = abs((item_time - entry_time).total_seconds()) if item_time and entry_time else 0
        if distance <= 48 * 3600:
            candidates.append((distance, item))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


def _metric_value(item: dict) -> float:
    total = item.get("total_value")
    if isinstance(total, dict) and "value" in total:
        value = total.get("value", 0)
    else:
        values = item.get("values") or []
        value = values[-1].get("value", 0) if values and isinstance(values[-1], dict) else item.get("value", 0)
    if isinstance(value, dict):
        value = sum(v for v in value.values() if isinstance(v, (int, float)))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_account_insights(token: str, timeout: int = 30) -> dict:
    now = datetime.now(timezone.utc)
    response = requests.get(
        f"{API_HOST}/me/threads_insights",
        params={
            "metric": ",".join(ACCOUNT_METRICS),
            "since": int((now - timedelta(days=1)).timestamp()),
            "until": int(now.timestamp()),
            "access_token": token,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    metrics = {name: 0.0 for name in ACCOUNT_METRICS}
    for item in response.json().get("data", []):
        name = item.get("name")
        if name in metrics:
            metrics[name] = _metric_value(item)
    return {key: int(value) if value.is_integer() else value for key, value in metrics.items()}


def _save_account_snapshot(metrics: dict, path: Path = ACCOUNT_INSIGHTS_PATH) -> None:
    try:
        snapshots = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(snapshots, list):
            snapshots = []
    except (FileNotFoundError, json.JSONDecodeError):
        snapshots = []
    snapshots.append({"collected_at": datetime.now(timezone.utc).isoformat(), **metrics})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshots[-90:], indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def fetch_post_insights(thread_id: str, token: str, timeout: int = 30) -> dict:
    response = requests.get(
        f"{API_HOST}/{thread_id}/insights",
        params={"metric": ",".join(POST_METRICS), "access_token": token},
        timeout=timeout,
    )
    response.raise_for_status()
    metrics = {name: 0.0 for name in POST_METRICS}
    for item in response.json().get("data", []):
        name = item.get("name")
        if name in metrics:
            metrics[name] = _metric_value(item)
    return {key: int(value) if value.is_integer() else value for key, value in metrics.items()}


def reconcile_history(path: Path = HISTORY_PATH) -> dict:
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        print("[ Insights ] THREADS_ACCESS_TOKEN not set; leaving metrics unchanged.")
        return {"configured": False, "matched": 0, "updated": 0}

    try:
        account_metrics = fetch_account_insights(token)
        _save_account_snapshot(account_metrics)
        account_updated = True
    except requests.RequestException as exc:
        print(f"[ Insights ] Account snapshot skipped: {exc}")
        account_updated = False

    history = _load_history(path)
    try:
        threads = fetch_recent_threads(token)
    except requests.RequestException as exc:
        print(f"[ Insights ] Recent post reconciliation skipped: {exc}")
        return {
            "configured": True, "matched": 0, "updated": 0,
            "account_updated": account_updated,
        }
    matched = updated = 0
    collected_at = datetime.now(timezone.utc)

    for entry in history:
        thread_id = entry.get("threads_post_id")
        thread = next((item for item in threads if str(item.get("id")) == str(thread_id)), None)
        if not thread:
            thread = match_thread(entry, threads)
        if not thread:
            continue
        matched += 1
        entry["threads_post_id"] = str(thread.get("id"))
        entry["permalink"] = thread.get("permalink", entry.get("permalink", ""))
        entry["publish_status"] = "published"
        try:
            metrics = fetch_post_insights(entry["threads_post_id"], token)
        except requests.RequestException as exc:
            print(f"  [Insights] {entry['threads_post_id']} skipped: {exc}")
            continue

        published_at = _parse_time(thread.get("timestamp", "")) or _parse_time(entry.get("timestamp", ""))
        age_hours = round((collected_at - published_at).total_seconds() / 3600, 1) if published_at else None
        snapshot = {"collected_at": collected_at.isoformat(), "age_hours": age_hours, **metrics}
        snapshots = entry.get("insight_snapshots") if isinstance(entry.get("insight_snapshots"), list) else []
        snapshots.append(snapshot)
        entry["insight_snapshots"] = snapshots[-12:]
        entry["insights"] = metrics
        if age_hours is not None and 12 <= age_hours <= 48:
            current_benchmark = entry.get("benchmark_insights")
            current_age = current_benchmark.get("age_hours") if isinstance(current_benchmark, dict) else None
            if current_age is None or abs(age_hours - 24) < abs(float(current_age) - 24):
                entry["benchmark_insights"] = snapshot
        updated += 1

    if updated or matched:
        _save_history(history, path)
    print(f"[ Insights ] Matched {matched} history entries; refreshed {updated} insight snapshots.")
    return {"configured": True, "matched": matched, "updated": updated, "account_updated": account_updated}


if __name__ == "__main__":
    reconcile_history()
