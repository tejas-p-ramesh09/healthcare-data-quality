DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ingest') THEN
        CREATE ROLE ingest LOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO ingest;
GRANT SELECT ON ingestion_source, ingestion_batch, ingestion_file, ingestion_attempt,
    ingestion_error, raw_resource, patient, encounter, observation, condition, medication_request TO ingest;
GRANT INSERT ON ingestion_source, ingestion_batch, ingestion_file, ingestion_attempt, ingestion_error,
    raw_resource, patient, encounter, observation, condition, medication_request TO ingest;
GRANT UPDATE (status,published_at,counts) ON ingestion_batch TO ingest;
GRANT UPDATE (status,finished_at,retryable,error_code) ON ingestion_attempt TO ingest;
GRANT USAGE ON SEQUENCE ingestion_source_id_seq, ingestion_batch_id_seq, ingestion_attempt_id_seq TO ingest;
