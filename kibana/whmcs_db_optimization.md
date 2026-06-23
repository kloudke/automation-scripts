# WHMCS Database Optimization Guide

This document contains instructions and DDL (Data Definition Language) SQL queries to apply indexes and optimize the performance of the WHMCS MySQL database based on the latest Elastic APM telemetry findings.

---

## 1. Index Inspection Commands
Run these queries first to check if any of these indexes already exist:
```sql
SHOW INDEXES FROM tblinvoiceitems;
SHOW INDEXES FROM tblactivitylog;
SHOW INDEXES FROM DNSManager3_Job;
SHOW INDEXES FROM tblclients;
SHOW INDEXES FROM tbldomainreminders;
```

---

## 2. DDL Index Creation Statements

### A. Invoice Items & Invoice Lookups (High Value)
Optimizes slow inner joins and item lookups by transaction type/relation (Average duration: 63.8ms - 73.8ms).
```sql
-- Composite index to satisfy JOINs on tblinvoiceitems.invoiceid filtering by type and relid
CREATE INDEX IF NOT EXISTS idx_tblinvoiceitems_type_relid_invoiceid 
ON tblinvoiceitems (type, relid, invoiceid);

-- Simple index for single table lookups
CREATE INDEX IF NOT EXISTS idx_tblinvoiceitems_type_relid 
ON tblinvoiceitems (type, relid);
```

### B. Activity Log Deletion & User Associations (Existing Index Analysis)
Optimizes slow deletion queries on activity logs when pruning or removing clients (Average duration: 6,244.49 ms).
> [!NOTE]
> Database verification confirms that the index on `userid` **already exists** on `tblactivitylog`. The 6.2-second latency spike is caused by concurrent log writing (which creates high lock wait times) and the overhead of updating all 6 indexes on `tblactivitylog` during client log deletions. Use the weekly pruning cron and defragmentation steps in Sections 3 and 4 to mitigate this.

### C. DNSManager3 Jobs Processing
Optimizes background and page-load job lookups by status or user association (Average duration: 518.5ms - 843.6ms).
```sql
CREATE INDEX IF NOT EXISTS idx_dnsmanager3_job_job_status 
ON DNSManager3_Job (job, status);

CREATE INDEX IF NOT EXISTS idx_dnsmanager3_job_userid 
ON DNSManager3_Job (userid);
```

### D. Client Sorting and Admin Lookups
Optimizes admin dashboard tables listing and sorting clients by name (Average duration: 147.5 ms).
```sql
CREATE INDEX IF NOT EXISTS idx_tblclients_name_search 
ON tblclients (lastname, firstname, companyname);
```

### E. Domain Renewal Reminders
Optimizes checks for historical reminders sent to clients for a given domain (Average duration: 103.9 ms).
```sql
CREATE INDEX IF NOT EXISTS idx_tbldomainreminders_domain_id 
ON tbldomainreminders (domain_id);
```

---

## 3. Log Table Pruning (Weekly Task)
To address write and insert updates, run these commands to prune logs older than 90 days:
```sql
DELETE FROM tblactivitylog WHERE `date` < DATE_SUB(NOW(), INTERVAL 90 DAY);
DELETE FROM tblgatewaylog WHERE `date` < DATE_SUB(NOW(), INTERVAL 90 DAY);
DELETE FROM tbladminlog WHERE `lastvisit` < DATE_SUB(NOW(), INTERVAL 90 DAY);
```

---

## 4. Defragmentation & Optimizer Statistics Tuning
If you inspect the database and find that the indexes *already exist* but queries are still performing slowly, the database optimizer may be using outdated statistics or the index blocks may be heavily fragmented due to high write/delete churn. 

Run these commands during off-peak hours (as they consume significant disk I/O and CPU) to update statistics and defragment the index trees:
```sql
-- Update optimizer statistics
ANALYZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders;

-- Rebuild tables and defragment indexes
OPTIMIZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders;
```
