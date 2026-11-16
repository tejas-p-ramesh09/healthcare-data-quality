import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import type { WorkflowContext } from '../ui'
import { ApiError } from '../api/client'
import type { Stage, Submission, Workflow } from '../api/client'
import { useSession } from '../api/session'
import { applyConfirmedWorkflow, workflowKey } from '../api/workflow-state'
import { DateTime, ErrorNotice, Loading, Pagination, Status, usePages } from '../ui'

const stageLabels: Record<string, string> = {
  ingest_left: 'Ingest left source', ingest_right: 'Ingest right source',
  validate_left: 'Validate left source', validate_right: 'Validate right source', match: 'Match Patients',
}
const failureGuidance: Record<string, string> = {
  INVALID_JSON: 'The approved fixture contains invalid JSON.',
  PUBLICATION_FAILED: 'The stage could not publish its results.',
  EXECUTOR_INTERRUPTED: 'The previous execution ended before it could record an outcome.',
  STAGE_BUSY: 'The underlying work is still held by another executor.',
  VALIDATION_ENGINE_FAILED: 'The quality engine could not finish its checks.',
  MATCHING_WRITE_FAILED: 'The matching stage could not publish its results.',
  OVERSIZED_BLOCK: 'The matching inputs exceeded the supported blocking size.',
}

function stageLabel(stage: Stage) { return stageLabels[stage.name] ?? 'Workflow stage' }

export function Overview() {
  const { api } = useSession()
  const page = usePages<string | null>(null)
  const result = useQuery({
    queryKey: ['workflows', page.cursor],
    queryFn: ({ signal }) => api.workflows(page.cursor, signal),
  })
  const rows = result.data?.items ?? []
  const next = result.data?.next_cursor
  return <>
    <div className="page-heading overview-heading">
      <div><p className="eyebrow">Local workflow history</p><h1>Workflows</h1>
        <p>Run synthetic FHIR checks and trace findings to their source.</p></div>
      <Link className="primary" to="/workflows/new">＋ New workflow</Link>
    </div>
    <section className="surface list" aria-labelledby="history">
      <div className="surface-heading"><h2 id="history">Workflow history</h2><button className="quiet"
        onClick={() => void result.refetch()} disabled={result.isFetching}>Refresh</button></div>
      {result.isPending && <Loading>Loading workflow history…</Loading>}
      {result.isError && <ErrorNotice error={result.error} />}
      {result.isSuccess && !rows.length && <div className="empty"><h3>No workflows on this page</h3>
        <p>Create a workflow to explore the approved synthetic fixtures.</p></div>}
      {!!rows.length && <div className="table-scroll" role="region" aria-label="Workflow history" tabIndex={0}>
        <table><thead><tr><th scope="col">Workflow</th><th scope="col">Status</th><th scope="col">Progress</th>
          <th scope="col">Last updated</th></tr></thead>
          <tbody>{rows.map(w => <tr key={w.id}>
            <td><Link className="table-link" to={`/workflows/${w.id}`}>Workflow #{w.id}</Link>
              <small>{w.inputs.map(i => i.source).join(' / ')} · {w.profile}</small></td>
            <td><Status state={w.status} /></td>
            <td>{w.stages.filter(s => s.status === 'completed').length} of {w.stages.length} stages</td>
            <td><DateTime value={w.updated_at} /></td>
          </tr>)}</tbody>
        </table>
      </div>}
      <Pagination {...page} next={next ? () => page.next(next) : undefined} />
    </section>
  </>
}

