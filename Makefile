.PHONY: setup run seed test clean admin

PYTHON := python3
VENV := venv
BIN := $(VENV)/bin

# Detect Windows OS environment
ifeq ($(OS),Windows_NT)
	BIN := $(VENV)/Scripts
	PYTHON := python
endif

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements.txt

run:
	$(BIN)/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

seed:
	$(BIN)/python -m scripts.seed_data

admin:
	$(BIN)/python -m scripts.create_admin

test:
	$(BIN)/pytest -o pythonpath=. tests/ -v

clean:
	$(PYTHON) -c "import shutil, os, glob; [shutil.rmtree(p, ignore_errors=True) for p in ['__pycache__', '.pytest_cache', 'chroma_data']]; [os.remove(f) for f in glob.glob('*.db*') if os.path.exists(f)]"
