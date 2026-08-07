---
name: api-contract
description: Generate/update the API contract for the Frontend team — FastAPI endpoints, pydantic schemas, WebSocket message formats, error codes. Use when frontend asks "what does the API return" or after backend API changes.
---

# API contract for the Frontend team

Reply in the same language as the user's request. The contract document itself
is always in English.

## Steps

1. Find all FastAPI routes (`APIRouter`, `@app.get/post/...`) and WebSocket
   endpoints in `src/`, plus their pydantic request/response models.
2. Produce/update `docs/api-contract.md` with, per endpoint:
   - method + path, auth requirements
   - request schema (field, type, required, constraints)
   - response schema per status code, including error shape
   - a realistic JSON example
3. WebSocket endpoints (WebLogs): connection URL, auth, every message type
   with a JSON example, close codes.
4. Diff against the previous version of the file if it exists and list
   **breaking changes** at the top of the document.
5. Verify against the code, not memory — every field in the doc must exist in
   a pydantic model or explicit response.
