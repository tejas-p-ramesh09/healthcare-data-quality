import { useQuery } from '@tanstack/react-query'
import { Link, useOutletContext, useSearchParams } from 'react-router-dom'
import type { WorkflowContext } from '../ui'
import type { Api, Finding, Page, Recommendation, Side, SourceFile, Stage } from '../api/client'
import { useSession } from '../api/session'
import { ErrorNotice, Hash, Loading, Pagination, SideNav, Status, provenanceUrl, readCount, usePages } from '../ui'

function Unavailable({ stage, id }: { stage?: Stage; id: number }) {
  return <div className="callout">
    <Status state={stage?.status ?? 'unknown'} />
    <p>{stage?.status === 'failed' ? 'This report could not be published.' :
      stage?.status === 'interrupted' ? 'Resume the interrupted stage to make this report available.' :
        'Results will be available when this stage completes.'}</p>
    <Link to={`/workflows/${id}`}>View execution stages</Link>
  </div>
}

function useSide(): Side { return useSearchParams()[0].get('side') === 'right' ? 'right' : 'left' }

export function Quality() {
  const { workflow } = useOutletContext<WorkflowContext>()
  const side = useSide()
  const stage = workflow.stages.find(s => s.name === `validate_${side}`)
  return <section className="surface report" aria-labelledby="quality-title">
    <div className="surface-heading"><div><h2 id="quality-title">Data quality findings</h2>
      <p>Project-specific quality checks. Findings do not establish FHIR conformance or clinical correctness.</p>
    </div></div>
    <SideNav side={side} url={`/workflows/${workflow.id}/quality`} />
    {stage?.status !== 'completed' ? <Unavailable stage={stage} id={workflow.id} /> :
      <Findings key={side} id={workflow.id} side={side} />}
  </section>
}

function Findings({ id, side }: { id: number; side: Side }) {
  const { api } = useSession()
  const page = usePages<string | null>(null)
  const output = useQuery({ queryKey: ['findings', id, side, page.cursor],
    queryFn: ({ signal }) => api.findings(id, side, page.cursor, signal) })
  const next = output.data?.next_cursor
  return <>
    {output.isPending && <Loading>Loading findings…</Loading>}
    {output.isError && <ErrorNotice error={output.error} />}
    {output.data && (output.data.items.length ?
      <div className="evidence-list">{output.data.items.map(f =>
        <FindingCard key={JSON.stringify([f.file_label, f.entry_index, f.rule_id, f.rule_version, f.pointer])}
          finding={f} id={id} side={side} />)}</div> :
      <div className="empty"><h3>No project quality findings for this source</h3>
        <p>The validation stage completed and returned zero findings.</p></div>)}
    <Pagination {...page} next={next ? () => page.next(next) : undefined} />
  </>
}

function FindingCard({ finding: f, id, side }: { finding: Finding; id: number; side: Side }) {
  const file = provenanceUrl(id, side, f.batch_id, f.file_label, f.file_sha256, f.entry_index)
  const related = f.related_file_label !== null && f.related_file_sha256 !== null && f.related_index !== null
    ? provenanceUrl(id, side, f.batch_id, f.related_file_label, f.related_file_sha256, f.related_index) : null
  return <article className="evidence-card">
    <div className="finding-title"><h3>{f.diagnostic}</h3><span className="label-chip">{f.severity}</span></div>
    <p><code>{f.rule_id}</code> · version {f.rule_version} · {f.category.replaceAll('_', ' ')}</p>
    <details><summary>Finding evidence</summary>
      <dl className="evidence-grid">
        <div><dt>Resource reference</dt><dd><code>{f.resource_type}/{f.fhir_id}</code></dd></div>
        <div><dt>Field pointer</dt><dd><code>{f.pointer}</code></dd></div>
        <div><dt>Source evidence</dt><dd><Link to={file}>{f.file_label} · entry {f.entry_index}</Link></dd></div>
        <div><dt>File SHA-256</dt><dd><Hash value={f.file_sha256} /></dd></div>
        {related && <div><dt>Related resource</dt><dd><Link to={related}>
          {f.related_file_label} · entry {f.related_index}</Link></dd></div>}
      </dl>
    </details>
  </article>
}

const comparisonFeatures = {
  given: 'Given name', family: 'Family name', birth: 'Birth date', phone: 'Phone',
  street: 'Street', zip: 'Postal code',
} as const
const comparisonLevels = new Set(['exact', 'close', 'conflict', 'missing'])

