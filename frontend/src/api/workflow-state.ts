import { replaceEqualDeep } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import type { Stage, Workflow } from './client'

export const workflowKey = (id: number) => ['workflow', id] as const

function regresses(previous: Stage, incoming: Stage) {
  if (incoming.name !== previous.name || incoming.attempt_count < previous.attempt_count) return true
  if (previous.status === 'completed') return incoming.status !== 'completed'
  if (incoming.attempt_count > previous.attempt_count || incoming.status === previous.status) return false
  // For a single attempt, running precedes its one terminal outcome. A retry
  // creates a new attempt. The database commits the stage and its journal together.
  if (previous.status === 'pending') return false
  return previous.status !== 'running' || incoming.status === 'pending'
}

export function retainWorkflow(previous: Workflow | undefined, incoming: Workflow) {
  if (previous && (incoming.stages.length !== previous.stages.length ||
      incoming.stages.some((stage, i) => regresses(previous.stages[i], stage)))) return previous
  return replaceEqualDeep(previous, incoming)
}

export async function applyConfirmedWorkflow(cache: QueryClient, data: Workflow, isActive: () => boolean) {
  if (!isActive()) return
  await cache.cancelQueries({ queryKey: workflowKey(data.id), exact: true })
  if (!isActive()) return
  cache.setQueryData(workflowKey(data.id), data)
  void cache.invalidateQueries({ queryKey: ['workflows'] })
}
