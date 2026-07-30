.PHONY: dev health

LAN_IP ?= $(shell ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)

dev:
	@if [ -z "$(LAN_IP)" ]; then echo "Could not find a local network address"; exit 1; fi
	@echo "Bagel connect URL: http://$(LAN_IP):5173"
	@set -e; \
	(cd backend && APP_BASE_URL=http://$(LAN_IP):5173 exec uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) & api_pid=$$!; \
	(cd messaging && exec npm run dev) & messaging_pid=$$!; \
	(VITE_API_URL=http://$(LAN_IP):8000 exec npm run dev -- --host 0.0.0.0) & web_pid=$$!; \
	trap 'kill $$api_pid $$messaging_pid $$web_pid 2>/dev/null || true' INT TERM EXIT; \
	wait

health:
	@curl -fsS http://127.0.0.1:8000/health/ready && echo
	@curl -fsS http://127.0.0.1:8787/health && echo
	@curl -fsS http://127.0.0.1:5173 >/dev/null && echo '{"frontend":"ok"}'
