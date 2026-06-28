# Root Cause Analysis: Performance & APM Overhead on Staging WHMCS (OPcache Enabled)

This document presents a Root Cause Analysis (RCA) of performance bottlenecks and APM overhead in the staging environment (`STAGING-WHMCS` / [staging-data.txt](file:///Users/gedeon/Dev/automation-scripts/kibana/staging-data.txt)) and compares these findings to the production clusters in India (`TIN-WHMCS`) and South Africa (`TSA-WHMCS`).

---

## 1. Executive Summary

The staging WHMCS container has **Zend OPcache enabled**, which has successfully optimized the script compilation and database execution layers. The database is highly responsive, with the slowest query taking only **354 ms** and normal queries executing in **< 1 ms**.

However, the staging environment still suffers from a high **99th percentile ($p99$) latency of 26.4 seconds**. The root causes for this are **synchronous outbound network I/O blockages** and **system memory allocator overhead (`USE_ZEND_ALLOC=0`)**, which combine to cause severe thread starvation in Apache mod_php.

---

## 2. Staging Root Cause Breakdown

### Root Cause 1: Synchronous Web Server Thread blocking (Apache mod_php)
* **Mechanism**: Staging runs Apache mod_php (`apache2-foreground`) and makes synchronous API calls during page execution.
* **Technical Impact**:
  - Apache mod_php lacks asynchronous flushing capabilities (like PHP-FPM's `fastcgi_finish_request()`).
  - The PHP execution thread and Apache worker connection must remain open while waiting for external API responses.
  - **Outbound Delays**: Outbound network requests to `central.dns.truehost.cloud` (116 calls @ 4.46s average) and `httpbin.org` (54 calls @ 6.55s average) block threads for up to 14 seconds per page load.
  - **Thread Starvation**: This block exhausts Apache's worker pool. Standard static assets (e.g., `logo_small.png` @ **4,673.22 ms** and `favicons.png` @ **2,755.03 ms**) get queued behind blocked network threads, stalling the entire application.

### Root Cause 2: Zend Alloc Bypass (`USE_ZEND_ALLOC=0`) with OPcache Active
* **Mechanism**: The container environment enforces `USE_ZEND_ALLOC=0` in `/entrypoint.sh` when APM is enabled.
* **Technical Impact**:
  - While Zend OPcache successfully caches compiled PHP bytecode in shared memory (reducing parsing CPU overhead), the execution of that bytecode must still allocate and destroy millions of temporary runtime variables, arrays, and template tokens.
  - Setting `USE_ZEND_ALLOC=0` bypasses PHP's optimized Zend Memory Manager (ZMM) heap pools, forcing every variable allocation/deallocation to go through libc system `malloc()` and `free()` calls.
  - This adds a significant CPU execution penalty and memory fragmentation, degrading the performance benefits of OPcache.

### Root Cause 3: High-Frequency N+1 Configuration & Theme Queries
* **Mechanism**: Staging does not cache core configurations or menu structures, causing redundant looping lookups.
* **Technical Impact**:
  - `tblconfiguration` queries run **40,886 times** in 24 hours.
  - Lagom Theme menu queries run **33,543 times** (`rsthemes_menus_content` and `rsthemes_menus_types`).
  - This high-frequency database query volume adds CPU and serialization overhead, blocking PHP processes.

### Root Cause 4: Accidental Active Debug Endpoints (`httpbin.org`)
* **Mechanism**: An active debug hook or testing configuration is querying `httpbin.org`.
* **Technical Impact**:
  - In staging, **54 calls** were made to `httpbin.org` with an average duration of **6,552.61 ms (6.55s)**, contributing **353.84 seconds** of cumulative blocking delay on user/admin pages.

---

## 3. Comparison & Similarities: Staging vs. Production

### A. Key Similarities (Shared Bottlenecks)

1. **The `USE_ZEND_ALLOC=0` Overhead**:
   - Both Staging and Production clusters run with the Zend Memory Manager disabled when APM is on, leading to increased CPU allocation overhead.
2. **Synchronous WAN Blocking (Apache mod_php)**:
   - Both environments run Apache mod_php and hold worker connections open while waiting for network I/O (APM telemetry uploads to `apm.jisort.com` and DNS/hypervisor API connections).
3. **Outbound DNS Manager Sync Latency**:
   - Both show high latency during nameserver zone syncs:
     - Staging: `central.dns.truehost.cloud` -> **4.46s** avg latency.
     - Production SA: `central3.dns.truehost.cloud` -> **4.90s** avg latency.
4. **Configuration N+1 Query Volumes**:
   - Redundant database lookups for static configurations are high in both (81k queries/day in SA, 40k queries/day in Staging).

### B. Key Differences (Why Staging Differs)

1. **Database Layer Performance**:
   - **Production (India/SA)**: Severely bottlenecked by unindexed database queries (India: `tbladminlog` session scans taking **12.6s**; SA: `tblactivitylog` log deletions taking **6.2s**).
   - **Staging**: The database layer is fast and healthy. There are no session locking issues or log deletion blockages. The slowest query is only **354 ms**.
2. **Zend OPcache Benefit**:
   - **Staging**: OPcache is active, reducing script loading and parsing overhead.
   - **Production**: OPcache was disabled, compounding CPU load.
3. **Debug Connection Leakage**:
   - **Staging**: Actively leaks synchronous requests to `httpbin.org` (54 executions, **353.84s cumulative**), which is not present as a primary blocker in the production logs.

---

## 4. Summary Matrix: Staging vs. Production Profiles

| Performance Dimension | Staging (`STAGING-WHMCS`) | India (`TIN-WHMCS`) | South Africa (`TSA-WHMCS`) |
| :--- | :--- | :--- | :--- |
| **Zend OPcache** | **Enabled** | Disabled | Disabled |
| **Zend Memory Manager** | **Bypassed (`USE_ZEND_ALLOC=0`)** | Bypassed | Bypassed |
| **Database Bottlenecks** | **None** (Slowest is 354 ms) | **Severe** (`tbladminlog` sessions @ 12s) | **High** (`tblactivitylog` logs @ 6s) |
| **Outbound Network Bottlenecks** | **Critical** (DNS Sync @ 4.4s, httpbin @ 6.5s) | **High** (External APIs @ 10s+) | **Critical** (DNS Sync @ 4.9s) |
| **Thread Starvation Risk** | **Extreme** (Static assets taking up to 4.6s) | **Severe** (Admin page loads up to 138s) | **Severe** (Cart checkouts up to 33s) |
