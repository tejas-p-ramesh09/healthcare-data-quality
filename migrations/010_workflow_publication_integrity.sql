-- Encode filenames exactly as Python json.dumps(..., ensure_ascii=True).
CREATE FUNCTION ingestion_manifest_label(label text) RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT coalesce(string_agg(CASE
        WHEN ascii(ch)<127 THEN substr(to_json(ch)::text,2,length(to_json(ch)::text)-2)
        WHEN ascii(ch)<65536 THEN '\u'||lpad(to_hex(ascii(ch)),4,'0')
        ELSE '\u'||to_hex(55296+((ascii(ch)-65536)>>10))
             ||'\u'||to_hex(56320+((ascii(ch)-65536)&1023))
        END,'' ORDER BY ord),'')
    FROM regexp_split_to_table(label,'') WITH ORDINALITY AS t(ch,ord)
$$;

CREATE FUNCTION ingestion_manifest_valid(b ingestion_batch) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT count(*) BETWEEN 1 AND 512
       AND sum(bytes)<=268435456
       AND bool_and(bytes<=16777216 AND sha256 ~ '^[0-9a-f]{64}$'
                    AND right(path,5)='.json' AND position('/' IN path)=0)
       AND b.manifest_sha256=encode(sha256(convert_to(
           '['||string_agg('["'||ingestion_manifest_label(path)||'",'||bytes||',"'||sha256||'"]',
                           ',' ORDER BY path COLLATE "C")||']','UTF8')),'hex')
    FROM ingestion_file WHERE batch_id=b.id
$$;

CREATE FUNCTION ingestion_manifest_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('running','published') AND ingestion_manifest_valid(NEW) IS NOT TRUE THEN
        RAISE EXCEPTION 'Ingestion manifest provenance mismatch' USING ERRCODE='23514';
    END IF;
    IF NEW.status='published' AND (
        (SELECT count(*) FROM raw_resource WHERE batch_id=NEW.id)>150000
        OR EXISTS(SELECT FROM raw_resource WHERE batch_id=NEW.id AND (
            jsonb_typeof(payload->'resourceType') IS DISTINCT FROM 'string'
            OR payload->>'resourceType' IS DISTINCT FROM resource_type
            OR payload->>'id' IS DISTINCT FROM fhir_id
            OR (disposition='normalized')<>(resource_type IN
                ('Patient','Encounter','Observation','Condition','MedicationRequest'))
        ))
        OR EXISTS(SELECT FROM raw_resource WHERE batch_id=NEW.id
                  GROUP BY path HAVING min(entry_index)<>0 OR max(entry_index)<>count(*)-1)
    ) THEN
        RAISE EXCEPTION 'Invalid persisted ingestion entries' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_manifest_consistency BEFORE UPDATE ON ingestion_batch
FOR EACH ROW EXECUTE FUNCTION ingestion_manifest_guard();

CREATE FUNCTION quality_rules_v1() RETURNS TABLE(code text,kind text,reason_a text,reason_b text)
LANGUAGE sql IMMUTABLE AS $$
    VALUES ('PATIENT_BIRTH_DATE_MISSING','Patient',NULL,NULL),
           ('OBSERVATION_CODE_NO_USABLE_CODING','Observation',NULL,NULL),
           ('OBSERVATION_AFTER_ENCOUNTER_END','Observation','NO_LINKED_ENCOUNTER','INCOMPARABLE_TIMES'),
           ('CONDITION_ABATEMENT_BEFORE_ONSET','Condition',NULL,'INCOMPARABLE_TIMES'),
           ('MEDICATION_ORDER_AUTHORED_ON_MISSING','MedicationRequest','NOT_ORDER',NULL)
$$;

CREATE FUNCTION quality_config_valid(q quality_run) RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT jsonb_typeof(q.ruleset)='object'
       AND q.ruleset->>'contract'='1'
       AND NOT EXISTS (SELECT FROM quality_rules_v1() k
                       WHERE q.ruleset->'rules'->>k.code IS DISTINCT FROM '1')
       AND (q.ruleset->>'encounter_grace_hours') ~ '^(0|[1-9][0-9]?|1[0-5][0-9]|16[0-8])$'
       AND q.ruleset=jsonb_build_object('contract',1,'encounter_grace_hours',
           q.ruleset->'encounter_grace_hours','rules',versions)
       AND jsonb_typeof(q.ruleset->'encounter_grace_hours')='number'
       AND q.ruleset_sha256=encode(sha256(convert_to(
           '{"contract":1,"encounter_grace_hours":'||(q.ruleset->>'encounter_grace_hours')
           ||',"rules":{'||canonical||'}}','UTF8')),'hex')
       AND EXISTS(SELECT FROM ingestion_batch WHERE id=q.batch_id AND status='published')
    FROM (SELECT jsonb_object_agg(code,1) AS versions,
                 string_agg('"'||code||'":1',',' ORDER BY code COLLATE "C") AS canonical
          FROM quality_rules_v1()) AS rules
