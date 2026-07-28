# bagel

Bagel is a message-first investment intelligence agent. The public page only opens the conversation; portfolio
connection and the rest of onboarding continue from the text thread.

## What is implemented

- React landing page and secure Wealthsimple connection handoff
- FastAPI API backed by PostgreSQL/Neon
- encrypted `ws-api` sessions, portfolio snapshots, holdings, accounts, and transaction sync
- signed, replay-safe messages.dev webhook handling and outbound iMessage delivery
- short conversational onboarding with a distilled internal investor profile
- OpenAI portfolio reasoning with current web research and thesis-aware judgment
- timezone-aware morning briefs and earnings-calendar monitoring with APScheduler
- admin endpoints for manual syncs, briefs, and thesis updates

The agent treats portfolio facts and current evidence as stronger than a user’s stated market view. Onboarding
context is used quietly and is not repeated unless it materially changes the conclusion.

## Local setup

Frontend:

```bash
npm install
npm run dev
```

Backend:

```bash
cd backend
cp .env.example .env
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Set `VITE_API_URL` if the API is not at `http://localhost:8000`. The frontend runs at
`http://localhost:5173` by default.

## Required production configuration

- `DATABASE_URL`: Neon’s pooled PostgreSQL URL using the `postgresql+asyncpg` driver
- `ENCRYPTION_KEY`: a Fernet key used for Wealthsimple session encryption
- `OPENAI_API_KEY`
- `MESSAGES_API_KEY`, `MESSAGES_LINE_HANDLE`, and `MESSAGES_WEBHOOK_SECRET`
- `ADMIN_API_KEY`
- `APP_BASE_URL`: the public frontend origin used in secure connection links
- `FMP_API_KEY`: optional, for deterministic earnings dates and quotes

Configure messages.dev to send inbound webhooks to `POST /webhooks/messages`. Production startup validates the
required secrets. Wealthsimple passwords are used only during authentication and are never written to the
database; the resulting session is encrypted at rest.

## Checks

```bash
cd backend
uv run ruff check app migrations tests
uv run pytest
cd ..
npm run build
```

Health endpoints are available at `/health/live` and `/health/ready`. Admin routes require the `X-Admin-Key`
header.
