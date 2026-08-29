from __future__ import annotations

from ..schema import Transcript, Translation


def render_text(transcript: Transcript) -> str:
    speakers = {word.speaker for word in transcript.words if word.speaker is not None}
    if not speakers:
        return transcript.text.strip() + "\n"

    lines: list[str] = []
    current_speaker: int | str | None = None
    current_words: list[str] = []
    for word in transcript.words:
        if current_words and word.speaker != current_speaker:
            lines.append(f"Speaker {current_speaker}: {_join_tokens(current_words)}")
            current_words = []
        current_speaker = word.speaker
        current_words.append(word.text)
    if current_words:
        label = current_speaker if current_speaker is not None else "unknown"
        lines.append(f"Speaker {label}: {_join_tokens(current_words)}")
    return "\n".join(lines) + "\n"


def render_translation_text(translation: Translation) -> str:
    if not translation.segments:
        return ""
    if not any(segment.speaker is not None for segment in translation.segments):
        return " ".join(segment.text for segment in translation.segments) + "\n"
    lines = []
    for segment in translation.segments:
        label = segment.speaker if segment.speaker is not None else "unknown"
        lines.append(f"Speaker {label}: {segment.text}")
    return "\n".join(lines) + "\n"


def _join_tokens(tokens: list[str]) -> str:
    result = ""
    for token in tokens:
        if not result or token[:1] in ".,!?;:%)]}…":
            result += token
        else:
            result += " " + token
    return result
