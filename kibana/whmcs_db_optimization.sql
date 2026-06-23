-- =============================================================================
-- whmcs_db_optimization.sql
-- Database optimizations based on Elastic APM Telemetry analysis.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. INSPECT CURRENT INDEXES (Run these to see existing indexes)
-- -----------------------------------------------------------------------------
-- SHOW INDEXES FROM tblinvoiceitems;
-- SHOW INDEXES FROM tblactivitylog;
-- SHOW INDEXES FROM DNSManager3_Job;
-- SHOW INDEXES FROM tblclients;
-- SHOW INDEXES FROM tbldomainreminders;

-- -----------------------------------------------------------------------------
-- 2. CREATE INDEXES FOR TBLINVOICEITEMS (>= 50ms)
-- Pinpoints: 
--   - Invoice items inner join (Average: 73.80 ms)
--   - Invoice items direct queries (Average: 63.83 ms)
-- -----------------------------------------------------------------------------
-- Composite index to satisfy JOINs on tblinvoiceitems.invoiceid filtering by type and relid
CREATE INDEX idx_tblinvoiceitems_type_relid_invoiceid 
ON tblinvoiceitems (type, relid, invoiceid);

-- Simple index for single table lookups
CREATE INDEX idx_tblinvoiceitems_type_relid 
ON tblinvoiceitems (type, relid);

-- -----------------------------------------------------------------------------
-- 3. TBLACTIVITYLOG OPTIMIZATION (>= 100ms)
-- Pinpoints:
--   - Delete from tblactivitylog where userid = ? and id <= ? (Average: 6,244.49 ms)
-- Notes:
--   - The 'userid' index already exists on this table. The 6.2s delay is caused 
--     by table lock wait times and updating all 6 indexes on tblactivitylog for 
--     every row deleted. Keep this table small using the weekly pruning and 
--     OPTIMIZE steps in Section 7 & 8 below.
-- -----------------------------------------------------------------------------
-- (Index already exists: 'userid' on tblactivitylog(userid))

-- -----------------------------------------------------------------------------
-- 4. CREATE INDEXES FOR DNSMANAGER3_JOB (>= 100ms)
-- Pinpoints:
--   - select * from DNSManager3_Job where job = ? and status = ? (Average: 843.64 ms)
--   - select DNSManager3_Job.* from DNSManager3_Job where userid = ? (Average: 518.57 ms)
-- -----------------------------------------------------------------------------
CREATE INDEX idx_dnsmanager3_job_job_status 
ON DNSManager3_Job (job, status);

CREATE INDEX idx_dnsmanager3_job_userid 
ON DNSManager3_Job (userid);

-- -----------------------------------------------------------------------------
-- 5. CREATE INDEXES FOR TBLCLIENTS (>= 100ms)
-- Pinpoints:
--   - Client admin search and lists sorted by name (Average: 147.54 ms)
-- Notes:
--   - Since these name columns might be stored as TEXT/BLOB or large VARCHAR, 
--     we specify a prefix key length (64 chars) to satisfy MySQL requirements.
-- -----------------------------------------------------------------------------
CREATE INDEX idx_tblclients_name_search 
ON tblclients (lastname(64), firstname(64), companyname(64));

-- -----------------------------------------------------------------------------
-- 6. CREATE INDEXES FOR TBLDOMAINREMINDERS (>= 100ms)
-- Pinpoints:
--   - SELECT * FROM tbldomainreminders WHERE domain_id = ? ORDER BY id DESC (Average: 103.95 ms)
-- -----------------------------------------------------------------------------
CREATE INDEX idx_tbldomainreminders_domain_id 
ON tbldomainreminders (domain_id);

-- -----------------------------------------------------------------------------
-- 7. LOG TABLE PRUNING (Weekly Cron candidate to control table sizes)
-- -----------------------------------------------------------------------------
-- DELETE FROM tblactivitylog WHERE `date` < DATE_SUB(NOW(), INTERVAL 90 DAY);
-- DELETE FROM tblgatewaylog WHERE `date` < DATE_SUB(NOW(), INTERVAL 90 DAY);
-- DELETE FROM tbladminlog WHERE `lastvisit` < DATE_SUB(NOW(), INTERVAL 90 DAY);

-- -----------------------------------------------------------------------------
-- 8. UPDATE OPTIMIZER STATISTICS & DEFRAGMENT TABLES
-- Run these if indexes already exist but queries are still performing slowly.
-- Updating statistics helps MySQL choose the correct index path. Defragmenting
-- reclaims disk space and rebuilds index pages.
-- (Preferably run during off-peak hours as they consume CPU and disk I/O)
-- -----------------------------------------------------------------------------
-- ANALYZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders;
-- OPTIMIZE TABLE tblinvoiceitems, tblactivitylog, DNSManager3_Job, tblclients, tbldomainreminders;