$$;

CREATE FUNCTION quality_counts_valid(q quality_run) RETURNS boolean LANGUAGE sql STABLE AS $$
    WITH totals AS (
        SELECT resource_type,count(*) AS n FROM raw_resource
        WHERE batch_id=q.batch_id AND disposition='normalized' GROUP BY resource_type
    ), outcomes AS (
        SELECT k.*,q.counts->code AS item,coalesce(t.n,0) AS n
        FROM quality_rules_v1() k LEFT JOIN totals t ON t.resource_type=k.kind
    )
    SELECT NOT EXISTS (
        SELECT FROM outcomes
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR NOT item ?& ARRAY['pass_','finding','not_applicable','unable_to_assess']
           OR EXISTS (SELECT FROM jsonb_each(CASE WHEN jsonb_typeof(item)='object' THEN item ELSE '{}' END) a
               WHERE (a.key <> ALL(ARRAY['pass_','finding','not_applicable','unable_to_assess'])
                      AND a.key IS DISTINCT FROM reason_a AND a.key IS DISTINCT FROM reason_b)
                  OR jsonb_typeof(a.value)<>'number' OR a.value::text !~ '^(0|[1-9][0-9]*)$')
           OR CASE WHEN jsonb_typeof(item)='object' AND NOT EXISTS (
                    SELECT FROM jsonb_each(item) a
                    WHERE jsonb_typeof(a.value)<>'number' OR a.value::text !~ '^(0|[1-9][0-9]*)$')
                THEN (item->>'pass_')::numeric+(item->>'finding')::numeric
                     +(item->>'not_applicable')::numeric+(item->>'unable_to_assess')::numeric
                       IS DISTINCT FROM n::numeric
                     OR coalesce((item->>reason_a)::numeric,0) IS DISTINCT FROM (item->>'not_applicable')::numeric
                     OR coalesce((item->>reason_b)::numeric,0) IS DISTINCT FROM (item->>'unable_to_assess')::numeric
                ELSE true END
    ) AND NOT EXISTS (
        SELECT FROM quality_finding f JOIN raw_resource r
           ON (r.batch_id,r.path,r.entry_index)=(f.batch_id,f.path,f.entry_index)
        LEFT JOIN quality_rules_v1() k ON k.code=f.rule_id
        WHERE f.run_id=q.id AND (r.disposition<>'normalized' OR r.resource_type IS DISTINCT FROM k.kind)
    ) AND NOT EXISTS (
        SELECT FROM quality_finding WHERE run_id=q.id
        GROUP BY path,entry_index,rule_id HAVING count(*)>1
    )
$$;

CREATE FUNCTION quality_input_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF quality_config_valid(NEW) IS NOT TRUE
       OR (NEW.status='completed' AND quality_counts_valid(NEW) IS NOT TRUE) THEN
        RAISE EXCEPTION 'Invalid quality publication inputs or counts' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER quality_input_consistency BEFORE INSERT OR UPDATE ON quality_run
FOR EACH ROW EXECUTE FUNCTION quality_input_guard();

DO $$
BEGIN
    IF EXISTS(SELECT FROM ingestion_batch b WHERE status='published' AND ingestion_manifest_valid(b) IS NOT TRUE)
       OR EXISTS(SELECT FROM quality_run q WHERE status='completed'
                 AND (quality_config_valid(q) IS NOT TRUE OR quality_counts_valid(q) IS NOT TRUE)) THEN
        RAISE EXCEPTION 'Historical publication violates strengthened input contract';
    END IF;
END $$;

CREATE FUNCTION ingestion_file_registration_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT FROM ingestion_batch WHERE id=NEW.batch_id AND status='registered' FOR UPDATE) THEN
        RAISE EXCEPTION 'Ingestion file manifest is sealed' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ingestion_files_sealed BEFORE INSERT ON ingestion_file
FOR EACH ROW EXECUTE FUNCTION ingestion_file_registration_only();
