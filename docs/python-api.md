# Python API

Speechloom's command line is a thin layer over a typed Python API. Install the package,
run `speechloom setup` once, then call the same service the CLI uses.

## Transcribe

```python
from pathlib import Path

from speechloom import StageEvent, TranscriptionRequest, TranscriptionService


def show(event: StageEvent) -> None:
    name = event.source.name if event.source else ""
    print(f"[{event.stage}] {name} {event.message}")


service = TranscriptionService.from_default_config()
request = TranscriptionRequest(inputs=(Path("interview.mp4"),), formats=("srt", "txt"))
results = service.transcribe(request, on_event=show)
for result in results:
    if result.error:
        print("failed:", result.error)
    else:
        print(result.state, result.job_dir, "(skipped)" if result.skipped else "")
```

`from_default_config()` reads the same configuration and installation state as the CLI.
`TranscriptionRequest` takes the CLI's options as fields: `inputs`, `output_dir`,
`formats`, `recursive`, `audio_stream`, `diarize`, `source_language`, `translate_to`,
`resume`, `force`, `keep_audio`, `workers` and `fail_fast`. Invalid values raise
`speechloom.errors.ConfigurationError`.

## Progress events

`on_event` receives `StageEvent` objects with `job_id`, `source`, `stage`, `status`,
`message`, `completed`, `total` and `timestamp`. The stages are `queued`, `validating`,
`probing`, `normalizing`, `transcribing`, `translating` and `rendering`, and a job ends
in `completed`, `failed`, `cancelled` or `interrupted`. Most events have the status
`started` or `completed`; a job whose results already exist reports `skipped`.

## Cancellation

```python
import threading

from speechloom import CancellationController

controller = CancellationController()
threading.Timer(30, controller.cancel).start()
results = service.transcribe(request, cancellation=controller)
```

Cancelling stops the running native process and marks unfinished jobs `cancelled`.
Running the same request again resumes from the last committed stage, so a finished
transcription is never repeated.

## Inspect a job

```python
details = service.inspect(results[0].job_dir)
print(details.state, details.source)
for artifact in details.artifacts:
    print(artifact.name, artifact.path, artifact.size)
```

`details.to_dict()` returns a copy of the full manifest.

## Diagnostics

```python
report = service.doctor()
print("ready" if report.ready else "not ready")
for check in report.checks:
    print(check.status, check.name, check.message)
```

## Capabilities

```python
capabilities = service.capabilities()
print(capabilities.translation, capabilities.diarization)
print(capabilities.asr_languages, capabilities.translation_languages)
```

## Edit a transcript

```python
from speechloom import SegmentEdit

details = service.save_edits(results[0].job_dir, [SegmentEdit(0, "Corrected first line.")])
print([artifact.name for artifact in details.artifacts])
```

Edits are saved as `transcript.edited.*` and `subtitles.edited.*` beside the canonical
files, which stay unchanged. By default each call replaces all earlier edits; pass
`merge=True` to add to them instead, where `SegmentEdit(index, None)` restores a
segment's original text. `service.clear_edits(job_dir)` removes every edit.

## Background jobs

`speechloom.JobManager` runs requests on worker threads with a bounded queue and a
single GPU inference slot. The HTTP API is built on it:

```python
from speechloom import JobManager

with JobManager(service, queue_size=16, media_workers=2) as manager:
    job = manager.submit(request)
    finished = manager.wait(job.id, timeout=600)
    print(finished.state, [item.event.message for item in manager.events(job.id)])
```
