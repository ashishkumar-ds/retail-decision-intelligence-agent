// Payload types shared by the board (/) and the store detail page
// (/store/[id]), matching docs/FRONTEND_CONTRACT.md. Every API field is
// optional: the UI renders em-dashes for missing values, it never crashes on
// one and never computes replacements client-side.

/** Machine token served as `forecast_status` (see the contract's chip rules). */
export type ForecastStatus = 'AVAILABLE' | 'NO_DATA' | 'ERROR' | string

// Shared record shape: the /recommendations record and the board's bucket
// entry carry the same decision/evidence fields; the board's entry adds its
// own bucket words and the human-gate fields for gated rows.
export interface Recommendation {
  recommendation?: string
  recommendation_id?: string
  confidence?: number | string | null
  reason?: string | null
  store_health_score?: number | string | null
  recovery_pct?: number | string | null
  recovery_direction?: string | null
  recovery_velocity?: string | null
  days_elapsed?: number | null
  days_remaining?: number | null
  requires_human_approval?: boolean
  forecast_status?: ForecastStatus | null
  forecast_signal_available?: boolean | null
  generated_at?: string | null
  baseline_forecast?: number | string | null
  current_forecast?: number | string | null
  uplift_pct?: number | string | null
  did_uplift_pct?: number | string | null
  causal_state?: string | null
  evidence_state?: string | null
  outcome_evidence?: Record<string, unknown> | null
  retail_context?: Record<string, unknown> | null
  trajectory?: Record<string, unknown> | null

  // Gated rows only (`requires_human_approval === true`). The API serves
  // `*_label` fields pre-labelled; render those, never the machine tokens.
  default_action?: string
  default_action_label?: string
  fallback_action?: string
  fallback_action_label?: string
  risk?: string | null
  commitment_label?: string | null
  cost_of_inaction?: string | null
  attention_rank?: number | null
  attention_tier?: string | null
  why_not_working?: string | null
}

export interface BoardRow extends Recommendation {
  store_id: number | string
  // The row's own bucket word. Print it even when a group heading collects
  // several buckets, or the heading overstates the rows inside it.
  state?: string
  bucket?: string
  decision_status?: string
  decision_status_label?: string | null
  directional_note?: string
}

export interface Board {
  total_stores?: number
  needs_decision?: number
  // `as_of` is the newest recommendation's evaluation time; `as_of_display` is
  // served display-ready and must never be re-formatted in the browser.
  as_of?: string
  as_of_display?: string
  counts?: Record<string, number>
  buckets?: Record<string, BoardRow[]>
  campaign?: { label?: string; timing_window?: string }
}

// Hypervisor's `GET /why/{store_id}` payload. `citations` and `methodology`
// are operator provenance (the contract keeps them off stakeholder surfaces);
// type them anyway so the client code says what the endpoint serves.
export interface WhyPayload {
  store_id?: number | string
  narrative?: string | null
  citations?: Array<{ type?: string; id?: string; detail?: string }>
  methodology?: Array<{ chunk_id?: string; title?: string; part?: string; score?: number }>
  evidence?: {
    latest_recommendation?: Recommendation | null
    [key: string]: unknown
  }
  guard?: Partial<Record<string, string>>
}
