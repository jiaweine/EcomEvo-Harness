.PHONY: run test smoke
run:
	uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000

test:
	pytest -q

smoke:
	python scripts/e2e_smoke.py
