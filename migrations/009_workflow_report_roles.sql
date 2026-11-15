DO $$
BEGIN
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='workflow_executor') THEN
        CREATE ROLE workflow_executor LOGIN;
    END IF;
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='workflow_reporter') THEN
        CREATE ROLE workflow_reporter LOGIN;
    END IF;
END $$;
GRANT ingest TO workflow_executor;
GRANT USAGE ON SCHEMA public TO workflow_executor,workflow_reporter;
GRANT SELECT ON workflow,workflow_request_key,workflow_stage,workflow_stage_attempt TO workflow_executor;
GRANT INSERT(execution_sha256,spec) ON workflow TO workflow_executor;
GRANT INSERT ON workflow_request_key TO workflow_executor;
GRANT INSERT(workflow_id,step) ON workflow_stage,workflow_stage_attempt TO workflow_executor;
GRANT UPDATE(status,expected,batch_id,quality_id,matching_id,updated_at) ON workflow_stage TO workflow_executor;
GRANT UPDATE(status,code,retryable,finished_at) ON workflow_stage_attempt TO workflow_executor;
GRANT USAGE ON SEQUENCE workflow_id_seq,workflow_stage_attempt_id_seq TO workflow_executor;
CREATE INDEX workflow_created_page ON workflow(created_at,id);

CREATE VIEW workflow_report AS SELECT id,execution_sha256,spec,created_at FROM workflow;
CREATE VIEW workflow_stage_report AS
SELECT s.workflow_id,s.step,s.status,s.batch_id,s.quality_id,s.matching_id,s.updated_at,
       (SELECT count(*) FROM workflow_stage_attempt WHERE workflow_id=s.workflow_id AND step=s.step)
           AS attempt_count,
       a.code,a.retryable,coalesce(b.counts,q.counts,m.counts) AS counts
FROM workflow_stage s
LEFT JOIN ingestion_batch b ON b.id=s.batch_id
LEFT JOIN quality_run q ON q.id=s.quality_id
LEFT JOIN matching_run m ON m.id=s.matching_id
LEFT JOIN LATERAL (
    SELECT code,retryable FROM workflow_stage_attempt WHERE workflow_id=s.workflow_id AND step=s.step
    ORDER BY id DESC LIMIT 1
) a ON true;
CREATE VIEW workflow_batch_report AS
SELECT w.workflow_id,w.step,b.id AS batch_id,s.source_key,b.manifest_sha256,b.counts
FROM workflow_stage w JOIN ingestion_batch b ON b.id=w.batch_id
JOIN ingestion_source s ON s.id=b.source_id WHERE w.status='completed' AND b.status='published';
CREATE VIEW workflow_file_report AS
SELECT b.workflow_id,b.step,f.path AS file_label,f.bytes,f.sha256
FROM workflow_batch_report b JOIN ingestion_file f ON f.batch_id=b.batch_id;
CREATE VIEW workflow_finding_report AS
SELECT s.workflow_id,s.step,f.path AS file_label,f.entry_index,f.rule_id,f.rule_version,
       f.category,f.severity,f.pointer,f.diagnostic,f.related_path AS related_file_label,f.related_index,
       source.sha256 AS file_sha256,related.sha256 AS related_file_sha256,r.fhir_id,r.resource_type,f.batch_id
FROM workflow_stage s JOIN quality_finding f ON f.run_id=s.quality_id
JOIN raw_resource r ON (r.batch_id,r.path,r.entry_index)=(f.batch_id,f.path,f.entry_index)
JOIN ingestion_file source ON (source.batch_id,source.path)=(f.batch_id,f.path)
LEFT JOIN ingestion_file related ON (related.batch_id,related.path)=(f.batch_id,f.related_path)
WHERE s.status='completed';
CREATE VIEW workflow_recommendation_report AS
SELECT w.workflow_id,c.left_batch,c.right_batch,c.left_id,c.right_id,c.block_codes,
       s.method,s.score,s.evidence,r.disposition,r.reason,
       l.path AS left_file_label,l.entry_index AS left_entry_index,lf.sha256 AS left_file_sha256,
       p.path AS right_file_label,p.entry_index AS right_entry_index,rf.sha256 AS right_file_sha256
FROM workflow_stage w JOIN matching_candidate c ON c.run_id=w.matching_id
JOIN matching_score s USING(run_id,left_id,right_id)
JOIN matching_recommendation r USING(run_id,left_id,right_id,method)
JOIN patient l ON (l.batch_id,l.fhir_id)=(c.left_batch,c.left_id)
JOIN patient p ON (p.batch_id,p.fhir_id)=(c.right_batch,c.right_id)
JOIN ingestion_file lf ON (lf.batch_id,lf.path)=(l.batch_id,l.path)
JOIN ingestion_file rf ON (rf.batch_id,rf.path)=(p.batch_id,p.path)
WHERE w.step=4 AND w.status='completed';
CREATE VIEW workflow_schema_ready AS
SELECT EXISTS(SELECT FROM schema_migration WHERE version='009_workflow_report_roles.sql') AS ready;

GRANT SELECT ON workflow_report,workflow_stage_report,workflow_batch_report,workflow_file_report,
    workflow_finding_report,workflow_recommendation_report,workflow_schema_ready TO workflow_reporter;
GRANT SELECT ON workflow_schema_ready TO workflow_executor;
