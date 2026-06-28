#!/usr/bin/env python3
"""
fetch_apm_telemetry.py
Queries the Elasticsearch cluster directly to pull Elastic APM metrics for WHMCS.
Allows split thresholds: transaction/API latency vs. database query latency.
"""

import os
import json
import ssl
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from datetime import datetime, timedelta

# =============================================================================
# 1. CONFIGURATION
# =============================================================================
ES_HOST = os.environ.get("ES_HOST", "http://12.34.56.78:9200").rstrip("/")
ES_USERNAME = os.environ.get("ES_USERNAME", "elasticsearch_cluster_username")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "your_elastic_password_here")
ES_API_KEY = os.environ.get("ES_API_KEY", "")

# APM Service name
SERVICE_NAME = os.environ.get("APM_SERVICE_NAME", "whmcs")

# APM Index pattern
APM_INDEX = os.environ.get("APM_INDEX", "traces-apm*,apm-*")

# Time window for analysis
TIME_WINDOW_HOURS = int(os.environ.get("TIME_WINDOW_HOURS", "24"))

# Latency threshold for transactions and external APIs (default: 1000ms)
LATENCY_THRESHOLD_MS = float(os.environ.get("LATENCY_THRESHOLD_MS", "1000.0"))

# Latency threshold specifically for DB queries (default: 50ms)
DB_LATENCY_THRESHOLD_MS = float(os.environ.get("DB_LATENCY_THRESHOLD_MS", "50.0"))

# =============================================================================
# 2. HELPER FUNCTIONS FOR REST CALLS
# =============================================================================
def send_es_request(path, payload):
    url = f"{ES_HOST}/{path}"
    headers = {
        "Content-Type": "application/json",
    }
    
    if ES_API_KEY:
        headers["Authorization"] = f"ApiKey {ES_API_KEY}"
    elif ES_USERNAME and ES_PASSWORD:
        import base64
        auth_str = f"{ES_USERNAME}:{ES_PASSWORD}"
        auth_bytes = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        headers["Authorization"] = f"Basic {auth_bytes}"
        
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urlopen(req, context=ctx) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        print(f"[-] HTTP Error {e.code}: {e.reason}")
        print(e.read().decode("utf-8"))
        raise
    except Exception as e:
        print(f"[-] Connection Error: {e}")
        raise

# =============================================================================
# 3. QUERIES
# =============================================================================

def fetch_latency_percentiles(start_time_iso):
    """Fetches p50, p95, and p99 transaction duration percentiles."""
    query = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"service.name": SERVICE_NAME}},
                    {"term": {"processor.event": "transaction"}},
                    {"range": {"@timestamp": {"gte": start_time_iso}}}
                ]
            }
        },
        "aggs": {
            "latencies": {
                "percentiles": {
                    "field": "transaction.duration.us",
                    "percents": [50, 95, 99]
                }
            }
        }
    }
    
    try:
        res = send_es_request(f"{APM_INDEX}/_search", query)
        percentiles = res["aggregations"]["latencies"]["values"]
        p50 = percentiles.get("50.0", 0) / 1000.0
        p95 = percentiles.get("95.0", 0) / 1000.0
        p99 = percentiles.get("99.0", 0) / 1000.0
        return p50, p95, p99
    except Exception:
        return None

def fetch_slowest_transactions(start_time_iso):
    """Fetches unique transactions exceeding the latency threshold."""
    query = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"service.name": SERVICE_NAME}},
                    {"term": {"processor.event": "transaction"}},
                    {"range": {"@timestamp": {"gte": start_time_iso}}}
                ]
            }
        },
        "aggs": {
            "by_name": {
                "terms": {
                    "field": "transaction.name",
                    "size": 1000,
                    "order": {"avg_latency": "desc"}
                },
                "aggs": {
                    "avg_latency": {
                        "avg": {"field": "transaction.duration.us"}
                    }
                }
            }
        }
    }
    
    try:
        res = send_es_request(f"{APM_INDEX}/_search", query)
        buckets = res["aggregations"]["by_name"]["buckets"]
        transactions = []
        for b in buckets:
            avg_ms = (b["avg_latency"]["value"] or 0) / 1000.0
            if avg_ms >= LATENCY_THRESHOLD_MS:
                transactions.append({
                    "name": b["key"],
                    "count": b["doc_count"],
                    "avg_ms": avg_ms
                })
        transactions.sort(key=lambda x: x["avg_ms"] * x["count"], reverse=True)
        return transactions
    except Exception:
        return []

