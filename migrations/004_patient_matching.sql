CREATE TABLE matching_run (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    left_batch bigint NOT NULL REFERENCES ingestion_batch,
    right_batch bigint NOT NULL REFERENCES ingestion_batch,
    execution_sha256 text NOT NULL,
    configuration jsonb NOT NULL,
    status text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered','running','failed','completed')),
    counts jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (left_batch <> right_batch),
    CHECK ((status='completed') = (counts IS NOT NULL AND finished_at IS NOT NULL)),
    UNIQUE(left_batch,right_batch,execution_sha256),
    UNIQUE(id,left_batch,right_batch)
);
CREATE TABLE matching_attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id integer NOT NULL REFERENCES matching_run,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','failed','interrupted','completed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    error_code text
);
CREATE UNIQUE INDEX matching_one_attempt ON matching_attempt(run_id) WHERE status='running';
CREATE TABLE matching_candidate (
    run_id integer NOT NULL,
    left_batch bigint NOT NULL,
    right_batch bigint NOT NULL,
    left_id text NOT NULL,
    right_id text NOT NULL,
    block_codes jsonb NOT NULL,
    PRIMARY KEY(run_id,left_id,right_id),
    FOREIGN KEY (run_id,left_batch,right_batch) REFERENCES matching_run(id,left_batch,right_batch),
    FOREIGN KEY (left_batch,left_id) REFERENCES patient,
    FOREIGN KEY (right_batch,right_id) REFERENCES patient
);
CREATE TABLE matching_score (
    run_id integer NOT NULL,
    left_id text NOT NULL,
    right_id text NOT NULL,
    method text NOT NULL CHECK (method IN ('exact','statistical')),
    score double precision NOT NULL CHECK (score > '-Infinity' AND score < 'Infinity'),
    evidence jsonb NOT NULL,
    PRIMARY KEY(run_id,left_id,right_id,method),
    FOREIGN KEY(run_id,left_id,right_id) REFERENCES matching_candidate
);
CREATE TABLE matching_recommendation (
    run_id integer NOT NULL,
    left_id text NOT NULL,
    right_id text NOT NULL,
    method text NOT NULL,
    disposition text NOT NULL CHECK (disposition IN ('suggested_match','review','unlikely_match')),
    reason text NOT NULL,
    PRIMARY KEY(run_id,left_id,right_id,method),
    FOREIGN KEY(run_id,left_id,right_id,method) REFERENCES matching_score
);
CREATE FUNCTION matching_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM matching_run WHERE id=NEW.run_id AND status='running' FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'Matching run is not active' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE FUNCTION matching_state_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status='completed' OR NOT (
        (OLD.status IN ('registered','failed','running') AND NEW.status='running')
        OR (OLD.status='running' AND NEW.status IN ('completed','failed'))
    ) THEN RAISE EXCEPTION 'Invalid matching transition' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER matching_run_transition BEFORE UPDATE ON matching_run
FOR EACH ROW EXECUTE FUNCTION matching_state_guard();
CREATE TRIGGER matching_candidate_insert BEFORE INSERT ON matching_candidate
FOR EACH ROW EXECUTE FUNCTION matching_result_guard();
CREATE TRIGGER matching_score_insert BEFORE INSERT ON matching_score
FOR EACH ROW EXECUTE FUNCTION matching_result_guard();
CREATE TRIGGER matching_recommendation_insert BEFORE INSERT ON matching_recommendation
FOR EACH ROW EXECUTE FUNCTION matching_result_guard();
CREATE TRIGGER matching_attempt_insert BEFORE INSERT ON matching_attempt
FOR EACH ROW EXECUTE FUNCTION matching_result_guard();
GRANT SELECT, INSERT ON matching_run,matching_attempt,matching_candidate,matching_score,
    matching_recommendation TO ingest;
GRANT UPDATE(status,counts,finished_at) ON matching_run TO ingest;
GRANT UPDATE(status,finished_at,error_code) ON matching_attempt TO ingest;
GRANT USAGE ON SEQUENCE matching_run_id_seq,matching_attempt_id_seq TO ingest;
