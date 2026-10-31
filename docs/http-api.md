# HTTP API

`speechloom serve` exposes the transcription service as a versioned REST API for local
clients such as a desktop or browser front end. It needs the `api` extra:

```bash
pipx install "speechloom[api]"
speechloom serve --allow-root ~/Videos
```

The server listens on `http://127.0.0.1:8765` and runs one worker process with one GPU
inference slot. Interactive documentation is served at `/docs` and the OpenAPI schema
at `/openapi.json`.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Any non-loopback address needs `--allow-remote` and `SPEECHLOOM_API_TOKEN`. |
| `--port` | `8765` | TCP port. |
| `--allow-root DIR` | none | Allow local-path jobs for files under `DIR`. Repeatable. |
| `--allow-origin URL` | none | Allow browser requests from this origin, such as a front end on another port. Repeatable. |
| `--max-upload-mb` | `2048` | Largest accepted request body. |
| `--queue-size` | `16` | Waiting jobs before new submissions get `429`. |
| `--media-workers` | `2` | Jobs preparing media at the same time. |

Job outputs go to the configured `output_dir`. A relative value, including the default
`transcripts`, is placed under the Speechloom data directory
(`~/.local/share/speechloom/transcripts` on Linux).

## Security

On loopback the API needs no token, but it answers only requests whose `Host` is
`127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>`, and whose `Origin`, when a
browser sends one, is the server itself or an `--allow-origin` value. Other websites
open in your browser therefore cannot submit jobs or read results; they get `403` with
the code `origin_not_allowed` or `host_not_allowed`. Binding to any other address
requires `--allow-remote` and a bearer token in `SPEECHLOOM_API_TOKEN`; clients then
send `Authorization: Bearer <token>`.

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /v1/health` | Liveness: `{"status": "ok"}` |
| `GET /v1/capabilities` | What this server supports |
| `POST /v1/jobs` | Submit a job as a multipart upload or as JSON local paths; answers `202` with a `Location` header |
| `GET /v1/jobs?offset=0&limit=50&state=completed` | List jobs, newest first |
| `GET /v1/jobs/{job_id}` | One job, including `has_upload` |
| `DELETE /v1/jobs/{job_id}` | Cancel the job; repeating it is harmless |
| `DELETE /v1/jobs/{job_id}/upload` | Delete the stored upload of a finished job; answers `204` |
| `DELETE /v1/jobs/{job_id}/files` | Delete a finished job with its stored upload and output directories; answers `204` |
| `GET /v1/jobs/{job_id}/events` | Progress as server-sent events; reconnect with `Last-Event-ID` |
| `GET /v1/jobs/{job_id}/artifacts` | List the job's artifacts |
| `GET /v1/jobs/{job_id}/artifacts/{name}` | Download one artifact |
| `PUT /v1/jobs/{job_id}/edits` | Replace all transcript edits of a finished job |
| `PATCH /v1/jobs/{job_id}/edits` | Merge transcript edits into the existing ones |
| `DELETE /v1/jobs/{job_id}/edits` | Remove all transcript edits; answers `204` |
| `GET /v1/jobs/{job_id}/media?result=1` | Stream the stored upload, local source or kept audio for playback |
| `GET /v1/storage` | Disk use per job and in total, with each job's share |
| `GET /v1/doctor` | Run the diagnostics (loopback only) |
| `GET /v1/setup` | Installation status and the latest setup run (loopback only) |
| `POST /v1/setup` | Start a background setup run; answers `202` (loopback only) |
| `DELETE /v1/setup` | Cancel the running setup (loopback only) |
| `GET /v1/setup/events` | Setup progress as server-sent events (loopback only) |
| `GET /`, `GET /ui/` | The web interface (loopback only); `/` and `/ui` redirect to `/ui/` |

Every error has the same shape:
`{"error": {"code": "...", "message": "...", "details": null, "request_id": "..."}}`.

## Walkthrough

```bash
api=http://127.0.0.1:8765/v1

# Upload a file and ask for SRT and TXT.
curl -s -F files=@interview.mp4 -F 'options={"formats":["srt","txt"]}' "$api/jobs"

# Follow progress; press Ctrl-C to stop.
curl -N "$api/jobs/<job id>/events"

# List and download the results.
curl -s "$api/jobs/<job id>/artifacts"
curl -s -o interview.srt "$api/jobs/<job id>/artifacts/srt"

# Free the space used by the upload once you no longer need it.
curl -s -X DELETE "$api/jobs/<job id>/upload"
```

A local-path job, for a file under an `--allow-root` directory:

```bash
curl -s -H 'Content-Type: application/json' \
  -d '{"inputs": ["/home/me/Videos/interview.mp4"], "formats": ["srt"]}' "$api/jobs"
```

Upload options accept the same fields as local-path jobs except `inputs`, `output_dir`
and `recursive`. `workers` is limited to 8.

## Uploads

Uploaded files are stored under the data directory, in `staging/`, and kept until a
client deletes them with `DELETE /v1/jobs/{job_id}/upload`, so a front end can replay
the media later. Deletion is refused with `409` and the code `job_active` while the job
is queued or running. Uploads from rejected submissions are removed immediately, and at
startup the server removes stored uploads that no known job refers to.

## Capabilities

`GET /v1/capabilities` reports, besides the API version and input modes, which optional
features are installed (`features.translation`, `features.diarization`), the output
`formats`, the 25 `asr_languages` Parakeet recognizes, and the `translation_languages`
(empty when translation is not installed). Use it to decide which options a front end
shows.

`features.transcription` is `true` when the configured speech model file exists and the
engine executable resolves, so jobs can run. `max_upload_bytes` is the largest accepted
upload, as set by `--max-upload-mb`.

## Transcript edits

```bash
curl -s -X PUT -H 'Content-Type: application/json' \
  -d '{"segments": [{"index": 0, "text": "Corrected first line."}]}' \
  "$api/jobs/<job id>/edits"
