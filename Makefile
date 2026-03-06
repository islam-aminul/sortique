PYTHON  ?= python3
PARENT  := $(abspath ..)

.PHONY: install test run build clean

## install — install all runtime + dev dependencies
install:
	$(PYTHON) -m pip install -r requirements.txt

## test — run the full test suite
test:
	$(PYTHON) -m pytest tests/ -v

## run — launch Sortique from source (adds parent dir to PYTHONPATH)
run:
	PYTHONPATH=$(PARENT) $(PYTHON) -m sortique

## build — create a single-file executable with PyInstaller
build:
	pyinstaller sortique.spec

## clean — remove build artefacts and bytecode caches
clean:
	rm -rf build/ dist/ *.egg-info .eggs .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
