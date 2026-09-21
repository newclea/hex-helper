"""Measured rich-text wrapping shared by all GameBuddy bubble copy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


LINE_END_PUNCTUATION = frozenset("，。！？；：、）》】〕〉」』”’…％,.;:!?)]}>%~～")


@dataclass(frozen=True)
class TextRun:
    text: str
    bold: bool = False


@dataclass(frozen=True)
class LaidOutLine:
    runs: tuple[TextRun, ...]
    width: int

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


def normalize_blocks(
    blocks: object,
    fallback: str,
) -> tuple[tuple[TextRun, ...], ...]:
    """Validate structured blocks or return plain fallback paragraphs."""

    paragraphs: list[tuple[TextRun, ...]] = []
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, Mapping):
                return _plain_paragraphs(fallback)
            if isinstance(block.get("label"), str) and isinstance(block.get("value"), str):
                paragraphs.append(
                    (TextRun(str(block["label"]) + " "), TextRun(str(block["value"]), True))
                )
            elif isinstance(block.get("text"), str):
                paragraphs.extend(_plain_paragraphs(str(block["text"])))
            else:
                return _plain_paragraphs(fallback)
        if paragraphs:
            return tuple(paragraphs)
    return _plain_paragraphs(fallback)


def _plain_paragraphs(text: str) -> tuple[tuple[TextRun, ...], ...]:
    return tuple((TextRun(paragraph),) for paragraph in str(text).split("\n"))


def _join_chars(chars: Sequence[tuple[str, bool]], measure: Callable[[str, bool], int]) -> LaidOutLine:
    runs: list[TextRun] = []
    for character, bold in chars:
        if runs and runs[-1].bold == bold:
            runs[-1] = TextRun(runs[-1].text + character, bold)
        else:
            runs.append(TextRun(character, bold))
    width = sum(measure(run.text, run.bold) for run in runs)
    return LaidOutLine(tuple(runs), width)


def _fits(
    chars: Sequence[tuple[str, bool]],
    max_width: int,
    measure: Callable[[str, bool], int],
) -> bool:
    return _join_chars(chars, measure).width <= max_width


def _rebalance_orphan(
    lines: list[list[tuple[str, bool]]],
    max_width: int,
    measure: Callable[[str, bool], int],
) -> None:
    if len(lines) < 2 or not 0 < len(lines[-1]) < 3:
        return
    while len(lines[-1]) < 3 and len(lines[-2]) > 1:
        candidate = [lines[-2][-1], *lines[-1]]
        if not _fits(candidate, max_width, measure):
            break
        lines[-1].insert(0, lines[-2].pop())


def _wrap_chars(
    chars: list[tuple[str, bool]],
    max_width: int,
    measure: Callable[[str, bool], int],
) -> list[LaidOutLine]:
    if not chars:
        return [LaidOutLine((), 0)]
    raw_lines: list[list[tuple[str, bool]]] = []
    remaining = list(chars)
    while remaining:
        end = 1
        while end < len(remaining) and _fits(remaining[:end + 1], max_width, measure):
            end += 1
        if end < len(remaining) and remaining[end][0] in LINE_END_PUNCTUATION and end > 1:
            end -= 1
        raw_lines.append(remaining[:end])
        remaining = remaining[end:]
    _rebalance_orphan(raw_lines, max_width, measure)
    return [_join_chars(line, measure) for line in raw_lines]


def wrap_paragraph(
    runs: Sequence[TextRun],
    max_width: int,
    measure: Callable[[str, bool], int],
) -> tuple[LaidOutLine, ...]:
    """Wrap styled text while preserving characters, newlines, and weights."""

    paragraphs: list[list[tuple[str, bool]]] = [[]]
    for run in runs:
        for character in run.text:
            if character == "\n":
                paragraphs.append([])
            else:
                paragraphs[-1].append((character, run.bold))
    lines: list[LaidOutLine] = []
    for paragraph in paragraphs:
        lines.extend(_wrap_chars(paragraph, max(1, max_width), measure))
    return tuple(lines)


def paginate_lines(
    lines: Sequence[LaidOutLine],
    line_height: int,
    max_height: int,
) -> tuple[tuple[LaidOutLine, ...], ...]:
    per_page = max(1, max_height // max(1, line_height))
    pages = [tuple(lines[index:index + per_page]) for index in range(0, len(lines), per_page)]
    return tuple(pages) or ((),)
