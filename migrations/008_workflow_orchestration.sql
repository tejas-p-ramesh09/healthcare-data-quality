CREATE TABLE workflow (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_sha256 text NOT NULL UNIQUE,
    spec jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE workflow_request_key (
    key text PRIMARY KEY,
    request_sha256 text NOT NULL,
    workflow_id integer NOT NULL REFERENCES workflow
);
CREATE TABLE workflow_stage (
    workflow_id integer NOT NULL REFERENCES workflow,
    step integer NOT NULL CHECK (step BETWEEN 0 AND 4),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','interrupted','failed','completed')),
    expected jsonb,
    batch_id bigint REFERENCES ingestion_batch,
    quality_id bigint REFERENCES quality_run,
    matching_id integer REFERENCES matching_run,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(workflow_id,step),
    CHECK ((status='completed') = (num_nonnulls(batch_id,quality_id,matching_id)=1)),
    CHECK (batch_id IS NULL OR step IN (0,1)),
    CHECK (quality_id IS NULL OR step IN (2,3)),
    CHECK (matching_id IS NULL OR step=4)
);
CREATE TABLE workflow_stage_attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workflow_id integer NOT NULL,
    step integer NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','completed','failed','interrupted','deferred')),
    code text,
    retryable boolean,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    FOREIGN KEY(workflow_id,step) REFERENCES workflow_stage
);
CREATE UNIQUE INDEX workflow_one_active ON workflow_stage_attempt(workflow_id)
    WHERE status='running';
CREATE INDEX workflow_attempt_stage ON workflow_stage_attempt(workflow_id,step,id);

CREATE FUNCTION workflow_stage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual jsonb;
BEGIN
    IF OLD.status='completed' OR
       (OLD.expected IS NOT NULL AND OLD.expected IS DISTINCT FROM NEW.expected) THEN
        RAISE EXCEPTION 'Immutable workflow stage identity' USING ERRCODE='23514';
    END IF;
    IF NOT ((OLD.status IN ('pending','failed','interrupted') AND NEW.status='running')
         OR (OLD.status='running' AND NEW.status IN ('completed','failed','interrupted'))) THEN
        RAISE EXCEPTION 'Invalid workflow transition' USING ERRCODE='23514';
    END IF;
    IF NEW.status='running' AND (
        NEW.expected IS NULL OR EXISTS(SELECT FROM workflow_stage
          WHERE workflow_id=NEW.workflow_id AND step<NEW.step AND status<>'completed')
    ) THEN RAISE EXCEPTION 'Unready workflow stage' USING ERRCODE='23514'; END IF;
    IF NEW.status <> 'completed' THEN RETURN NEW; END IF;
    IF NEW.step IN (0,1) THEN
        SELECT jsonb_build_array(s.source_key,b.manifest_sha256,b.contract_version) INTO actual
        FROM ingestion_batch b JOIN ingestion_source s ON s.id=b.source_id
        WHERE b.id=NEW.batch_id AND b.status='published';
    ELSIF NEW.step IN (2,3) THEN
        SELECT jsonb_build_array(q.batch_id,q.ruleset_sha256,q.engine_revision) INTO actual
        FROM quality_run q JOIN workflow_stage prior
          ON prior.workflow_id=NEW.workflow_id AND prior.step=NEW.step-2
             AND prior.status='completed' AND prior.batch_id=q.batch_id
        WHERE q.id=NEW.quality_id AND q.status='completed';
    ELSE
        SELECT jsonb_build_array(m.left_batch,m.right_batch,m.execution_sha256) INTO actual
        FROM matching_run m JOIN workflow_stage l ON l.workflow_id=NEW.workflow_id AND l.step=0
            AND l.status='completed' AND l.batch_id=m.left_batch
        JOIN workflow_stage r ON r.workflow_id=NEW.workflow_id AND r.step=1
            AND r.status='completed' AND r.batch_id=m.right_batch
        WHERE m.id=NEW.matching_id AND m.status='completed';
    END IF;
    IF actual IS NULL OR actual IS DISTINCT FROM NEW.expected THEN
        RAISE EXCEPTION 'Workflow result identity mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER workflow_stage_transition BEFORE UPDATE ON workflow_stage
FOR EACH ROW EXECUTE FUNCTION workflow_stage_guard();
