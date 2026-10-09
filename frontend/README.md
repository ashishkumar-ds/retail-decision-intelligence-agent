# Frontend (stakeholder board)

The read-only stakeholder board is a **separate Next.js app** deployed on Vercel:

- **Live:** https://retail-decision-intelligence-agent.vercel.app/
- **Backend it reads:** https://retail-decision-intelligence-agent.onrender.com
  (`GET /board`, `/recommendations`, `/why/{store_id}`, `/pending-approvals`)

This directory intentionally holds no app code — it documents the contract so the
two repos can't drift:

- API spec + polling/retry rules: [`docs/FRONTEND_CONTRACT.md`](../docs/FRONTEND_CONTRACT.md)
- Same-origin bypass (avoids CORS entirely): add a `vercel.json` rewrite in the
  frontend repo mapping `/api/:path*` → `<render-url>/:path*`, then fetch
  same-origin (`/api/board`, …).

## Why no `backend/` split

The backend stays at the repo root on purpose: every module import
(`app.*`, `decision_engine.*`, …), the `Dockerfile`, `render.yaml`, and
`docker-compose.yml` resolve from root. Moving it under `backend/` would churn
all imports, CI, and deploys for zero runtime benefit.

## Repo map for reviewers

- Backend (this repo root): FastAPI agent — deterministic decision engine,
  DiD-causal priors, human-gated writes, RAG explanations, evals.
- Frontend (Vercel repo): read-only Next.js board — renders what the API says,
  computes nothing.