export type Pair = { exact: Recommendation; statistical: Recommendation }
export function alignPairs(exact: Page<Recommendation>, statistical: Page<Recommendation>): Pair[] {
  const key = (r: Recommendation) => JSON.stringify([r.left_id, r.right_id])
  const other = new Map(statistical.items.map(item => [key(item), item]))
  if (other.size !== statistical.items.length || exact.items.length !== other.size ||
      !!exact.next_cursor !== !!statistical.next_cursor) throw new Error('Candidate pages disagree')
  return exact.items.map(item => {
    const found = other.get(key(item))
    other.delete(key(item))
    if (!found || item.method !== 'exact' || found.method !== 'statistical' ||
        item.left_batch !== found.left_batch || item.right_batch !== found.right_batch) {
      throw new Error('Candidate pages disagree')
    }
    return { exact: item, statistical: found }
  })
}

export function Matching() {
  const { workflow } = useOutletContext<WorkflowContext>()
  const stage = workflow.stages.find(s => s.name === 'match')
  return <section className="surface report" aria-labelledby="match-title">
    <div className="surface-heading"><div><h2 id="match-title">Patient matching</h2>
      <p>Compare methods on the same candidate pairs. Suggested matches require human review.</p></div></div>
    {stage?.status !== 'completed' ? <Unavailable stage={stage} id={workflow.id} /> :
      <MatchingReport id={workflow.id} stage={stage} />}
  </section>
}

function MatchingReport({ id, stage }: { id: number; stage: Stage }) {
  const { api } = useSession()
  const page = usePages<[string | null, string | null]>([null, null])
  const result = useQuery({
    queryKey: ['matching', id, ...page.cursor],
    queryFn: async ({ signal }) => {
      const [exact, statistical] = await Promise.all([
        api.recommendations(id, 'exact', page.cursor[0], signal),
        api.recommendations(id, 'statistical', page.cursor[1], signal),
      ])
      return { items: alignPairs(exact, statistical), next: [exact.next_cursor, statistical.next_cursor] as const }
    },
  })
  const left = readCount(stage.counts ?? null, 'no_candidate_left')
  const right = readCount(stage.counts ?? null, 'no_candidate_right')
  const next = result.data?.next
  return <>
    {left !== undefined && right !== undefined && <div className="no-candidates">
      <div><span>Left without a candidate</span><strong>{left}</strong></div>
      <div><span>Right without a candidate</span><strong>{right}</strong></div>
      <p>These Patients generated no pair under the configured blocking rules. This does not rule out duplicates.</p>
    </div>}
    {result.isPending && <Loading>Loading both matching methods…</Loading>}
    {result.isError && <div className="callout error" role="alert">
      <strong>Comparison unavailable.</strong> {result.error.name === 'Error' &&
        result.error.message === 'Candidate pages disagree' ? 'The methods returned different candidate pages.' :
        'Both result pages must load before this comparison can be shown.'}
      <button className="secondary" onClick={() => void result.refetch()}>Retry comparison</button>
    </div>}
    {result.data && (result.data.items.length ? <div className="evidence-list">
      {result.data.items.map(pair => <PairCard key={JSON.stringify([pair.exact.left_id, pair.exact.right_id])}
        pair={pair} id={id} />)}
    </div> : <div className="empty"><h3>No candidate pairs</h3><p>Matching completed. The blocking rules produced zero pairs.</p></div>)}
    <Pagination {...page} next={next?.[0] && next[1] ? () => page.next([next[0], next[1]]) : undefined} />
  </>
}

const dispositions = {
  suggested_match: 'Suggested match · review required',
  review: 'Review recommended',
  unlikely_match: 'Unlikely match',
}

function PairCard({ pair, id }: { pair: Pair; id: number }) {
  const { exact: row, statistical } = pair
  return <article className="evidence-card">
    <div className="finding-title"><h3>Candidate pair</h3>
      <span className="label-chip">Synthetic Patients</span></div>
    <div className="pair-sources">
      <Link to={provenanceUrl(id, 'left', row.left_batch, row.left_file_label, row.left_file_sha256, row.left_entry_index)}>
        Left · <code>{row.left_id}</code></Link>
      <Link to={provenanceUrl(id, 'right', row.right_batch, row.right_file_label, row.right_file_sha256, row.right_entry_index)}>
        Right · <code>{row.right_id}</code></Link>
    </div>
    <div className="method-grid">
      <section><h4>Exact baseline</h4><strong>{dispositions[row.disposition]}</strong>
        <p>Exact score {row.score} · {row.reason.replaceAll('_', ' ')}</p></section>
      <section><h4>Statistical method</h4><strong>{dispositions[statistical.disposition]}</strong>
        <p>Ranking score {statistical.score.toFixed(3)} · {statistical.reason.replaceAll('_', ' ')}</p></section>
    </div>
    <details><summary>Comparison evidence</summary>
      <p>Blocking rules: {row.block_codes.join(', ') || 'Unavailable'}</p>
      <p className="hint">Comparison levels and score contributions summarize attributes without displaying their values. The ranking score is not a probability.</p>
      <table><thead><tr><th scope="col">Feature</th><th scope="col">Comparison</th><th scope="col">Contribution</th></tr></thead>
        <tbody>{Object.entries(comparisonFeatures).map(([feature, label]) => {
          const level = statistical.evidence.levels[feature]
          const contribution = statistical.evidence.contributions[feature]
          return <tr key={feature}><th scope="row">{label}</th><td>{comparisonLevels.has(level) ? level : '—'}</td>
            <td>{typeof contribution === 'number' && Number.isFinite(contribution) ? contribution.toFixed(3) : '—'}</td></tr>
        })}</tbody>
      </table>
    </details>
  </article>
}

