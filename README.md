
# Resize Bot — final build (layouts + overlays + RTL)

- 6 layouts, 4 sizes (config-driven)
- Overlays (Overlay/<size>.png), toggle via `apply_overlay`
- RTL Arabic/Hebrew (arabic-reshaper + python-bidi + Raqm if available)
- Max-lines + ellipsis, per-layout style overrides
- FastAPI: GET /health, GET /layouts, POST /render

Run:
  pip install -r requirements.txt
  uvicorn app:app --reload
