import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from healthcare_dq.db import ROOT, connect, migrate
from healthcare_dq.ingestion import ingest
from healthcare_dq.inputs import discover, manifest
from healthcare_dq.quality.engine import ruleset
from healthcare_dq.quality.evaluation import score
from healthcare_dq.quality.mutation import freeze
from healthcare_dq.quality.repository import findings
from healthcare_dq.quality.service import validate
from tests.unit.test_ingestion_inputs import entry, file, ref


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL required")
class QualityExperiment(unittest.TestCase):
    def test_frozen_variant_scores_exact_expected_labels(self):
        with TemporaryDirectory() as tmp, connect("TEST_DATABASE_URL") as db:
            migrate(db)
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            start = "2025-01-01T00:00:00Z"
            rows = [entry("Patient", "p", birthDate="2000"),
                    entry("Encounter", "e", subject=ref("Patient/p"), status="finished", period={"end": start}),
                    entry("MedicationRequest", "m", subject=ref("Patient/p"), status="active",
                          intent="order", authoredOn=start),
                    entry("Condition", "c", subject=ref("Patient/p"),
                          onsetDateTime=start, abatementDateTime=start)]
            rows += [entry("Observation", ident, subject=ref("Patient/p"),
                           encounter=ref("Encounter/e"), status="final", effectiveDateTime=start,
                           code={"coding": [{"system": "urn:example", "code": "x"}]})
                     for ident in ("o1", "o2")]
            original = file(*rows)
            (source / original.path).write_bytes(original.data)
            config = ruleset(ROOT / "config/quality/ruleset-v1.json")
            settings = {"version": 1, "seed": 12, "positives_per_rule": 1, "controls_per_rule": 0,
                        "baseline_manifest": manifest(discover(source))}
            m = freeze(source, root / "variant", settings, config)
            self.assertEqual(m, freeze(source, root / "repeat", settings, config))
            self.assertEqual((source / original.path).read_bytes(), original.data)
            self.assertEqual(json.loads((root / "variant/manifest.json").read_text()), m)
            before_batch = ingest(db, str(uuid4()), discover(source))["batch_id"]
            after_batch = ingest(db, str(uuid4()), discover(root / "variant/fhir"))["batch_id"]
            before = validate(db, before_batch, config)
            after = validate(db, after_batch, config)
            output = score(m["labels"], findings(db, before["id"]), findings(db, after["id"]))
            self.assertEqual(output["micro"]["tp"], 5)
            self.assertEqual(output["micro"]["fp"], 0)
            self.assertEqual(output["micro"]["fn"], 0)
