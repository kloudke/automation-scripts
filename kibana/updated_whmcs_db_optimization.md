# WHMCS Database Optimization Guide & Runbook

This guide contains schema enhancements, indexing scripts, pruning procedures, and application-level code recommendations to resolve database performance bottlenecks identified by Elastic APM telemetry.

---

## 1. Capture a Baseline & Schema Inspect
Before running any DDL statements, inspect the current schemas to verify existing structures and check database buffer sizing.

```sql
-- Check current index state
SHOW CREATE TABLE tblactivitylog;
SHOW CREATE TABLE DNSManager3_Job;
SHOW CREATE TABLE tbldomainreminders;
SHOW CREATE TABLE tblinvoices;
SHOW CREATE TABLE tblclients;

-- Review row counts and database footprint sizes
SELECT table_name, table_rows, 
       ROUND(data_length/1024/1024,1) AS data_mb,
       ROUND(index_length/1024/1024,1) AS index_mb
FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name IN ('tblactivitylog','DNSManager3_Job','tbldomainreminders','tblinvoices','tblclients');

-- Check InnoDB buffer pool allocation (target is ~60-70% of database RAM)
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';
```

---

## 2. DDL Index Creation Statements
Apply these indexes during low-traffic windows. 

```sql
-- 1. DNSManager3 Job Queue (High Priority)
-- Satisfies background sync and page-load job queues (avg 518ms - 843ms)
ALTER TABLE DNSManager3_Job
  ADD INDEX idx_dnsmanager3_job_job_status (job, status),
  ADD INDEX idx_dnsmanager3_job_userid (userid),
  ALGORITHM=INPLACE, LOCK=NONE;

-- 2. Domain Renewal Reminders (High Priority)
-- Satisfies domain renewal queries (avg 100ms)
ALTER TABLE tbldomainreminders
  ADD INDEX idx_tbldomainreminders_domain_id (domain_id),
  ALGORITHM=INPLACE, LOCK=NONE;

-- 3. Invoice Items & Invoice Lookups
-- Satisfies inner joins and type filters on billing checks
CREATE INDEX idx_tblinvoiceitems_type_relid_invoiceid ON tblinvoiceitems (type, relid, invoiceid);
CREATE INDEX idx_tblinvoiceitems_type_relid ON tblinvoiceitems (type, relid);

-- 4. Invoice Date Sorting
-- Eliminates "Using filesort" overhead when sorting invoices by date
ALTER TABLE tblinvoices
  ADD INDEX idx_tblinvoices_date (date),
  ALGORITHM=INPLACE, LOCK=NONE;

-- 5. Client Admin Dashboards & Search
-- Optimizes searching/sorting clients by name in the admin area (avg 147ms)
CREATE INDEX idx_tblclients_name_search ON tblclients (lastname(64), firstname(64), companyname(64));

-- 6. Admin Session Verification (Critical for India Deployment)
-- Resolves slow admin logins and page transitions (avg 5.0s - 40.6s)
ALTER TABLE tbladminlog
  ADD INDEX idx_tbladminlog_sessionid (sessionid),
  ALGORITHM=INPLACE, LOCK=NONE;
```

> [!NOTE]
> **Duplicate Index Cleanup**: During the baseline inspection, check if `tblactivitylog` has duplicate indexes on both `userid` and `user_id`. Having both increases index maintenance overhead on every insert/delete. If confirmed duplicate, drop the redundant index:
> ```sql
> ALTER TABLE tblactivitylog DROP INDEX user_id, ALGORITHM=INPLACE, LOCK=NONE;
> ```

---

## 3. Log Table Batch-Pruning (Safe Initial Cleanup)
Do not execute a single large `DELETE` query on `tblactivitylog` (which locked the table for **6.2 seconds** in the APM log). Instead, run deletions in smaller batches of **5,000 rows** to avoid lock contention.

### Bash Batch Deletion Script
Run this script on the application container or database host:
```bash
while true; do
  AFFECTED=$(mysql -N -e "DELETE FROM tblactivitylog WHERE date <= DATE_SUB(NOW(), INTERVAL 90 DAY) LIMIT 5000; SELECT ROW_COUNT();")
  echo "Deleted: $AFFECTED"
  if [ "$AFFECTED" -eq 0 ]; then break; fi
  sleep 1   # Pause briefly to prevent I/O saturation
done
```

### Option B: Automated Daily Event Scheduler
If Event Scheduling is enabled on your MySQL server, create a native daily job:
```sql
CREATE EVENT IF NOT EXISTS prune_tblactivitylog
ON SCHEDULE EVERY 1 DAY STARTS '2026-06-26 02:00:00'
DO
  DELETE FROM tblactivitylog WHERE date <= (CURDATE() - INTERVAL 90 DAY) LIMIT 5000;
```

---

## 4. Defragmentation & Optimizer Statistics
After performing bulk deletions or index updates, defragment the index trees to reclaim disk blocks and update statistics for the MySQL optimizer:
```sql
-- Update statistics
ANALYZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders, tblinvoices, tbladminlog;

-- Rebuild tables and defragment indexes (Run during off-peak hours)
OPTIMIZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders, tblinvoices, tbladminlog;
```

---

## 5. Application-Level Queries Optimization
Provide these recommendations to the development team to optimize how PHP queries the database:

### A. Batch N+1 Lookups (Looping Queries)
Avoid executing a SQL query inside a loop (e.g. looking up domain reminders per domain). Batch them into a single `WHERE IN` statement:
*   **Before**:
    ```sql
    -- Executed 50 times in a loop:
    SELECT * FROM tbldomainreminders WHERE domain_id = ?;
    ```
*   **After (Single Query)**:
    ```sql
    SELECT * FROM tbldomainreminders WHERE domain_id IN (12, 14, 15, 23, ...) ORDER BY domain_id, id DESC;
    ```
    *Group the results in PHP memory after fetching.*

### B. Keyset Pagination (Avoid `OFFSET`)
WHMCS export scripts use high offset values (e.g., `LIMIT 1000 OFFSET 20000`). This forces MySQL to scan and discard 20,000 rows. Use **Keyset/Cursor Pagination** instead:
*   **Before**:
    ```sql
    SELECT ... FROM tblinvoices ORDER BY tblinvoices.date DESC LIMIT 1000 OFFSET 20000;
    ```
*   **After (Cursor-based)**:
    ```sql
    SELECT ... FROM tblinvoices WHERE tblinvoices.id < :last_seen_id ORDER BY tblinvoices.id DESC LIMIT 1000;
    ```
    *Feed the minimum ID from the previous page into the next page request.*

### C. Remove `SQL_CALC_FOUND_ROWS`
Some listing pages run `SQL_CALC_FOUND_ROWS` on queries to get the total row count. This modifier is deprecated in MySQL 8.0 and forces a full scan of the dataset. Run a separate, cached `SELECT COUNT(*)` query only when the total count is explicitly required by the UI.

---

## 6. Target Milestones
Compare pre- and post-optimization execution times:

| Query Shape / Target | Pre-Optimization | Post-Index / Target |
| --- | --- | --- |
| `DNSManager3_Job` status lookup | ~843 ms | **< 10 ms** |
| `DNSManager3_Job` user lookup | ~518 ms | **< 10 ms** |
| `tbldomainreminders` lookup | ~100 ms | **< 10 ms** |
| `tblclients` admin search | ~147 ms | **< 15 ms** |
| `tblactivitylog` delete lock time | 6.2 seconds | **< 50 ms** (via 5k limit) |
