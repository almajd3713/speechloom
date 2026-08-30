PYTHON ?= python3
VERSION_SCRIPT := scripts/version.py

.PHONY: version check-version set-version

version:
	@$(PYTHON) $(VERSION_SCRIPT) show

check-version:
	@$(PYTHON) $(VERSION_SCRIPT) check

set-version:
	@if [ -z "$(VERSION)" ]; then \
		echo "VERSION is required, for example: make set-version VERSION=0.1.1" >&2; \
		exit 2; \
	fi
	@$(PYTHON) $(VERSION_SCRIPT) set "$(VERSION)"
