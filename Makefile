PYTHON ?= python3
VERSION_SCRIPT := scripts/version.py

RUNTIME_VERSION ?= latest
AUTO_MERGE ?= false

.PHONY: version check-version set-version prepare-release

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

prepare-release:
	@if [ -z "$(VERSION)" ]; then \
		echo "VERSION is required, for example: make prepare-release VERSION=0.1.1" >&2; \
		exit 2; \
	fi
	gh workflow run prepare-release.yml \
		--ref main \
		-f package_version="$(VERSION)" \
		-f runtime_version="$(RUNTIME_VERSION)" \
		-f auto_merge="$(AUTO_MERGE)"
