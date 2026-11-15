REVOKE INSERT ON ingestion_batch, ingestion_attempt, quality_run, quality_attempt FROM ingest;
GRANT INSERT(source_id,manifest_sha256,contract_version) ON ingestion_batch TO ingest;
GRANT INSERT(batch_id) ON ingestion_attempt TO ingest;
GRANT INSERT(batch_id,ruleset_sha256,ruleset,engine_revision) ON quality_run TO ingest;
GRANT INSERT(run_id) ON quality_attempt TO ingest;

CREATE FUNCTION stage_registration_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'registered' OR NEW.counts IS NOT NULL THEN
        RAISE EXCEPTION 'Stage must start registered' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_registration BEFORE INSERT ON ingestion_batch
FOR EACH ROW EXECUTE FUNCTION stage_registration_guard();
CREATE TRIGGER quality_registration BEFORE INSERT ON quality_run
FOR EACH ROW EXECUTE FUNCTION stage_registration_guard();

CREATE FUNCTION ingestion_output_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text;
BEGIN
    SELECT status INTO parent_status FROM ingestion_batch WHERE id=NEW.batch_id FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'running'
       AND NOT (TG_TABLE_NAME='ingestion_file' AND parent_status='registered') THEN
        RAISE EXCEPTION 'Ingestion is not writable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ingestion_file','raw_resource','patient','encounter','observation',
                              'condition','medication_request','ingestion_attempt'] LOOP
        EXECUTE format('CREATE TRIGGER ingestion_output BEFORE INSERT ON %I
                         FOR EACH ROW EXECUTE FUNCTION ingestion_output_guard()',t);
    END LOOP;
END $$;
CREATE TRIGGER ingestion_attempt_update BEFORE UPDATE ON ingestion_attempt
FOR EACH ROW EXECUTE FUNCTION ingestion_output_guard();

CREATE FUNCTION ingestion_error_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM ingestion_attempt a JOIN ingestion_batch b ON b.id=a.batch_id
    WHERE a.id=NEW.attempt_id AND b.status='running' FOR UPDATE OF b;
    IF NOT FOUND THEN RAISE EXCEPTION 'Ingestion is not active' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_error_insert BEFORE INSERT ON ingestion_error
FOR EACH ROW EXECUTE FUNCTION ingestion_error_guard();

CREATE FUNCTION quality_output_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM quality_run WHERE id=NEW.run_id AND status='running' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Quality run is not active' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER quality_finding_insert BEFORE INSERT ON quality_finding
FOR EACH ROW EXECUTE FUNCTION quality_output_guard();
CREATE TRIGGER quality_attempt_write BEFORE INSERT OR UPDATE ON quality_attempt
FOR EACH ROW EXECUTE FUNCTION quality_output_guard();

CREATE FUNCTION ingestion_completion_valid(b ingestion_batch) RETURNS boolean LANGUAGE sql STABLE AS $$
    WITH types AS (
        SELECT 'Patient' AS kind,count(*) AS n FROM patient WHERE batch_id=b.id
        UNION ALL SELECT 'Encounter',count(*) FROM encounter WHERE batch_id=b.id
        UNION ALL SELECT 'Observation',count(*) FROM observation WHERE batch_id=b.id
        UNION ALL SELECT 'Condition',count(*) FROM condition WHERE batch_id=b.id
        UNION ALL SELECT 'MedicationRequest',count(*) FROM medication_request WHERE batch_id=b.id
    ), totals AS (
        SELECT count(*) AS raw,count(*) FILTER(WHERE disposition='raw_only') AS raw_only
        FROM raw_resource WHERE batch_id=b.id
    )
    SELECT b.counts->'raw'=to_jsonb(raw)
        AND b.counts->'raw_only'=to_jsonb(raw_only)
        AND b.counts->'normalized'=to_jsonb(raw-raw_only)
        AND raw-raw_only=(SELECT sum(n) FROM types)
        AND b.counts->'by_type'=(SELECT coalesce(jsonb_object_agg(kind,n),'{}') FROM types WHERE n>0)
        AND (SELECT count(*) FROM ingestion_attempt WHERE batch_id=b.id AND status='published')=1
        AND NOT EXISTS(SELECT FROM ingestion_attempt WHERE batch_id=b.id AND status='running')
    FROM totals
$$;
CREATE FUNCTION quality_completion_valid(q quality_run) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT jsonb_typeof(q.counts)='object'
        AND q.counts ?& ARRAY(SELECT jsonb_object_keys(q.ruleset->'rules'))
        AND NOT EXISTS (
            SELECT FROM jsonb_each(q.counts) AS c(rule, item)
            WHERE NOT (q.ruleset->'rules' ? rule)
               OR item->'finding' IS DISTINCT FROM to_jsonb(
                   (SELECT count(*) FROM quality_finding WHERE run_id=q.id AND rule_id=rule))
        )
        AND NOT EXISTS(
            SELECT FROM quality_finding f WHERE f.run_id=q.id
            AND to_jsonb(f.rule_version) IS DISTINCT FROM q.ruleset->'rules'->f.rule_id
        )
        AND (SELECT count(*) FROM quality_attempt WHERE run_id=q.id AND status='completed')=1
        AND NOT EXISTS(SELECT FROM quality_attempt WHERE run_id=q.id AND status='running')
$$;
DO $$
BEGIN
    IF EXISTS(SELECT FROM ingestion_batch b WHERE status='published' AND ingestion_completion_valid(b) IS NOT TRUE)
        OR EXISTS(SELECT FROM quality_run q WHERE status='completed' AND quality_completion_valid(q) IS NOT TRUE) THEN
        RAISE EXCEPTION 'Historical stage completion records violate the proposed contract';
    END IF;
END $$;

CREATE FUNCTION ingestion_transition_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ((OLD.status IN ('registered','failed','running') AND NEW.status='running')
         OR (OLD.status='running' AND NEW.status IN ('published','failed'))) THEN
        RAISE EXCEPTION 'Invalid ingestion transition' USING ERRCODE='23514';
    END IF;
    IF NEW.status='published' AND ingestion_completion_valid(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'Incomplete ingestion publication' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_transition BEFORE UPDATE ON ingestion_batch
FOR EACH ROW EXECUTE FUNCTION ingestion_transition_guard();

CREATE FUNCTION quality_transition_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ((OLD.status IN ('registered','failed','running') AND NEW.status='running')
         OR (OLD.status='running' AND NEW.status IN ('completed','failed'))) THEN
        RAISE EXCEPTION 'Invalid quality transition' USING ERRCODE='23514';
    END IF;
    IF NEW.status='completed' AND quality_completion_valid(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'Incomplete quality publication' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER quality_transition BEFORE UPDATE ON quality_run
FOR EACH ROW EXECUTE FUNCTION quality_transition_guard();