export function CreateWorkflow() {
  const { api, isActive } = useSession()
  const cache = useQueryClient()
  const navigate = useNavigate()
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: ({ signal }) => api.catalog(signal) })
  const [input, setInput] = useState<Submission>({ left_fixture: '', right_fixture: '', profile: '' })
  const intent = useRef<{ key: string; payload: string } | null>(null)
  const submitting = useRef(false)
  const mutation = useMutation({
    networkMode: 'always', retry: false,
    mutationFn: ({ payload, key }: { payload: Submission; key: string }) => api.submit(payload, key),
  })
  const left = catalog.data?.fixtures.find(f => f.key === input.left_fixture)
  const right = catalog.data?.fixtures.find(f => f.key === input.right_fixture)
  const profile = input.profile || catalog.data?.profiles[0] || ''
  const sameSource = left && right && left.source === right.source

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting.current || !left || !right || !profile || sameSource) return
    const payload = { ...input, profile }
    const signature = JSON.stringify(payload)
    if (intent.current?.payload !== signature) intent.current = { payload: signature, key: crypto.randomUUID() }
    submitting.current = true
    try {
      const workflow = await mutation.mutateAsync({ payload, key: intent.current.key })
      if (!isActive()) return
      await applyConfirmedWorkflow(cache, workflow, isActive)
      if (!isActive()) return
      navigate(`/workflows/${workflow.id}`)
    } catch {
      // The same selection retains its submission key after failure.
    } finally { submitting.current = false }
  }

  function set(field: keyof Submission, value: string) {
    intent.current = null
    mutation.reset()
    setInput(previous => ({ ...previous, [field]: value }))
  }

  return <>
    <Link className="back" to="/workflows">← All workflows</Link>
    <div className="page-heading"><div><p className="eyebrow">Synthetic inputs</p><h1>New workflow</h1>
      <p>Choose two distinct source namespaces and an analysis profile.</p></div></div>
    <section className="surface form-panel" aria-label="Workflow configuration">
      {catalog.isPending && <Loading>Loading approved fixtures…</Loading>}
      {catalog.isError && <ErrorNotice error={catalog.error} />}
      {catalog.data && <form onSubmit={submit} className="workflow-form">
        <div className="form-grid">
          {(['left_fixture', 'right_fixture'] as const).map((field, i) => <div key={field}>
            <label htmlFor={field}>{i ? 'Right source' : 'Left source'}</label>
            <select id={field} value={input[field]} onChange={event => set(field, event.target.value)}
              disabled={mutation.isPending} required>
              <option value="">Choose a fixture</option>
              {catalog.data.fixtures.map(f => <option value={f.key} key={f.key}>{f.key} · {f.source}</option>)}
            </select>
          </div>)}
        </div>
        <label htmlFor="profile">Analysis profile</label>
        <select id="profile" value={profile} onChange={event => set('profile', event.target.value)}
          disabled={mutation.isPending} required>
          {catalog.data.profiles.map(p => <option key={p}>{p}</option>)}
        </select>
        {sameSource && <p role="alert" className="callout error">Choose two distinct source namespaces.</p>}
        {mutation.error && <ErrorNotice error={mutation.error} />}
        <button className="primary" disabled={!left || !right || !profile || !!sameSource || mutation.isPending}>
          {mutation.isPending ? 'Registering…' : 'Register workflow'}</button>
      </form>}
    </section>
  </>
}

const wait = (ms: number, signal: AbortSignal) => new Promise<void>(resolve => {
  const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
  const timer = setTimeout(finish, ms)
  signal.addEventListener('abort', finish, { once: true })
})

