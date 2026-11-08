PYTHON ?= $(shell if [ -x .venv/bin/python ]; then printf ".venv/bin/python"; else printf "python"; fi)

.PHONY: run install test lint typecheck clean eval-smoke hooks docs-check

run:
	$(PYTHON) -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

install:
	$(PYTHON) -m pip install --no-cache-dir -r requirements.txt

test:
	APP_ENV=test LLM_DISABLED=true $(PYTHON) -m pytest tests/ -v

lint:
	$(PYTHON) -m ruff check src/ tests/ evals/ scripts/

typecheck:
	$(PYTHON) -m mypy src/

eval-smoke:
	$(PYTHON) -m evals.run evals/datasets/smoke.jsonl --split all --mode rules

hooks:
	git config core.hooksPath scripts/hooks
	@echo "Đã bật kiểm tra tài liệu trước commit (scripts/hooks/pre-commit)."

docs-check:
	$(PYTHON) scripts/check_docs.py --base main

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
