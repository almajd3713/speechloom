# Changelog

All notable changes to Speechloom are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `GET /v1/capabilities` reports installed features and supported languages.
- Transcript edits, saved beside the canonical transcript as `*.edited.*` files:
  `PUT`, `PATCH` and `DELETE` on `/v1/jobs/{job_id}/edits`, and
  `TranscriptionService.save_edits` and `clear_edits`.
- `GET /v1/jobs/{job_id}/media` streams stored media for playback, with range requests.
- `GET /v1/storage` reports disk use per job and each job's share of the total.
- `speechloom gui` opens a local web interface at `/ui/` for transcribing, following
  progress, correcting transcripts against the media, downloading results, managing disk
  use and running setup and diagnostics. It ships in the wheel with no new dependency.
- Progress events carry `media_seconds` and, once an earlier job has been timed,
  `estimated_seconds` on the transcription start event (`StageEvent` gains both fields).
- `GET /v1/capabilities` reports `features.transcription` and `max_upload_bytes`
  (`ServiceCapabilities` gains `transcription`).
- `DELETE /v1/jobs/{job_id}/files` deletes a finished job with its stored files.
- `GET /v1/doctor` and `GET`, `POST` and `DELETE /v1/setup`, with `GET /v1/setup/events`,
  run diagnostics and background setup on loopback servers.
- `TranscriptionService.replace_settings` switches the settings used by later jobs.

### Changed

- Artifact names in multi-file jobs are numbered by the file's position, even when an
  earlier file has no readable manifest.
- `POST /v1/jobs` answers `409 setup_running` while a setup run is active.
- The README is shorter and plainer, and its repository links point at the right project.

## [0.1.2] - 2026-10-30

### Fixed

- Plain transcription failed after `speechloom setup --features translation`.
  Translation now runs only when both `--source-language` and `--translate-to` are
  given.
- Transcribing a long recording as a single file failed, because the engine's output was
  cut off at 64 KiB.
- `speechloom setup` installed the slow CPU engine on NVIDIA machines without the CUDA
  toolkit. It now installs the prebuilt CUDA engine whenever `nvidia-smi` works.
- `speechloom setup --features diarization` compiled the engine from source instead of
  using the prebuilt engine.
- Running `speechloom setup` again without `--features` dropped installed features.
- The configuration written by setup pinned the engine, model and device, so later setup
  runs had no effect. Setup now leaves them to its state file and removes the lines that
  version 0.1.1 wrote.
- `--no-shared-model` was ignored.
- The local API wrote job outputs relative to the directory it was started from. They
  now go under the Speechloom data directory.
- Uploads sent to the local API piled up with no way to remove them.

### Security

- The local API rejects requests from other websites (a foreign `Origin`) and DNS
  rebinding attempts (a foreign `Host`).
- `speechloom setup` no longer runs scripts or adopts engines found through the current
  working directory. A source checkout can be selected with
  `SPEECHLOOM_REPOSITORY_ROOT`.

### Added

- `has_upload` on API job responses and `DELETE /v1/jobs/{job_id}/upload`. Uploads are
  kept until a client deletes them; uploads from rejected submissions and uploads no job
  refers to are removed automatically.
- Stage progress on interactive terminals during `speechloom transcribe`.
- Reproducible benchmarks: `scripts/benchmark.py` and `docs/benchmarks.md`.
- Python and HTTP API guides, a contributing guide and a security policy.

### Changed

- Upgrading Speechloom no longer invalidates finished jobs. Jobs created by 0.1.1 are
  recomputed once.
- File fingerprints are cached between runs, so large models are no longer re-hashed by
  every command.
- `speechloom transcribe` before setup now says to run `speechloom setup`.
- The local API accepts at most 8 `workers` per job.
- Lint (ruff) and type checks (mypy) run in CI.

## [0.1.1] - 2026-08-30

### Added

- First release on PyPI: managed setup with prebuilt CPU and CUDA engines, resumable
  transcription jobs, TXT, SRT, WebVTT and JSON output, optional translation and speaker
  diarization, a typed Python API and an optional local HTTP API.

## [0.1.0] - 2026-08-30

- Prepared but never published to PyPI.

[Unreleased]: https://github.com/almajd3713/speechloom/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/almajd3713/speechloom/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/almajd3713/speechloom/releases/tag/v0.1.1
