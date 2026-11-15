REVOKE INSERT ON matching_run FROM ingest;
GRANT INSERT (left_batch,right_batch,execution_sha256,configuration) ON matching_run TO ingest;

CREATE FUNCTION matching_registration_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'registered' OR NEW.counts IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'Matching runs must start registered' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER matching_registration BEFORE INSERT ON matching_run
FOR EACH ROW EXECUTE FUNCTION matching_registration_guard();

CREATE FUNCTION matching_completion_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    candidate_count bigint;
BEGIN
    IF NEW.status <> 'completed' THEN RETURN NEW; END IF;

    SELECT count(*) INTO candidate_count FROM matching_candidate WHERE run_id=NEW.id;
    IF NEW.counts->'candidates' IS DISTINCT FROM to_jsonb(candidate_count)
       OR (SELECT count(*) FROM matching_recommendation WHERE run_id=NEW.id) <> 2 * candidate_count
       OR (SELECT count(*) FROM matching_attempt WHERE run_id=NEW.id AND status='completed') <> 1
       OR EXISTS (SELECT FROM matching_attempt WHERE run_id=NEW.id AND status='running') THEN
        RAISE EXCEPTION 'Incomplete matching publication' USING ERRCODE='23514';
    END IF;
    -- Each recommendation has a score, and each score belongs to a candidate.
    -- The primary keys and method check allow at most two recommendations per candidate.
    RETURN NEW;
END $$;
CREATE TRIGGER matching_completion BEFORE UPDATE ON matching_run
FOR EACH ROW EXECUTE FUNCTION matching_completion_guard();
