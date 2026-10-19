PYTHON ?= .venv/bin/python
NODE ?= node

.PHONY: test audit build package

test:
	$(NODE) tests/repair_zotero.test.cjs
	$(NODE) tests/drive_annotations.test.cjs
	$(NODE) tests/drive_engine.test.cjs
	$(NODE) tests/plugin_bootstrap.test.cjs
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

audit:
	$(PYTHON) scripts/check_repository.py

build:
	$(PYTHON) scripts/build_drive_bridge.py

package: audit
	$(PYTHON) scripts/check_repository.py --package
