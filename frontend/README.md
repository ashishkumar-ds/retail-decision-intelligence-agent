# Frontend (stakeholder board)

The read-only stakeholder board is the Next.js app in [`app/`](../app/), deployed on Vercel:

- **Live:** https://retail-decision-intelligence-agent.vercel.app/
- **Backend it reads:** https://retail-decision-intelligence-agent.onrender.com
  (`GET /board`, `/recommendations`, `/why/{store_id}`, `/pending-approvals`)

This directory holds no app code — the board lives in `app/` next to the
FastAPI backend so the two can't drift. The shared contract is:

- API spec + polling/retry rules: [`docs/FRONTEND_CONTRACT.md`](../docs/FRONTEND_CONTRACT.md)
- Same-origin bypass (avoids CORS entirely): the Vercel project rewrites
  `/api/:path*` → `<render-url>/:path*`, then the board fetches
  same-origin (`/api/board`, …).

## Why no `backend/` split

The backend stays at the repo root on purpose: every module import
(`app.*`, `decision_engine.*`, …), the `Dockerfile`, `render.yaml`, and
`docker-compose.yml` resolve from root. Moving it under `backend/` would churn
all imports, CI, and deploys for zero runtime benefit.

## Repo map for reviewers

- Backend (this repo root): FastAPI agent — deterministic decision engine,
  DiD-causal priors, human-gated writes, RAG explanations, evals.
- Frontend (`app/` in this repo, on Vercel): read-only Next.js board — renders what the API says,
  computes nothing.
