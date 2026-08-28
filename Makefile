# Vaapsi — install, run, test
PYTHON = .venv/Scripts/python.exe
PIP    = .venv/Scripts/python.exe -m pip

.PHONY: install run test verify-chain eval clean tunnel

install:
	-python -m venv .venv
	-$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest tests/ -v

verify-chain:
	$(PYTHON) -m app.audit.verify_chain

# Offline evaluation: rebuild results/evaluation.json (never touches the
# live data store), then verify the committed numbers against README.md.
eval:
	$(PYTHON) scripts/run_evaluation.py
	$(PYTHON) scripts/verify_numbers.py

clean:
	rm -rf .pytest_cache .ruff_cache __pycache__ app/__pycache__

tunnel:
	cloudflared tunnel --url http://localhost:8000
