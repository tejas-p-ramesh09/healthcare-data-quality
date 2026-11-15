ALTER TABLE workflow_stage_attempt
    ADD COLUMN ingestion_attempt_id bigint UNIQUE REFERENCES ingestion_attempt,
    ADD COLUMN quality_attempt_id bigint UNIQUE REFERENCES quality_attempt,
    ADD COLUMN matching_attempt_id bigint UNIQUE REFERENCES matching_attempt,
    ADD CHECK (num_nonnulls(ingestion_attempt_id,quality_attempt_id,matching_attempt_id)<=1),
    ADD CHECK (ingestion_attempt_id IS NULL OR step IN (0,1)),
    ADD CHECK (quality_attempt_id IS NULL OR step IN (2,3)),
    ADD CHECK (matching_attempt_id IS NULL OR step=4);

-- Capture correlation in the transaction that claims the underlying attempt.
-- A session setting tags the caller. The trigger still verifies its pinned identity.
CREATE FUNCTION capture_workflow_attempt() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE
    tag text := current_setting('healthcare_dq.workflow_attempt', true);
    actual jsonb;
    journal record;
BEGIN
    IF coalesce(tag,'')='' THEN RETURN NEW; END IF;
    SELECT a.id,a.step,s.expected INTO STRICT journal
    FROM workflow_stage_attempt a JOIN workflow_stage s USING(workflow_id,step)
    WHERE a.id=tag::bigint AND a.status='running' AND s.status='running'
      AND num_nonnulls(a.ingestion_attempt_id,a.quality_attempt_id,a.matching_attempt_id)=0
    FOR UPDATE OF s NOWAIT;
    CASE TG_TABLE_NAME
    WHEN 'ingestion_attempt' THEN
        SELECT jsonb_build_array(src.source_key,b.manifest_sha256,b.contract_version) INTO actual
        FROM ingestion_batch b JOIN ingestion_source src ON src.id=b.source_id WHERE b.id=NEW.batch_id;
    WHEN 'quality_attempt' THEN
        SELECT jsonb_build_array(batch_id,ruleset_sha256,engine_revision) INTO actual
        FROM quality_run WHERE id=NEW.run_id;
    WHEN 'matching_attempt' THEN
        SELECT jsonb_build_array(left_batch,right_batch,execution_sha256) INTO actual
        FROM matching_run WHERE id=NEW.run_id;
    END CASE;
    IF actual IS NULL OR actual IS DISTINCT FROM journal.expected THEN
        RAISE EXCEPTION 'Workflow attempt identity mismatch' USING ERRCODE='23514';
    END IF;
    CASE TG_TABLE_NAME
    WHEN 'ingestion_attempt' THEN
        UPDATE workflow_stage_attempt SET ingestion_attempt_id=NEW.id WHERE id=journal.id;
    WHEN 'quality_attempt' THEN
        UPDATE workflow_stage_attempt SET quality_attempt_id=NEW.id WHERE id=journal.id;
    WHEN 'matching_attempt' THEN
        UPDATE workflow_stage_attempt SET matching_attempt_id=NEW.id WHERE id=journal.id;
    END CASE;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION capture_workflow_attempt() FROM PUBLIC;

CREATE TRIGGER ingestion_workflow_attempt AFTER INSERT ON ingestion_attempt
FOR EACH ROW EXECUTE FUNCTION capture_workflow_attempt();
CREATE TRIGGER quality_workflow_attempt AFTER INSERT ON quality_attempt
FOR EACH ROW EXECUTE FUNCTION capture_workflow_attempt();
CREATE TRIGGER matching_workflow_attempt AFTER INSERT ON matching_attempt
FOR EACH ROW EXECUTE FUNCTION capture_workflow_attempt();

CREATE FUNCTION workflow_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text;
        underlying_status text;