def fetch_slowest_spans(start_time_iso, span_type="db"):
    """Fetches spans of a specific type exceeding the appropriate latency threshold."""
    query = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"service.name": SERVICE_NAME}},
                    {"term": {"processor.event": "span"}},
                    {"term": {"span.type": span_type}},
                    {"range": {"@timestamp": {"gte": start_time_iso}}}
                ]
            }
        },
        "aggs": {
            "by_span_name": {
                "terms": {
                    "field": "span.name",
                    "size": 1000,
                    "order": {"avg_duration": "desc"}
                },
                "aggs": {
                    "avg_duration": {
                        "avg": {"field": "span.duration.us"}
                    }
                }
            }
        }
    }
    
    # Choose threshold based on span type
    threshold = DB_LATENCY_THRESHOLD_MS if span_type == "db" else LATENCY_THRESHOLD_MS
    
    try:
        res = send_es_request(f"{APM_INDEX}/_search", query)
        buckets = res["aggregations"]["by_span_name"]["buckets"]
        spans = []
        for b in buckets:
            avg_ms = (b["avg_duration"]["value"] or 0) / 1000.0
            if avg_ms >= threshold:
                spans.append({
                    "name": b["key"],
                    "count": b["doc_count"],
                    "avg_ms": avg_ms
                })
        spans.sort(key=lambda x: x["avg_ms"] * x["count"], reverse=True)
        return spans
    except Exception:
        return []

def fetch_high_impact_dependencies(start_time_iso):
    """Fetches all spans (dependencies) grouped by type and name, prioritized by cumulative impact."""
    query = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"term": {"service.name": SERVICE_NAME}},
                    {"term": {"processor.event": "span"}},
                    {"range": {"@timestamp": {"gte": start_time_iso}}}
                ]
            }
        },
        "aggs": {
            "by_type": {
                "terms": {
                    "field": "span.type",
                    "size": 100
                },
                "aggs": {
                    "by_name": {
                        "terms": {
                            "field": "span.name",
                            "size": 500
                        },
                        "aggs": {
                            "avg_duration": {
                                "avg": {"field": "span.duration.us"}
                            }
                        }
                    }
                }
            }
        }
    }
    
    try:
        res = send_es_request(f"{APM_INDEX}/_search", query)
        type_buckets = res["aggregations"]["by_type"]["buckets"]
        dependencies = []
        for type_bucket in type_buckets:
            span_type = type_bucket["key"]
            name_buckets = type_bucket["by_name"]["buckets"]
            for name_bucket in name_buckets:
                span_name = name_bucket["key"]
                count = name_bucket["doc_count"]
                avg_ms = (name_bucket["avg_duration"]["value"] or 0) / 1000.0
                dependencies.append({
                    "type": span_type,
                    "name": span_name,
                    "count": count,
                    "avg_ms": avg_ms
                })
        dependencies.sort(key=lambda x: x["avg_ms"] * x["count"], reverse=True)
        return dependencies
    except Exception as e:
        print(f"[-] Error fetching dependencies: {e}")
        return []

