import type { Finding, Recommendation, Workflow } from '../src/api/client'

export const hash = 'a'.repeat(64)
export const inputs = [
  { key: 'demo-source-a', source: 'left-source', manifest: hash },
  { key: 'demo-source-b', source: 'right-source', manifest: hash },
]
export const catalog = { fixtures: inputs, profiles: ['demo-v1'] }

export function workflow(statuses: Workflow['stages'][number]['status'][]): Workflow {
  const date = '2026-01-01T00:00:00Z'
  const stages = ['ingest_left', 'ingest_right', 'validate_left', 'validate_right', 'match'].map((name, i) => ({
    name, status: statuses[i] ?? 'pending' as const, attempt_count: statuses[i] ? 1 : 0,
    updated_at: date, batch_id: i + 1, run_id: i + 1, counts: {}, failure: null,
  }))
  return { id: 1, profile: 'demo-v1', inputs, stages, status: stages.find(s => s.status !== 'completed')?.status ??
    'completed', created_at: date, updated_at: date, ruleset_sha256: hash, model_sha256: hash,
  ruleset_file_sha256: hash, model_file_sha256: hash, matching_execution: hash, quality_revision: hash,
  ingestion_contract: 1 }
}
export const complete = workflow(Array(5).fill('completed'))
complete.stages[4].counts = { no_candidate_left: 0, no_candidate_right: 1 }

export function finding(n: number): Finding {
  return { file_label: 'bundle.json', entry_index: n, rule_id: 'PATIENT_BIRTH_DATE_MISSING', rule_version: 1,
    severity: 'warning', diagnostic: `Finding ${n}`, category: 'completeness', pointer: '/birthDate',
    related_file_label: null, related_file_sha256: null, related_index: null, file_sha256: hash,
    fhir_id: `synthetic-${n}`, resource_type: 'Patient', batch_id: 1 }
}

export function recommendation(method: Recommendation['method'], n = 1): Recommendation {
  return { left_id: `left-${n}`, right_id: `right-${n}`, left_batch: 1, right_batch: 2,
    block_codes: ['name_birth'], method, score: 1, disposition: 'suggested_match', reason: 'above_threshold',
    evidence: { levels: { given: 'exact' }, contributions: { given: 0.35 } },
    left_file_label: 'bundle.json', left_entry_index: 0, left_file_sha256: hash,
    right_file_label: 'bundle.json', right_entry_index: 1, right_file_sha256: hash }
}
