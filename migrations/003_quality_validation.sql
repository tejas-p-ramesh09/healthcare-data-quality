CREATE TABLE quality_run (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY CHECK (id < 9223372036854775807),
    batch_id bigint NOT NULL REFERENCES ingestion_batch,
    ruleset_sha256 text NOT NULL,
    ruleset jsonb NOT NULL,
    engine_revision text NOT NULL,
    status text NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered','running','failed','completed')),
    counts jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK ((status='completed') = (counts IS NOT NULL AND completed_at IS NOT NULL)),
    UNIQUE (batch_id,ruleset_sha256,engine_revision),
    UNIQUE (id,batch_id)
);
CREATE TABLE quality_attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES quality_run,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed','interrupted')),
    error_code text
);
CREATE UNIQUE INDEX quality_one_running ON quality_attempt(run_id) WHERE status='running';
CREATE TABLE quality_finding (
    run_id bigint NOT NULL,
    batch_id bigint NOT NULL,
    path text NOT NULL,
    entry_index integer NOT NULL,
    rule_id text NOT NULL,
    rule_version integer NOT NULL,
    category text NOT NULL,
    severity text NOT NULL,
    pointer text NOT NULL,
    diagnostic text NOT NULL,
    related_path text,
    related_index integer,
    PRIMARY KEY (run_id,path,entry_index,rule_id,rule_version,pointer),
    FOREIGN KEY (run_id,batch_id) REFERENCES quality_run(id,batch_id),
    FOREIGN KEY (batch_id,path,entry_index) REFERENCES raw_resource,
    FOREIGN KEY (batch_id,related_path,related_index) REFERENCES raw_resource,
    CHECK ((related_path IS NULL) = (related_index IS NULL))
);
GRANT SELECT,INSERT ON quality_run,quality_attempt,quality_finding TO ingest;
GRANT UPDATE (status,counts,completed_at) ON quality_run TO ingest;
GRANT UPDATE (status,finished_at,error_code) ON quality_attempt TO ingest;
GRANT USAGE ON SEQUENCE quality_run_id_seq,quality_attempt_id_seq TO ingest;