BEGIN
    SELECT status INTO parent_status FROM workflow_stage
    WHERE workflow_id=NEW.workflow_id AND step=NEW.step FOR UPDATE NOWAIT;
    IF parent_status IS DISTINCT FROM 'running' THEN
        RAISE EXCEPTION 'Workflow stage is not active' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.status='running' AND NEW.finished_at IS NULL AND NEW.code IS NULL
           AND NEW.retryable IS NULL THEN RETURN NEW; END IF;
    ELSIF OLD.status='running' THEN
        IF NEW.status='running'
           AND num_nonnulls(OLD.ingestion_attempt_id,OLD.quality_attempt_id,OLD.matching_attempt_id)=0
           AND num_nonnulls(NEW.ingestion_attempt_id,NEW.quality_attempt_id,NEW.matching_attempt_id)=1
           AND NEW.finished_at IS NULL AND NEW.code IS NULL AND NEW.retryable IS NULL THEN RETURN NEW; END IF;
        IF OLD.ingestion_attempt_id IS NOT NULL THEN
            SELECT status INTO STRICT underlying_status FROM ingestion_attempt WHERE id=OLD.ingestion_attempt_id;
        ELSIF OLD.quality_attempt_id IS NOT NULL THEN
            SELECT status INTO STRICT underlying_status FROM quality_attempt WHERE id=OLD.quality_attempt_id;
        ELSIF OLD.matching_attempt_id IS NOT NULL THEN
            SELECT status INTO STRICT underlying_status FROM matching_attempt WHERE id=OLD.matching_attempt_id;
        END IF;
        IF underlying_status='failed' AND NEW.status<>'failed' THEN
            RAISE EXCEPTION 'Workflow attempt has a durable underlying failure' USING ERRCODE='23514';
        END IF;
        IF NEW.finished_at IS NOT NULL AND (
            (NEW.status='completed' AND NEW.code IS NULL AND NEW.retryable IS NULL)
            OR (NEW.status='failed' AND NEW.code IS NOT NULL AND NEW.retryable IS NOT NULL)
            OR (NEW.status IN ('interrupted','deferred') AND NEW.code IS NOT NULL AND NEW.retryable IS TRUE)
        ) THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'Invalid workflow attempt transition' USING ERRCODE='23514';
END $$;
CREATE TRIGGER workflow_attempt_transition BEFORE INSERT OR UPDATE ON workflow_stage_attempt
FOR EACH ROW EXECUTE FUNCTION workflow_attempt_guard();

-- Verify the final state of both rows, allowing the caller to finalize the
-- attempt before attaching the result, in one transaction.
CREATE FUNCTION workflow_journal_valid(wid integer, stage_step integer) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT CASE s.status
        WHEN 'pending' THEN a.status IS NULL
        WHEN 'interrupted' THEN coalesce(a.status IN ('interrupted','deferred'),false)
        ELSE s.status IS NOT DISTINCT FROM a.status
    END FROM workflow_stage s LEFT JOIN LATERAL (
        SELECT status FROM workflow_stage_attempt
        WHERE workflow_id=wid AND step=stage_step ORDER BY id DESC LIMIT 1
    ) a ON true WHERE s.workflow_id=wid AND s.step=stage_step
$$;

CREATE FUNCTION workflow_journal_consistency() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF workflow_journal_valid(NEW.workflow_id,NEW.step) IS NOT TRUE THEN
        RAISE EXCEPTION 'Workflow stage and attempt journal disagree' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

DO $$
BEGIN
    IF EXISTS(SELECT FROM workflow_stage WHERE workflow_journal_valid(workflow_id,step) IS NOT TRUE) THEN
        RAISE EXCEPTION 'Historical workflow stage and attempt journal disagree';
    END IF;
END $$;
CREATE CONSTRAINT TRIGGER workflow_stage_journal AFTER INSERT OR UPDATE ON workflow_stage
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workflow_journal_consistency();
CREATE CONSTRAINT TRIGGER workflow_attempt_journal AFTER INSERT OR UPDATE ON workflow_stage_attempt
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION workflow_journal_consistency();

CREATE OR REPLACE VIEW workflow_schema_ready AS
SELECT EXISTS(SELECT FROM schema_migration WHERE version='011_workflow_attempt_integrity.sql') AS ready;

-- A correlated outcome remains stable even when another workflow or CLI later
-- retries the same underlying execution identity.
CREATE FUNCTION workflow_underlying_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status<>'running' OR NEW.status='running' OR NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'Invalid underlying attempt transition' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_attempt_terminal BEFORE UPDATE ON ingestion_attempt
FOR EACH ROW EXECUTE FUNCTION workflow_underlying_attempt_guard();
CREATE TRIGGER quality_attempt_terminal BEFORE UPDATE ON quality_attempt
FOR EACH ROW EXECUTE FUNCTION workflow_underlying_attempt_guard();
CREATE TRIGGER matching_attempt_terminal BEFORE UPDATE ON matching_attempt
FOR EACH ROW EXECUTE FUNCTION workflow_underlying_attempt_guard();
