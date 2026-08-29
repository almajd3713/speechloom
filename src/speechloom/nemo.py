"""NeMo-Speech.cpp command adapter and JSON normalization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterable

from .errors import InferenceError, ModelError, UnsupportedFeatureError
from .process import CommandFailed, CommandResult, run_command
from .schema import Transcript, Word


Runner = Callable[..., CommandResult]


@dataclass(frozen=True)
class NemoOptions:
    executable: str
    model: Path
    device: str = "auto"
    diarize: bool = False
    diar_model: Path | None = None


@dataclass(frozen=True)
class TranslationOptions:
    executable: str
    model: Path
    source_language: str
    target_language: str
    device: str = "auto"


def transcribe(
    audio: Path,
    options: NemoOptions,
    *,
    duration: float | None = None,
    runner: Runner = run_command,
) -> Transcript:
    _validate_options(options)

    argv = [
        options.executable,
        "transcribe",
        str(audio),
        "--model", str(options.model),
        "--format", "json",
        "--word-times",
    ]
    if options.device != "auto":
        argv.extend(["--device", options.device])
    if options.diarize and options.diar_model is not None:
        argv.extend(["--diar-model", str(options.diar_model)])

    try:
        result = runner(argv)
    except CommandFailed as exc:
        raise _map_native_failure(exc) from exc
    payload = _parse_json_output(result.stdout)
    transcript = adapt_payload(payload, duration=duration)
    transcript.provenance.update(
        {
            "engine": "NeMo-Speech.cpp",
            "model_path": str(options.model.resolve()),
            "device": options.device,
            "diarization": options.diarize,
        }
    )
    if options.diar_model is not None and options.diarize:
        transcript.provenance["diar_model_path"] = str(options.diar_model.resolve())
    return transcript


def transcribe_directory(
    input_dir: Path,
    output_dir: Path,
    options: NemoOptions,
    *,
    concurrency: int,
    runner: Runner = run_command,
) -> CommandResult:
    """Run native directory mode once so all inputs share one recognizer."""

    _validate_options(options)
    output_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        options.executable,
        "transcribe",
        str(input_dir),
        "--recursive",
        "--model", str(options.model),
        "--format", "json",
        "--word-times",
        "--output-dir", str(output_dir),
        "--concurrency", str(concurrency),
    ]
    if options.device != "auto":
        argv.extend(["--device", options.device])
    if options.diarize and options.diar_model is not None:
        argv.extend(["--diar-model", str(options.diar_model)])
    return runner(argv, check=False)


def translate_texts(
    texts: list[str],
    options: TranslationOptions,
    *,
    runner: Runner = run_command,
) -> list[str]:
    """Translate a batch in one native invocation so the NMT model loads once."""

    if not options.model.is_file():
        raise ModelError(f"Translation model does not exist: {options.model}")
    normalized = [" ".join(text.split()) for text in texts]
    if not normalized or any(not text for text in normalized):
        raise InferenceError("Translation inputs must contain text")

    with tempfile.TemporaryDirectory(prefix="parakeet-translation-") as temporary:
        input_path = Path(temporary) / "segments.txt"
        input_path.write_text("\n".join(normalized) + "\n", encoding="utf-8")
        argv = [
            options.executable,
            "translate",
            "--model", str(options.model),
            "--from", options.source_language,
            "--to", options.target_language,
            "--input", str(input_path),
            "--json",
        ]
        if options.device != "auto":
            argv.extend(["--device", options.device])
        try:
            result = runner(argv)
        except CommandFailed as exc:
            raise _map_translation_failure(exc.result) from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InferenceError("NeMo produced malformed translation JSON") from exc
    if not isinstance(payload, list) or len(payload) != len(normalized):
        raise InferenceError(
            f"NeMo returned {len(payload) if isinstance(payload, list) else 'invalid'} "
            f"translations for {len(normalized)} inputs"
        )
    translated: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            raise InferenceError(f"NeMo translation result {index} has no text")
        translated.append(" ".join(str(item["text"]).split()))
    return translated


def adapt_payload(payload: Any, *, duration: float | None = None) -> Transcript:
    """Normalize supported NeMo/Riva-shaped JSON into the canonical schema."""

    if not isinstance(payload, dict):
        raise InferenceError("NeMo JSON output must be an object")
    container = _find_result_container(payload)
    raw_words = _find_words(container)
    if raw_words is None and container is not payload:
        raw_words = _find_words(payload)
    if not raw_words:
        raise InferenceError("NeMo JSON did not contain word timestamps")

    words = [_adapt_word(item, index) for index, item in enumerate(raw_words)]
    text = _find_text(container) or _find_text(payload) or _join_words(words)
    language = _first_value(container, ("language", "language_code", "detected_language"))
    if language is None:
        language = _first_value(payload, ("language", "language_code", "detected_language"))
    transcript = Transcript(
        text=str(text).strip(),
        words=words,
        duration=duration,
        language=str(language) if language else None,
        provenance={"adapter_schema": 1},
    )
    transcript.validate()
    return transcript


def map_native_failure(result: CommandResult) -> InferenceError:
    """Map a non-zero native result without requiring CommandFailed."""

    return _map_native_result(result)


def _parse_json_output(stdout: str) -> Any:
    stripped = stdout.strip()
    if not stripped:
        raise InferenceError("NeMo produced no JSON output")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        for line in reversed(stripped.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise InferenceError("NeMo produced malformed JSON output")


def _find_result_container(payload: dict[str, Any]) -> dict[str, Any]:
    direct = payload.get("result")
    if isinstance(direct, dict):
        return _find_result_container(direct)
    results = payload.get("results")
    if isinstance(results, list) and results and isinstance(results[-1], dict):
        return _find_result_container(results[-1])
    alternatives = payload.get("alternatives")
    if isinstance(alternatives, list) and alternatives and isinstance(alternatives[0], dict):
        return alternatives[0]
    return payload


def _find_words(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    for key in ("words", "word_timestamps", "word_times", "tokens"):
        value = payload.get(key)
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    words_info = payload.get("words_info")
    if isinstance(words_info, dict):
        value = words_info.get("words")
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    return None


def _adapt_word(raw: dict[str, Any], index: int) -> Word:
    text = _first_value(raw, ("word", "text", "token"))
    start = _first_value(raw, ("start", "start_time", "start_sec", "begin"))
    end = _first_value(raw, ("end", "end_time", "end_sec", "finish"))
    if text is None or start is None or end is None:
        raise InferenceError(f"Word {index} lacks text or timestamps")
    confidence = _first_value(raw, ("confidence", "probability", "score"))
    speaker = _first_value(raw, ("speaker", "speaker_tag", "speaker_id"))
    language = _first_value(raw, ("language", "language_code"))
    try:
        return Word(
            text=str(text).strip(),
            start=float(start),
            end=float(end),
            confidence=float(confidence) if confidence is not None else None,
            speaker=speaker,
            language=str(language) if language else None,
        )
    except (TypeError, ValueError) as exc:
        raise InferenceError(f"Word {index} contains invalid values") from exc


def _find_text(payload: dict[str, Any]) -> str | None:
    value = _first_value(payload, ("text", "transcript", "transcription"))
    return str(value) if value is not None else None


def _first_value(payload: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _join_words(words: list[Word]) -> str:
    text = ""
    for word in words:
        token = word.text
        if not text or token[:1] in ".,!?;:%)]}…":
            text += token
        else:
            text += " " + token
    return text


def _map_native_failure(exc: CommandFailed) -> InferenceError:
    return _map_native_result(exc.result)


def _map_native_result(result: CommandResult) -> InferenceError:
    code = result.returncode
    diagnostic = result.stderr.strip() or result.stdout.strip() or "unknown inference failure"
    if code == 3:
        return ModelError(f"NeMo model is missing: {diagnostic}")
    if code == 4:
        return UnsupportedFeatureError(f"NeMo feature is unsupported: {diagnostic}")
    return InferenceError(f"NeMo transcription failed: {diagnostic}", native_exit_code=code)


def _map_translation_failure(result: CommandResult) -> InferenceError:
    code = result.returncode
    diagnostic = result.stderr.strip() or result.stdout.strip() or "unknown translation failure"
    if code == 3:
        return ModelError(f"NeMo translation model is missing: {diagnostic}")
    if code == 4:
        return UnsupportedFeatureError(f"NeMo translation is unsupported: {diagnostic}")
    return InferenceError(f"NeMo translation failed: {diagnostic}", native_exit_code=code)


def _validate_options(options: NemoOptions) -> None:
    if not options.model.is_file():
        raise ModelError(f"ASR model does not exist: {options.model}")
    if options.diarize and (options.diar_model is None or not options.diar_model.is_file()):
        raise ModelError("Diarization requires an existing Sortformer model")
