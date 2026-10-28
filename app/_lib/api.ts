// Shared client helpers for the stakeholder board. Both surfaces (the board
// and the store detail page) go through the same-origin /api proxy; the
// helpers here are small enough to stay hand-rolled — the contract is
// "render what the API serves", never recompute.

/** All reads/writes go same-origin through the catch-all proxy. */
export function api(path: string): string {
  return `/api${path}`
}

/** Em-dash placeholder for anything the API did not serve. */
export function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

// Machine action tokens are reserved out of stakeholder surfaces: the API
// serves `*_label` fields for the board, but raw evidence (`/why` itself) has
// no label field, so the client maps the token to the same phrasing the
// backend's action_label() uses. Keyed summary mirrors
// presentation/board.py:ACTION_LABELS — keep the two in sync.
const ACTION_LABELS: Record<string, string> = {
  CONTINUE: 'Continue as planned',
  MONITOR: 'Monitor only',
  EXTEND_INTERVENTION: 'Extend the intervention',
  PAUSE_INTERVENTION: 'Pause the intervention',
  ESCALATE: 'Escalate to a senior reviewer',
  NEEDS_REVIEW: 'Review the data gap',
  RETARGET_SEGMENT: 'Retarget the segment',
  TIMING_SHIFT: 'Shift the campaign timing',
  REALLOCATE_BUDGET: 'Reallocate budget',
}

/** Stakeholder wording for an action code; degrades to a readable token. */
export function actionLabel(code: unknown): string {
  const text = String(code ?? '').trim()
  if (!text) return '-'
  const known = ACTION_LABELS[text]
  if (known) return known
  const klined = text.replace(/_/g, ' ')
  return klined.charAt(0).toUpperCase() + klined.slice(1)
}
