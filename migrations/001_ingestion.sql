CREATE TABLE ingestion_source (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_key text NOT NULL UNIQUE
);
CREATE TABLE ingestion_batch (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id bigint NOT NULL REFERENCES ingestion_source,
    manifest_sha256 text NOT NULL,
    contract_version integer NOT NULL,
    status text NOT NULL DEFAULT 'registered'
        CHECK (status IN ('registered', 'running', 'failed', 'published')),
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    counts jsonb,
    CHECK ((status = 'published') = (published_at IS NOT NULL AND counts IS NOT NULL)),
    UNIQUE (source_id, manifest_sha256, contract_version)
);
CREATE TABLE ingestion_file (
    batch_id bigint NOT NULL REFERENCES ingestion_batch,
    path text NOT NULL,
    bytes bigint NOT NULL CHECK (bytes >= 0),
    sha256 text NOT NULL,
    PRIMARY KEY (batch_id, path)
);
CREATE TABLE ingestion_attempt (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES ingestion_batch,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'published', 'failed', 'interrupted')),
    retryable boolean,
    error_code text
);
CREATE UNIQUE INDEX one_running_attempt ON ingestion_attempt(batch_id) WHERE status = 'running';
CREATE TABLE ingestion_error (
    attempt_id bigint PRIMARY KEY REFERENCES ingestion_attempt,
    path text,
    pointer text,
    code text NOT NULL
);
CREATE TABLE raw_resource (
    batch_id bigint NOT NULL,
    path text NOT NULL,
    entry_index integer NOT NULL CHECK (entry_index >= 0),
    full_url text,
    resource_type text NOT NULL,
    fhir_id text,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    disposition text NOT NULL CHECK (disposition IN ('normalized', 'raw_only')),
    PRIMARY KEY (batch_id, path, entry_index),
    FOREIGN KEY (batch_id, path) REFERENCES ingestion_file,
    UNIQUE (batch_id, path, entry_index, resource_type, fhir_id)
);
CREATE FUNCTION create_projection(name text, kind text, fields text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('CREATE TABLE %I (
        batch_id bigint NOT NULL, fhir_id text NOT NULL, path text NOT NULL, entry_index integer NOT NULL,
        resource_type text GENERATED ALWAYS AS (%L::text) STORED,
        PRIMARY KEY (batch_id, fhir_id),
        FOREIGN KEY (batch_id, path, entry_index, resource_type, fhir_id)
            REFERENCES raw_resource(batch_id, path, entry_index, resource_type, fhir_id), %s)',
        name, kind, fields);
END $$;
SELECT create_projection('patient', 'Patient', 'gender text, birth_date text');
SELECT create_projection('encounter', 'Encounter', '
    patient_id text NOT NULL, status text NOT NULL, class_code text, period_start text, period_end text,
    UNIQUE (batch_id, fhir_id, patient_id),
    FOREIGN KEY (batch_id, patient_id) REFERENCES patient');
SELECT create_projection('observation', 'Observation', '
    patient_id text NOT NULL, encounter_id text, status text NOT NULL, code jsonb NOT NULL,
    effective_at text, quantity numeric, quantity_unit text, value_string text, value_code jsonb,
    FOREIGN KEY (batch_id, patient_id) REFERENCES patient,
    FOREIGN KEY (batch_id, encounter_id, patient_id) REFERENCES encounter(batch_id, fhir_id, patient_id)');
SELECT create_projection('condition', 'Condition', '
    patient_id text NOT NULL, encounter_id text, code jsonb, clinical_status jsonb,
    verification_status jsonb, onset_at text, recorded_at text,
    FOREIGN KEY (batch_id, patient_id) REFERENCES patient,
    FOREIGN KEY (batch_id, encounter_id, patient_id) REFERENCES encounter(batch_id, fhir_id, patient_id)');
SELECT create_projection('medication_request', 'MedicationRequest', '
    patient_id text NOT NULL, encounter_id text, status text NOT NULL, intent text NOT NULL,
    authored_at text, medication_code jsonb,
    FOREIGN KEY (batch_id, patient_id) REFERENCES patient,
    FOREIGN KEY (batch_id, encounter_id, patient_id) REFERENCES encounter(batch_id, fhir_id, patient_id)');
DROP FUNCTION create_projection(text, text, text);
CREATE INDEX encounter_patient ON encounter(batch_id, patient_id);
CREATE INDEX observation_patient ON observation(batch_id, patient_id);
CREATE INDEX condition_patient ON condition(batch_id, patient_id);
CREATE INDEX medication_patient ON medication_request(batch_id, patient_id);
CREATE INDEX raw_identity ON raw_resource(batch_id, resource_type, fhir_id);
