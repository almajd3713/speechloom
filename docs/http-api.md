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
| `GET /v1/jobs/{job_id}/events` | Progress as server-sent events; reconnect with `Last-Event-ID` |
| `GET /v1/jobs/{job_id}/artifacts` | List the job's artifacts |
| `GET /v1/jobs/{job_id}/artifacts/{name}` | Download one artifact |

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
