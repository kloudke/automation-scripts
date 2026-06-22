#!/usr/bin/env python3
"""
fetch_apm_telemetry.py
Queries the Elasticsearch cluster directly to pull Elastic APM metrics for WHMCS.
Requires no external dependencies (uses standard library urllib.request).
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
# Adjust these values or export them as environment variables.
ES_HOST = os.environ.get("ES_HOST", "http://12.34.56.78:9200").rstrip("/")
ES_USERNAME = os.environ.get("ES_USERNAME", "elasticsearch_cluster_username")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "your_elastic_password_here")
ES_API_KEY = os.environ.get("ES_API_KEY", "")  # Or use an API Key if configured

# APM Service name for WHMCS
SERVICE_NAME = os.environ.get("APM_SERVICE_NAME", "whmcs")

# APM Index pattern (defaults to standard Elastic Agent / APM Server patterns)
APM_INDEX = os.environ.get("APM_INDEX", "traces-apm*,apm-*")

# Time window for analysis
TIME_WINDOW_HOURS = int(os.environ.get("TIME_WINDOW_HOURS", "24"))

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
    
    # Allow self-signed SSL certs commonly found on internal clusters
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
        # Convert microsecond durations to milliseconds
        p50 = percentiles.get("50.0", 0) / 1000.0
        p95 = percentiles.get("95.0", 0) / 1000.0
        p99 = percentiles.get("99.0", 0) / 1000.0
        return p50, p95, p99
    except Exception:
        return None

def fetch_slowest_transactions(start_time_iso):
    """Fetches the top 10 slowest unique transactions by average duration."""
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
                    "size": 10,
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
            transactions.append({
                "name": b["key"],
                "count": b["doc_count"],
                "avg_ms": (b["avg_latency"]["value"] or 0) / 1000.0
            })
        return transactions
    except Exception:
        return []

def fetch_slowest_spans(start_time_iso, span_type="db"):
    """Fetches slow spans of a specific type (e.g. 'db', 'external', 'app')."""
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
                    "size": 10,
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
    
    try:
        res = send_es_request(f"{APM_INDEX}/_search", query)
        buckets = res["aggregations"]["by_span_name"]["buckets"]
        spans = []
        for b in buckets:
            spans.append({
                "name": b["key"],
                "count": b["doc_count"],
                "avg_ms": (b["avg_duration"]["value"] or 0) / 1000.0
            })
        return spans
    except Exception:
        return []

# =============================================================================
# 4. MAIN RUNNER
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("           WHMCS Elastic APM Telemetry Fetcher")
    print("=" * 60)
    
    # Calculate timestamps
    time_threshold = datetime.utcnow() - timedelta(hours=TIME_WINDOW_HOURS)
    start_time_iso = time_threshold.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    
    print(f"[*] Querying Elasticsearch Host: {ES_HOST}")
    print(f"[*] Targeting APM Service     : {SERVICE_NAME}")
    print(f"[*] Time Range                : Last {TIME_WINDOW_HOURS} hours (since {start_time_iso})")
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
    print("[*] Fetching top 10 slowest transactions...")
    slow_tx = fetch_slowest_transactions(start_time_iso)
    if slow_tx:
        for idx, tx in enumerate(slow_tx, 1):
            print(f"    {idx:2d}. {tx['name']}")
            print(f"        Average Latency : {tx['avg_ms']:.2f} ms (Count: {tx['count']})")
    else:
        print("    [!] No transactions found.")
        
    print("-" * 60)
    
    # 3. Fetch Slowest Database Spans
    print("[*] Fetching top 10 slowest database queries (SQL)...")
    slow_db = fetch_slowest_spans(start_time_iso, "db")
    if slow_db:
        for idx, span in enumerate(slow_db, 1):
            print(f"    {idx:2d}. {span['name']}")
            print(f"        Average Duration: {span['avg_ms']:.2f} ms (Count: {span['count']})")
    else:
        print("    [!] No database queries found.")
        
    print("-" * 60)
    
    # 4. Fetch Slowest External Spans (API Calls)
    print("[*] Fetching top 10 slowest external network requests (APIs)...")
    slow_ext = fetch_slowest_spans(start_time_iso, "external")
    if slow_ext:
        for idx, span in enumerate(slow_ext, 1):
            print(f"    {idx:2d}. {span['name']}")
            print(f"        Average Duration: {span['avg_ms']:.2f} ms (Count: {span['count']})")
    else:
        print("    [!] No external API requests found.")
        
    print("=" * 60)
    print("Execution complete.")