export async function linkedFile(api: Api, id: number, side: Side, label: string, sha: string, signal: AbortSignal) {
  let cursor: string | null = null
  for (let page = 0; page < 21; page++) {
    const output: Page<SourceFile> = await api.files(id, side, cursor, signal)
    const found = output.items.find(item => item.file_label === label && item.sha256 === sha)
    if (found) return found
    if (!output.next_cursor) return null
    cursor = output.next_cursor
  }
  throw new Error('Source file page limit exceeded')
}

export function Provenance() {
  const { workflow } = useOutletContext<WorkflowContext>()
  const side = useSide()
  const stage = workflow.stages.find(s => s.name === `ingest_${side}`)
  return <section className="surface report" aria-labelledby="provenance-title">
    <div className="surface-heading"><div><h2 id="provenance-title">Source provenance</h2>
      <p>Trace reports to the published batch and its synthetic input files.</p></div></div>
    <SideNav side={side} url={`/workflows/${workflow.id}/provenance`} />
    {stage?.status !== 'completed' ? <Unavailable stage={stage} id={workflow.id} /> :
      <BatchReport key={side} id={workflow.id} side={side} />}
    <details className="pinned"><summary>Pinned execution identities</summary>
      <dl className="evidence-grid">
        <div><dt>Ruleset SHA-256</dt><dd><Hash value={workflow.ruleset_sha256} /></dd></div>
        <div><dt>Model SHA-256</dt><dd><Hash value={workflow.model_sha256} /></dd></div>
        <div><dt>Quality engine revision</dt><dd><Hash value={workflow.quality_revision} /></dd></div>
        <div><dt>Matching execution digest</dt><dd><Hash value={workflow.matching_execution} /></dd></div>
      </dl>
    </details>
  </section>
}

function BatchReport({ id, side }: { id: number; side: Side }) {
  const { api } = useSession()
  const [params] = useSearchParams()
  const label = params.get('file')
  const hash = params.get('sha')
  const batchParam = params.get('batch')
  const entry = params.get('entry')
  const page = usePages<string | null>(null)
  const batch = useQuery({ queryKey: ['batch', id, side], queryFn: ({ signal }) => api.batch(id, side, signal) })
  const files = useQuery({ queryKey: ['files', id, side, page.cursor], queryFn: ({ signal }) =>
    api.files(id, side, page.cursor, signal) })
  const linked = useQuery({ enabled: !!(label && hash && /^[a-f0-9]{64}$/.test(hash) &&
      batch.data && String(batch.data.batch_id) === batchParam),
    queryKey: ['linked-file', id, side, batchParam, label, hash],
    queryFn: ({ signal }) => linkedFile(api, id, side, label!, hash!, signal) })
  const next = files.data?.next_cursor
  return <>
    {batch.isPending && <Loading>Loading batch provenance…</Loading>}
    {batch.isError && <ErrorNotice error={batch.error} />}
    {batch.data && <dl className="evidence-grid batch">
      <div><dt>Published batch</dt><dd>#{batch.data.batch_id}</dd></div>
      <div><dt>Source namespace</dt><dd>{batch.data.source_key}</dd></div>
      <div><dt>Manifest SHA-256</dt><dd><Hash value={batch.data.manifest_sha256} /></dd></div>
    </dl>}
    {label && (linked.fetchStatus === 'fetching' ? <Loading>Locating the linked file…</Loading> :
      linked.isError ? <ErrorNotice error={linked.error} /> : linked.data ? <section className="callout linked-file">
        <h3>Linked source evidence</h3><p>{linked.data.file_label}
          {entry && /^(0|[1-9][0-9]{0,8})$/.test(entry) && <> · entry {entry} (from report link)</>}</p>
        <Hash value={linked.data.sha256} />
      </section> : batch.data && <p className="callout error" role="alert">The linked file was not found in this source batch.</p>)}
    <h3>Published files</h3>
    {files.isPending && <Loading>Loading file provenance…</Loading>}
    {files.isError && <ErrorNotice error={files.error} />}
    {files.data && (files.data.items.length ? <div className="file-list">
      {files.data.items.map(file => <div key={file.file_label}>
        <strong>{file.file_label}</strong><span>{file.bytes.toLocaleString()} bytes</span><Hash value={file.sha256} />
      </div>)}</div> : <p className="empty">No files were returned for this page.</p>)}
    <Pagination {...page} next={next ? () => page.next(next) : undefined} />
  </>
}