export function WorkflowDetail() {
  const { workflow } = useOutletContext<WorkflowContext>()
  const { api, isActive } = useSession()
  const cache = useQueryClient()
  const [notice, setNotice] = useState('')
  const [blocked, setBlocked] = useState(false)
  const [checking, setChecking] = useState(false)
  const busy = useRef(false)
  const polling = useRef<AbortController | null>(null)
  const current = workflow.stages.find(s => s.status !== 'completed')
  const completed = workflow.stages.length - workflow.stages.filter(s => s.status !== 'completed').length

  useEffect(() => () => polling.current?.abort(), [])

  function record(data: Workflow) {
    if (!isActive()) return
    const accepted = cache.setQueryData<Workflow>(workflowKey(workflow.id), data)
    void cache.invalidateQueries({ queryKey: ['workflows'] })
    return accepted
  }

  async function check(observed?: Stage, delay = 0) {
    polling.current?.abort()
    const controller = new AbortController()
    polling.current = controller
    setChecking(true)
    try {
      for (let i = 0; i < 4; i++) {
        if (delay || i) await wait(delay || 1000, controller.signal)
        if (controller.signal.aborted) return
        const data = await api.workflow(workflow.id, controller.signal)
        if (controller.signal.aborted) return
        const accepted = record(data)
        if (!accepted) return
        const stage = accepted.stages.find(s => s.name === observed?.name)
        if (observed ? stage?.status === 'completed' || stage?.status === 'failed' ||
            stage?.status === 'interrupted' : accepted.status !== 'running') {
          setNotice('Durable workflow status refreshed.')
          return
        }
      }
      setNotice('Status checked. Review the recorded stage before continuing.')
    } catch (error) {
      if (!controller.signal.aborted) setNotice(error instanceof ApiError ? error.message : 'Status could not be checked.')
    } finally {
      if (!controller.signal.aborted) setChecking(false)
    }
  }

  const advance = useMutation({
    networkMode: 'always', retry: false,
    mutationFn: (stage: Stage) => api.advance(workflow.id, stage),
  })
  async function run() {
    if (!current || busy.current || checking) return
    busy.current = true
    setNotice('')
    try {
      const response = await advance.mutateAsync(current)
      polling.current?.abort()
      setChecking(false)
      await applyConfirmedWorkflow(cache, response, isActive)
      if (isActive()) setNotice('Durable workflow status updated.')
    } catch (error) {
      if (!isActive() || !(error instanceof ApiError) || error.status === 401) return
      if (error.code === 'STAGE_NOT_RETRYABLE') setBlocked(true)
      setNotice(error.message)
      if (error.status === 0 || error.status >= 500 || ['RESPONSE', 'STAGE_STATE_CHANGED', 'WORKFLOW_BUSY'].includes(error.code)) {
        await check(current, error.retryAfterMs)
      }
    } finally { busy.current = false }
  }

  const label = current?.status === 'running' ? 'Check and resume' : current?.status === 'interrupted'
    ? 'Resume stage' : current?.status === 'failed' ? 'Retry stage' : 'Advance one stage'
  return <>
    <section className="execution surface" aria-labelledby="execution-title">
      <div className="surface-heading"><div><h2 id="execution-title">Execution stages</h2>
        <p>{completed} of {workflow.stages.length} stages completed</p></div>
        <div className="actions"><button className="quiet" onClick={() => void check()}
          disabled={checking || advance.isPending}>Check status</button>
          {current && <button className="primary" onClick={() => void run()}
            disabled={advance.isPending || checking || blocked || (current.status === 'failed' &&
              current.failure?.retryable === false)}>
            {advance.isPending ? 'Advancing…' : label}</button>}</div>
      </div>
      <progress aria-label="Workflow progress" value={completed} max={workflow.stages.length} />
      {notice && <p className="callout" role="status">{notice}</p>}
      {checking && <Loading>Checking durable status…</Loading>}
      <ol className="stage-list">{workflow.stages.map((s, i) => <li key={s.name}>
        <span className={`stage-number ${s.status === 'completed' ? 'done' : ''}`} aria-hidden="true">
          {s.status === 'completed' ? '✓' : i + 1}</span>
        <div className="stage-body"><h3>{stageLabel(s)}</h3>
          <p>{s.attempt_count} {s.attempt_count === 1 ? 'attempt' : 'attempts'} · <DateTime value={s.updated_at} /></p>
          {s.failure && <p className="failure-text">{failureGuidance[s.failure.code] ?? 'This stage reported a failure.'}
            {' '}{s.failure.retryable ? 'Check status before retrying.' : 'This attempt is not retryable.'}</p>}
        </div><Status state={s.status} />
      </li>)}</ol>
    </section>
    <p className="footnote">This workflow processes synthetic records. Completed stages remain available for inspection.</p>
  </>
}
