.PHONY: run test lint format typecheck check cutover-gate eval-ragas eval-ragas-offline clean

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/

check: lint format typecheck test

cutover-gate:
	pytest tests/test_api/test_agent_v2_cutover.py -v --tb=short
	pytest tests/ -v --tb=short

# RAGAS eval toàn agent. LIVE = tốn phí API (dùng model canonical trong .env). LIMIT giới hạn số case.
eval-ragas:
	python -m eval.eval_ragas --limit $(or $(LIMIT),12) --with-goal-accuracy

# Smoke KHÔNG gọi mạng — chỉ dựng + dump samples để soi hạ tầng.
eval-ragas-offline:
	python -m eval.eval_ragas --offline --limit $(or $(LIMIT),8)

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
