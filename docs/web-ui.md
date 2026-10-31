# Web interface

`speechloom gui` opens Speechloom in your browser, for transcribing without a terminal.
It needs the `api` extra:

```bash
pipx install "speechloom[api]"
speechloom gui
```

The command starts the local server on `127.0.0.1:8765`, waits until it answers, and opens
`http://127.0.0.1:8765/ui/`. Everything stays on your computer.

| Option | Meaning |
|---|---|
| `--port N` | Use another port (default 8765). |
| `--allow-root DIR` | Let the interface use local paths below `DIR` (repeatable). |
| `--max-upload-mb MB` | Largest upload (default 2048). |
| `--no-browser` | Don't open a browser; the address is printed instead. |

Press Ctrl-C in the terminal to stop it. The interface works in current Chromium, Firefox
and Safari, and follows your system's light or dark theme.

## New job

Drop audio or video files on the page, or choose them with the file picker. The same file
added twice is listed once. If the files together are larger than the server's upload
limit, nothing is sent and the message tells you both sizes. On a server started with
`--allow-root`, you can enter local paths instead, which are read in place and not
uploaded.

Pick the output formats (all four by default), and optionally speaker labels or a
translation. Speaker labels and translation are greyed out until you install those
features in Setup. The spoken language and the translation language must differ. Under
**Advanced** are the remaining job options: audio stream, workers, resume, redoing
finished work, keeping the converted audio, and stopping at the first failure.

While the engine or models aren't installed, a banner links to Setup and the Transcribe
button stays disabled.

## Jobs and progress

**Jobs** lists jobs newest first, with a state filter and paging. The list refreshes
every 2 seconds while a job is waiting or running, and every 10 seconds otherwise.

A job's page shows each file's steps. While a file is being transcribed there is also an
estimated progress bar. It is only an estimate, based on how fast earlier jobs ran: the
first job on a fresh install shows elapsed time instead, and a bar that passes its
estimate waits at 99 % and says so. Files transcribed together share one bar.

You can cancel an active job. For a finished job, every output is listed for download (one
tab per file in a multi-file job), and **Delete job** removes it with its stored upload and
results, after asking you to confirm.

## Correcting a transcript

Finished jobs show the transcript beside the stored audio or video. Click a line to jump
the player to it; the current line is highlighted while it plays. If the media isn't
stored, or your browser can't play it, a message replaces the player and the rest still
works.

Click into a line to change its text. Changes are saved automatically about a second after
you stop typing, and the status next to the search box always says whether everything is
saved. If a save fails, your edits stay on the page and **Retry** appears. A line can't be
empty or longer than 1000 characters; those are flagged and not saved. **Revert** restores
one line, and **Revert all** restores every line. Leaving the page saves pending edits
first, and closing the tab with unsaved edits asks you to confirm.

Timings, speaker labels and translations aren't editable. The edited text goes into
separate files (`edited.srt`, `edited.vtt`, `edited.txt`, `edited.json`) listed under
Downloads; the original transcript is never changed. Edits made earlier apply only if they
were made against the current transcript. If the job has a translation with the same
number of lines, each line shows its translation underneath. Use the find box to narrow the
list to matching lines.

## Storage

**Storage** shows how much disk Speechloom uses and each job's share. For any finished job
you can delete just the uploaded media, keeping the transcript, or the whole job.

## Setup and diagnostics

**Setup** shows what is installed. Choose a backend (automatic is right for most people)
and the features you want, then **Install or update**. Features add up, so installing one
keeps the others. The run streams a live log, can be cancelled, and can be followed from
another tab. Setup refuses to start while jobs are running, and jobs are refused while
setup runs. When a run ends the server reloads its settings, so new features work without
restarting. Downloads are large; the engine and speech model are about 1.5 GB, and
translation adds 4.4 GB.

If you configured the engine and models by hand, setup may report "not installed" while
transcription already works; the page says so instead of contradicting the banner.

**Run diagnostics** checks the engine, models, tools and disk space, and lists each check
with its status.

## When something goes wrong

**No browser opens** (common on a headless machine or WSL without a browser handler). The
command prints `Open http://127.0.0.1:8765/ui/ in your browser.`; open it yourself. In
WSL2, Windows browsers can normally reach that address.

**Port already in use.** `speechloom gui` stops with exit code 2 before starting anything.
Choose another port with `--port`.

**Upload over the limit.** Restart with a larger `--max-upload-mb`, or use local paths
with `--allow-root`.

**The page says the connection was lost.** The server stopped. Start `speechloom gui`
again; the page reconnects by itself within 10 seconds.

**`/ui/` is missing on a server you started with `speechloom serve`.** The interface, setup
and diagnostics exist only on loopback servers. Bind to `127.0.0.1` (the default) or use
the [HTTP API](http-api.md) directly.