# =============================================================================
# 4. MAIN RUNNER
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("           WHMCS Elastic APM Telemetry Fetcher")
    print("=" * 60)
    
    # Calculate timestamps
    try:
        time_threshold = datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)
    except NameError:
        from datetime import timezone
        try:
            time_threshold = datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)
        except Exception:
            time_threshold = datetime.utcnow() - timedelta(hours=TIME_WINDOW_HOURS)
            
    start_time_iso = time_threshold.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    
    print(f"[*] Querying Elasticsearch Host: {ES_HOST}")
    print(f"[*] Targeting APM Service     : {SERVICE_NAME}")
    print(f"[*] Time Range                : Last {TIME_WINDOW_HOURS} hours (since {start_time_iso})")
    print(f"[*] Transaction/API Threshold : >= {LATENCY_THRESHOLD_MS} ms")
    print(f"[*] DB Query Latency Threshold: >= {DB_LATENCY_THRESHOLD_MS} ms")
    print("-" * 60)
    
    # 1. Fetch Latency Percentiles
    print("[*] Fetching general latency percentiles...")
    percentiles = fetch_latency_percentiles(start_time_iso)
    if percentiles:
        p50, p95, p99 = percentiles
        print(f"    - Median Latency (p50): {p50:.2f} ms")
        print(f"    - 95th Percentile (p95): {p95:.2f} ms")
        print(f"    - 99th Percentile (p99): {p99:.2f} ms")
    else:
        print("    [!] Could not retrieve percentiles. Check index configuration or service name.")
        
    print("-" * 60)
    
    # 2. Fetch Slowest Transactions
    print(f"[*] Fetching all transactions >= {LATENCY_THRESHOLD_MS} ms...")
    slow_tx = fetch_slowest_transactions(start_time_iso)
    if slow_tx:
        for idx, tx in enumerate(slow_tx, 1):
            impact_sec = (tx['avg_ms'] * tx['count']) / 1000.0
            print(f"    {idx:2d}. {tx['name']}")
            print(f"        Average Latency : {tx['avg_ms']:.2f} ms (Count: {tx['count']})")
            print(f"        Cumulative Time : {impact_sec:.2f} seconds")
    else:
        print(f"    [!] No transactions found >= {LATENCY_THRESHOLD_MS} ms.")
        
    print("-" * 60)
    
    # 3. Fetch Slowest Database Spans
    print(f"[*] Fetching all database queries (SQL) >= {DB_LATENCY_THRESHOLD_MS} ms...")
    slow_db = fetch_slowest_spans(start_time_iso, "db")
    if slow_db:
        for idx, span in enumerate(slow_db, 1):
            impact_sec = (span['avg_ms'] * span['count']) / 1000.0
            print(f"    {idx:2d}. {span['name']}")
            print(f"        Average Duration: {span['avg_ms']:.2f} ms (Count: {span['count']})")
            print(f"        Cumulative Time : {impact_sec:.2f} seconds")
    else:
        print(f"    [!] No database queries found >= {DB_LATENCY_THRESHOLD_MS} ms.")
        
    print("-" * 60)
    
    # 4. Fetch Slowest External Spans (API Calls)
    print(f"[*] Fetching all external network requests (APIs) >= {LATENCY_THRESHOLD_MS} ms...")
    slow_ext = fetch_slowest_spans(start_time_iso, "external")
    if slow_ext:
        for idx, span in enumerate(slow_ext, 1):
            impact_sec = (span['avg_ms'] * span['count']) / 1000.0
            print(f"    {idx:2d}. {span['name']}")
            print(f"        Average Duration: {span['avg_ms']:.2f} ms (Count: {span['count']})")
            print(f"        Cumulative Time : {impact_sec:.2f} seconds")
    else:
        print(f"    [!] No external API requests found >= {LATENCY_THRESHOLD_MS} ms.")
        
    print("-" * 60)
    
    # 5. Fetch High Impact Dependencies (Unified)
    print(f"[*] Fetching all high-impact dependencies (Prioritized by Cumulative Impact)...")
    high_impact_deps = fetch_high_impact_dependencies(start_time_iso)
    if high_impact_deps:
        for idx, dep in enumerate(high_impact_deps[:20], 1): # Top 20
            impact_sec = (dep['avg_ms'] * dep['count']) / 1000.0
            print(f"    {idx:2d}. [{dep['type']}] {dep['name']}")
            print(f"        Average Duration: {dep['avg_ms']:.2f} ms (Count: {dep['count']})")
            print(f"        Cumulative Time : {impact_sec:.2f} seconds")
    else:
        print("    [!] No dependencies found.")
        
    print("=" * 60)
    print("Execution complete.")