```

Edits replace the text of transcript segments, keyed by segment index; timings are
unchanged. They are written beside the canonical transcript as `transcript.edited.json`,
`transcript.edited.txt`, `subtitles.edited.srt` and `subtitles.edited.vtt`, listed as
the artifacts `edited.json`, `edited.txt`, `edited.srt` and `edited.vtt`.

- `PUT` replaces all earlier edits with the segments in the request.
- `PATCH` merges the request into the earlier edits; a segment whose `text` is `null`
  goes back to its original text.
- `DELETE` removes every edit and the edited files.

The canonical `transcript.json` is never changed, and rerunning the job with `force`
removes the edited files. For a multi-file job, choose the file with `"result": N` in
the body, or `?result=N` for `DELETE` (1-based).

## Progress events

`GET /v1/jobs/{job_id}/events` sends one server-sent event per progress step. The event
name is the stage and the data holds `job_id`, `source`, `stage`, `status`, `message`,
`completed`, `total`, `timestamp`, `media_seconds` and `estimated_seconds`.

`media_seconds` is the file's duration, present once it is known. When transcription
starts and the server has timed an earlier job, the start event also carries
`estimated_seconds`: the media duration divided by the median speed of the newest five
timed results, rounded to 0.1 s with a floor of 0.1 s. Otherwise it is `null`. Speed is
media seconds per inference second, read from each finished result's manifest; in a
shared batch a file's inference time is the batch time divided by the batch size.
Results recovered at startup count, and failed, cancelled and skipped results do not.

In a job with several files, artifact names start with the file's 1-based position
(`2.srt`), even when an earlier file has no readable manifest.

## Deleting a job

`DELETE /v1/jobs/{job_id}/files` removes a finished job's stored upload and output
directories, then the job itself. A directory is removed only if it holds a
`manifest.json` and no other known job uses it. An active job is refused with `409` and
the code `job_active`; repeating the request after it succeeded gives `404`.
`DELETE /v1/jobs/{job_id}` still only cancels.

## Diagnostics and setup

These endpoints reveal local paths and change what is installed, so they exist only on a
loopback server. On any other host they answer `403` with the code `local_only`.

`GET /v1/doctor` answers `{"ready": true, "checks": [{"name", "status", "message",
"details"}]}`, where `status` is `ok`, `warn` or `error`. While a setup run is active it
answers `409 setup_running`.

`GET /v1/setup` answers `{"status": ..., "task": ...}`. `status` is the installation
status, or `null` while a run is active, because computing it hashes every installed
model. `task` is the latest run, or `null` if none has started.

`POST /v1/setup` starts one run in the background and answers `202` with the run. The
body is optional: `{"backend": "auto", "features": [], "from_source": false,
"keep_cache": false}`. Only one run can be active, and setup and jobs exclude each
other. Errors: `409 setup_running`, `409 jobs_active`, and `422 invalid_setup` for an
unknown backend or feature. While a run is active, `POST /v1/jobs` answers
`409 setup_running`. After every run the server reloads its settings before it reports
that the run ended, so new features work without a restart; if the reload fails, the old
settings stay in use.

A run is `{"id", "state", "request", "started_at", "finished_at", "current_stage",
"message", "error", "ready", "actions"}` where `state` is `running`, `completed`,
`failed` or `cancelled`. `DELETE /v1/setup` cancels the active run and answers `200`
with it; with no run ever started it answers `404 setup_not_started`.

`GET /v1/setup/events` streams the latest run's progress like job events, with the data
`{"task_id", "stage", "status", "message", "timestamp"}`, and ends after the run's final
event (`completed`, `failed` or `cancelled`). Event ids keep increasing across runs, so
`Last-Event-ID` resumes a stream. It answers `404 setup_not_started` before any run and
`422 invalid_event_id` for a bad `Last-Event-ID`.

## Web interface

On a loopback server, `/ui/` serves the [web interface](web-ui.md) as static files. They
are sent with a `Content-Security-Policy` that allows only the server's own origin (plus
`data:` images and `blob:` media), `X-Frame-Options: DENY`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer` and `Cache-Control: no-cache`. On any other
host neither `/ui/` nor `/` exists.

## Playback

`GET /v1/jobs/{job_id}/media` returns the job's original upload, or its local source
file when it lies under an `--allow-root` directory, and supports range requests, so a
player can seek. When neither exists, it returns the 16 kHz audio kept for jobs
submitted with `"keep_audio": true`, and otherwise `404 media_unavailable`.

## Storage

`GET /v1/storage` reports `total_bytes`, `upload_bytes`, `output_bytes` and the
filesystem's `free_bytes`, plus one entry per job with its upload and output sizes and
its `share` of the total (a fraction from 0 to 1). A directory shared by several jobs
is counted once, for the newest job.
