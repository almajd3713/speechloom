"""Deterministic subtitle segmentation and formatting."""

from __future__ import annotations

import math
import re
import textwrap

from ..schema import Segment, Transcript, Word


TERMINAL_PUNCTUATION = re.compile(r"[.!?…][\"'”’)]*$")


def build_segments(
    transcript: Transcript,
    *,
    max_line_chars: int = 42,
    max_lines: int = 2,
    max_duration: float = 7.0,
    silence_gap: float = 0.8,
) -> list[Segment]:
    words = transcript.words
    if not words:
        return []
    max_chars = max_line_chars * max_lines
    groups: list[tuple[int, int]] = []
    group_start = 0
    current_chars = 0

    for index, word in enumerate(words):
        token_cost = len(word.text) + (1 if current_chars else 0)
        current_chars += token_cost
        next_word = words[index + 1] if index + 1 < len(words) else None
        duration = word.end - words[group_start].start
        should_break = next_word is None
        if next_word is not None:
            projected = current_chars + 1 + len(next_word.text)
            should_break = (
                next_word.speaker != word.speaker
                or next_word.start - word.end >= silence_gap
                or duration >= max_duration
                or projected > max_chars
                or bool(TERMINAL_PUNCTUATION.search(word.text))
            )
        if should_break:
            groups.append((group_start, index + 1))
            group_start = index + 1
            current_chars = 0

    segments: list[Segment] = []
    previous_end = 0.0
    for group_index, (start_index, end_index) in enumerate(groups):
        group = words[start_index:end_index]
        start = max(group[0].start, previous_end)
        natural_end = group[-1].end
        desired_end = max(natural_end, start + 0.7, start + len(_join_words(group)) / 20.0)
        next_start = words[groups[group_index + 1][0]].start if group_index + 1 < len(groups) else None
        cap = transcript.duration
        if next_start is not None:
            cap = next_start - 0.001 if cap is None else min(cap, next_start - 0.001)
        end = desired_end if cap is None else min(desired_end, cap)
        end = max(end, start + 0.001)
        segments.append(
            Segment(
                text=_wrap_subtitle(_join_words(group), max_line_chars),
                start=start,
                end=end,
                speaker=group[0].speaker,
                word_start=start_index,
                word_end=end_index,
            )
        )
        previous_end = end
    _validate_segments(segments, transcript.duration)
    return segments


def render_srt(segments: list[Segment]) -> str:
    blocks = [
        f"{index}\n{_format_timestamp(segment.start, srt=True)} --> {_format_timestamp(segment.end, srt=True)}\n"
        f"{_speaker_prefix(segment)}{segment.text}"
        for index, segment in enumerate(segments, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(segments: list[Segment]) -> str:
    blocks = [
        f"{_format_timestamp(segment.start, srt=False)} --> {_format_timestamp(segment.end, srt=False)}\n"
        f"{_speaker_prefix(segment)}{segment.text}"
        for segment in segments
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


def _join_words(words: list[Word]) -> str:
    result = ""
    for word in words:
        token = word.text
        if not result or token[:1] in ".,!?;:%)]}…":
            result += token
        else:
            result += " " + token
    return result.strip()


def _wrap_subtitle(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if len(lines) <= 2:
        return "\n".join(lines)
    midpoint = len(text) // 2
    split_positions = [match.start() for match in re.finditer(r"\s+", text)]
    if not split_positions:
        return text
    split = min(split_positions, key=lambda position: abs(position - midpoint))
    return text[:split].strip() + "\n" + text[split:].strip()


def _format_timestamp(seconds: float, *, srt: bool) -> str:
    millis = max(0, int(math.floor(seconds * 1000 + 0.5)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1_000)
    separator = "," if srt else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


def _speaker_prefix(segment: Segment) -> str:
    if segment.speaker is None:
        return ""
    return f"[Speaker {segment.speaker}] "


def _validate_segments(segments: list[Segment], duration: float | None) -> None:
    previous_end = -1.0
    for index, segment in enumerate(segments):
        if segment.start < 0 or segment.end <= segment.start:
            raise ValueError(f"Subtitle segment {index} has invalid timestamps")
        if segment.start < previous_end - 0.001:
            raise ValueError(f"Subtitle segment {index} overlaps the previous segment")
        if duration is not None and segment.end > duration + 0.001:
            raise ValueError(f"Subtitle segment {index} exceeds media duration")
        previous_end = segment.end
