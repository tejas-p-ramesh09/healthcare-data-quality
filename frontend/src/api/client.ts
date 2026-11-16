import type { components } from './schema'

type Schemas = components['schemas']
export type Workflow = Schemas['Summary']
export type Stage = Schemas['Stage']
export type Catalog = Schemas['Catalog']
export type Submission = Schemas['Submission']
export type Finding = Schemas['Finding']
export type Recommendation = Schemas['Recommendation']
export type Batch = Schemas['Batch']
export type SourceFile = Schemas['File']
export type Side = 'left' | 'right'
export type Method = Recommendation['method']
export type Page<T> = { items: T[]; next_cursor: string | null }

const messages: Record<string, string> = {
  UNAUTHORIZED: 'Your session ended. Enter the local API token again.',
  WORKFLOW_NOT_FOUND: 'This workflow could not be found.',
  STAGE_STATE_CHANGED: 'The stage changed since you opened it. Check its current state.',
  WORKFLOW_BUSY: 'Another request is advancing this workflow.',
  EXECUTION_CAPACITY_BUSY: 'The local executor is busy. Check status before trying again.',
  STAGE_NOT_RETRYABLE: 'This stage cannot be retried with its current inputs and profile.',
  STAGE_NOT_READY: 'This report will be available when its stage completes.',
  STAGE_FAILED: 'This stage failed. See workflow details for recovery.',
  STAGE_INTERRUPTED: 'This stage was interrupted. Resume it from workflow details.',
  ARTIFACT_UNAVAILABLE: 'The pinned fixture or profile is unavailable. Check the local API setup.',
  IDEMPOTENCY_KEY_CONFLICT: 'That submission key belongs to another request.',
  INVALID_CURSOR: 'This page is unavailable. Return to the first page.',
  SAME_SOURCE_NOT_SUPPORTED: 'Choose fixtures from different source namespaces.',
  UNKNOWN_FIXTURE: 'That fixture is unavailable. Refresh the catalog.',
  UNKNOWN_PROFILE: 'That profile is unavailable. Refresh the catalog.',
  DATABASE_UNAVAILABLE: 'The local database is unavailable.',
  SERVICE_NOT_READY: 'The local API is unavailable or still starting.',
  NETWORK: 'The local API did not return a response. Check workflow status before retrying.',
  RESPONSE: 'The local API returned an unexpected response.',
}

export class ApiError extends Error {
  constructor(readonly code: string, readonly status: number, readonly requestId?: string,
              readonly retryAfterMs?: number) {
    super(messages[code] ?? 'The local API could not complete the request.')
  }
}

export function retryRead(attempt: number, error: Error) {
  return attempt < 1 && error instanceof ApiError && (error.status === 0 || error.status >= 500)
}

export function stageHeader(stage: Stage) {
  return `${stage.name}:${stage.status}:${stage.attempt_count}`
}

export function makeApi(token: string, onUnauthorized: () => void = () => {}) {
  const lifetime = new AbortController()

  async function request<T>(path: string, signal?: AbortSignal, post?: { headers: Record<string, string>; body?: string }) {
    let result: Response
    const timeout = AbortSignal.timeout(20_000)
    const signals = signal ? [signal, timeout, lifetime.signal] : [timeout, lifetime.signal]
    try {
      result = await fetch(path, {
        method: post ? 'POST' : 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error',
        signal: AbortSignal.any(signals),
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/json', ...post?.headers },
        body: post?.body,
      })
    } catch {
      if (signal?.aborted) throw signal.reason
      if (lifetime.signal.aborted) throw lifetime.signal.reason
      throw new ApiError('NETWORK', 0)
    }
    if (result.status === 401) {
      onUnauthorized()
      throw new ApiError('UNAUTHORIZED', 401)
    }
    const isJson = result.headers.get('content-type')?.split(';')[0] === 'application/json'
    const body = isJson ? await result.json().catch(() => undefined) : undefined
    if (!result.ok || body == null) {
      const id = result.headers.get('X-Request-ID')
      const safeId = id && /^[a-f0-9]{32}$/.test(id) ? id : undefined
      const retry = result.headers.get('Retry-After')
      const code = body?.error?.code
      const knownCode = typeof code === 'string' && Object.hasOwn(messages, code) ? code : 'RESPONSE'
      throw new ApiError(knownCode, result.status, safeId, retry && /^\d{1,2}$/.test(retry) ? Number(retry) * 1000 : undefined)
    }
    return body as T
  }

  function page(cursor: string | null, extra: Record<string, string> = {}) {
    const query = new URLSearchParams({ limit: '25', ...extra })
    if (cursor) query.set('cursor', cursor)
    return query
  }

  return {
    close: () => lifetime.abort(),
    catalog: (signal?: AbortSignal) => request<Catalog>('/v1/catalog', signal),
    workflows: (cursor: string | null, signal?: AbortSignal) =>
      request<Page<Workflow>>(`/v1/workflows?${page(cursor)}`, signal),
    workflow: (id: number, signal?: AbortSignal) => request<Workflow>(`/v1/workflows/${id}`, signal),
    submit: (payload: Submission, key: string) => request<Workflow>('/v1/workflows', undefined, {
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key }, body: JSON.stringify(payload),
    }),
    advance: (id: number, expected: Stage) => request<Workflow>(`/v1/workflows/${id}/advance`, undefined, {
      headers: { 'X-Expected-Stage': stageHeader(expected) },
    }),
    findings: (id: number, side: Side, cursor: string | null, signal?: AbortSignal) =>
      request<Page<Finding>>(`/v1/workflows/${id}/quality-findings?${page(cursor, { side })}`, signal),
    recommendations: (id: number, method: Method, cursor: string | null, signal?: AbortSignal) =>
      request<Page<Recommendation>>(`/v1/workflows/${id}/recommendations?${page(cursor, { method })}`, signal),
    batch: (id: number, side: Side, signal?: AbortSignal) => request<Batch>(`/v1/workflows/${id}/batches/${side}`, signal),
    files: (id: number, side: Side, cursor: string | null, signal?: AbortSignal) =>
      request<Page<SourceFile>>(`/v1/workflows/${id}/batches/${side}/files?${page(cursor)}`, signal),
  }
}
export type Api = ReturnType<typeof makeApi>
