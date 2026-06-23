# WHMCS Performance Telemetry Analysis Report (K8s Service: TSA-WHMCS)

This report details the latest Elastic APM performance analysis of the **`TSA-WHMCS`** service running on Kubernetes (K8s) using standard analysis thresholds:
- **Transactions & APIs**: >= 2000ms
- **Database Queries**: >= 100ms

---

## 1. Latency Baseline Summary

- **Median Response Time (p50)**: 467.29 ms
- **95th Percentile Latency (p95)**: 3,640.89 ms (3.6s)
- **99th Percentile Latency (p99)**: 11,068.87 ms (11.0s)

---

## 2. Isolated Database Bottlenecks (>= 100ms)

The latest telemetry isolates several high-impact database queries causing thread-blocking and user delays:

### A. Severe Table Locks on Activity Log Deletions
* **Query**: `delete from tblactivitylog where userid = ? and id <= ?`
* **Average Duration**: **6,244.49 ms** (6.2s)
* **Root Cause**: Missing index on `tblactivitylog(userid)`. When a client is deleted or pruned, the database performs a full table scan on `tblactivitylog` to identify matching rows. On a large logs table, this locks database rows/pages, stalling other client transactions.
* **Remediation**: Add a single index on `userid`.

### B. Slow DNSManager3 Job Lookups
* **Queries**:
  - `select * from DNSManager3_Job where job = ? and status = ?` (Average: **843.64 ms**)
  - `select DNSManager3_Job.* from DNSManager3_Job where userid = ?` (Average: **518.57 ms**)
* **Root Cause**: Missing indexes on the job execution table used by the custom DNS manager module.
* **Remediation**: Create a composite index on `(job, status)` and a single index on `userid`.

### C. Client Admin Directory Sorting
* **Query**: `SELECT SQL_CALC_FOUND_ROWS ... FROM tblclients ORDER BY lastname ASC, firstname ASC, companyname ASC LIMIT ...`
* **Average Duration**: **138ms - 147ms** (high frequency)
* **Root Cause**: The admin client listing page queries a large number of rows and performs a filesystem sort (filesort) to order the records by name.
* **Remediation**: Create a composite index on `(lastname, firstname, companyname)` to enable direct index ordering and avoid filesorts.

### D. Domain Renewal Reminder History Lookups
* **Query**: `SELECT * FROM tbldomainreminders WHERE domain_id = ? ORDER BY id DESC`
* **Average Duration**: **103.95 ms**
* **Root Cause**: Missing index on `domain_id` column.
* **Remediation**: Create an index on `domain_id`.

---

## 3. Outbound API & Transaction Bottlenecks (>= 2000ms)

Synchronous, blocking external network connections remain the primary contributor to the application's p99 latency spikes:

### A. Olitt API Integration delays (22.3s - 28.4s)
* **Outbound HTTP Spans**:
  - `POST olitt.com` (Average: **28,454.69 ms**)
  - `POST app.olitt.com` (Average: **22,308.44 ms**)
* **Transactions Blocked**:
  - `POST /cloud/modules/addons/olitt_whmcs_addon/olitt-connect.php` (**33.7s** average latency)
  - `GET /cloud/modules/addons/olitt_ai_sites/olitt-edit.php` (**22.5s** average latency)
* **Impact**: Transactions block PHP execution indefinitely waiting for remote responses.
* **Remediation**: Enforce connection and execution timeouts (3s / 5s) on all outbound cURL clients (as implemented in `olitt-connect.php`).

### B. Lim116 Host Node Connection (14.2s Overhead)
* **Outbound HTTP Span**: `POST 141.95.3.80` (hostname: **`lim116.truehost.cloud`**)
* **Average Duration**: **14,257.64 ms** (14.2s)
* **Impact**: Order placements and client operations are blocked by slow hypervisor communication.

### C. DNS Manager Sync (4.9s Overhead)
* **Outbound HTTP Span**: `GET central3.dns.truehost.cloud`
* **Average Duration**: **4,900.88 ms** (4.9s, executed 196 times)
* **Root Cause**: Synchronous DNS syncing operations to your central nameserver during client area requests.

### D. Hypervisor Status Checks (2.9s Overhead)
* **Outbound HTTP Span**: `POST 157.180.74.207`
* **Average Duration**: **2,929.05 ms** (2.9s, executed 148 times)
* **Remediation**: Mitigated by the 60-second caching wrapper implemented on the `Virtualizor_Curl` class.

---

## 4. Kubernetes-Specific Recommendations & Remediation Plan

### Step 1: Apply Updated Database Indexes
Run the updated optimization DDL statements to create the missing indexes on the database:
* [whmcs_db_optimization.sql](file:///Users/gedeon/Dev/work/tcloud-whmcs/whmcs_db_optimization.sql)

```bash
kubectl exec -i deployment/whmcs-deployment -- mysql -h [DB_HOST] -u [DB_USER] -p[DB_PASSWORD] [DB_NAME] < whmcs_db_optimization.sql
```

### Step 2: Implement APM Sidecar or DaemonSet
Route `elastic_apm.server_url` to a local sidecar/DaemonSet to reduce APM telemetry dispatch latency to under 1ms.

### Step 3: Route Host/Hypervisor Traffic Privately & Enable Cache
Pass `REDIS_HOST` environment variable to enable internal Redis caching, storing hypervisor responses to avoid redundant synchronous API calls.
