from __future__ import annotations

import unittest

from rich_text_layout import TextRun, content_width, normalize_blocks, paginate_lines, wrap_paragraph


def measure(text: str, bold: bool) -> int:
    return len(text) * (11 if bold else 10)


class RichTextLayoutTests(unittest.TestCase):
    def test_short_text_uses_measured_width(self) -> None:
        paragraphs = ((TextRun("推荐：巨人杀手"),),)
        width = content_width(
            paragraphs,
            lambda text, _bold: len(text) * 20,
            minimum_width=120,
            maximum_width=780,
        )
        self.assertEqual(140, width)

    def test_long_text_stops_at_double_width_cap(self) -> None:
        paragraphs = ((TextRun("甲" * 100),),)
        width = content_width(paragraphs, measure, minimum_width=120, maximum_width=780)
        self.assertEqual(780, width)

    def test_wide_control_sets_the_minimum_content_width(self) -> None:
        paragraphs = ((TextRun("短"),),)
        width = content_width(paragraphs, measure, minimum_width=260, maximum_width=780)
        self.assertEqual(260, width)

    def test_explicit_newline_uses_the_widest_line(self) -> None:
        paragraphs = ((TextRun("第一行\n第二行更长"),),)
        width = content_width(paragraphs, measure, minimum_width=20, maximum_width=780)
        self.assertEqual(50, width)

    def test_mixed_runs_are_measured_together(self) -> None:
        paragraphs = ((TextRun("标题 "), TextRun("加粗", True)),)
        width = content_width(paragraphs, measure, minimum_width=20, maximum_width=780)
        self.assertEqual(52, width)

    def test_empty_paragraphs_use_minimum_width(self) -> None:
        width = content_width((), measure, minimum_width=120, maximum_width=780)
        self.assertEqual(120, width)

    def test_rebalances_two_character_orphan(self) -> None:
        lines = wrap_paragraph((TextRun("甲乙丙丁戊己庚"),), 50, measure)
        self.assertEqual(["甲乙丙丁", "戊己庚"], [line.text for line in lines])

    def test_preserves_mixed_weights(self) -> None:
        paragraphs = normalize_blocks(
            [{"label": "所需装备", "value": "无尽之刃、饮血剑"}],
            "fallback",
        )
        self.assertFalse(paragraphs[0][0].bold)
        self.assertTrue(paragraphs[0][1].bold)

    def test_keeps_closing_punctuation_off_line_start(self) -> None:
        lines = wrap_paragraph((TextRun("甲乙，丙丁"),), 20, measure)
        self.assertEqual("甲", lines[0].text)
        self.assertEqual("乙，", lines[1].text)

    def test_preserves_explicit_newline_and_every_character(self) -> None:
        source = "第一段\n第二段"
        lines = wrap_paragraph((TextRun(source),), 100, measure)
        self.assertEqual(["第一段", "第二段"], [line.text for line in lines])
        self.assertEqual(source, "\n".join(line.text for line in lines))

    def test_very_narrow_width_terminates_without_losing_text(self) -> None:
        source = "甲乙丙丁"
        lines = wrap_paragraph((TextRun(source),), 1, measure)
        self.assertEqual(source, "".join(line.text for line in lines))
        self.assertEqual(["甲", "乙", "丙", "丁"], [line.text for line in lines])

    def test_invalid_blocks_use_plain_fallback(self) -> None:
        paragraphs = normalize_blocks([{"label": "缺值"}], "原始消息")
        self.assertEqual("原始消息", paragraphs[0][0].text)
        self.assertFalse(paragraphs[0][0].bold)

    def test_pagination_uses_reflowed_lines(self) -> None:
        lines = wrap_paragraph((TextRun("甲乙丙丁戊己"),), 20, measure)
        pages = paginate_lines(lines, line_height=12, max_height=24)
        self.assertEqual(2, len(pages))
        self.assertEqual("甲乙丙丁戊己", "".join(line.text for page in pages for line in page))


if __name__ == "__main__":
    unittest.main()
