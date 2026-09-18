from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_docx_from_md import (  # noqa: E402
    THEME_FONT_TARGETS,
    code_paragraph_commands,
    header_commands,
    normalize_docx_theme_fonts,
    normalize_theme_fonts_xml,
    parse_code_pages,
    prepare_manual_markdown,
    theme_font_slots,
)
from common import (  # noqa: E402
    CODE_FONT_NAME,
    CODE_FONT_SIZE,
    CODE_LINE_SPACING,
    CODE_LINES_PER_PAGE,
    CODE_MAX_COLUMNS,
)
from officecli_backend import officecli_environment  # noqa: E402


class OfficeCliBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / ".tmp"
        self.temp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        for path in self.temp_dir.iterdir():
            if path.is_file():
                path.unlink()
        self.temp_dir.rmdir()

    def test_header_uses_positional_tab_and_page_field(self) -> None:
        commands = header_commands("示例软件", "V1.0")
        self.assertEqual(commands[0]["props"]["text"], "示例软件 V1.0")
        self.assertTrue(any(command.get("type") == "ptab" for command in commands))
        fields = [command for command in commands if command.get("type") == "field"]
        self.assertEqual(fields[0]["props"]["fieldType"], "page")

    def test_code_paragraphs_do_not_force_page_breaks(self) -> None:
        commands = code_paragraph_commands([(1, ["a", "b"]), (2, ["c", "d"])])
        self.assertEqual([command["props"]["text"] for command in commands], ["a", "b", "c", "d"])
        self.assertTrue(all("pageBreakBefore" not in command["props"] for command in commands))
        self.assertTrue(all("keepLines" not in command["props"] for command in commands))
        self.assertTrue(all(command["props"]["font"] == CODE_FONT_NAME for command in commands))
        self.assertTrue(all(command["props"]["size"] == CODE_FONT_SIZE for command in commands))
        self.assertTrue(all(command["props"]["lineSpacing"] == CODE_LINE_SPACING for command in commands))
        self.assertEqual(CODE_LINES_PER_PAGE, 55)
        self.assertEqual(CODE_MAX_COLUMNS, 90)

    def test_parse_code_pages_preserves_page_numbers(self) -> None:
        path = self.temp_dir / "code.md"
        path.write_text("## 第 31 页\n\n```text\na\nb\n```\n", encoding="utf-8")
        self.assertEqual(parse_code_pages(path), [(31, ["a", "b"])])

    def test_manual_local_image_becomes_placeholder(self) -> None:
        image = self.temp_dir / "test-officecli-builder-shot.png"
        source = self.temp_dir / "test-officecli-builder-manual.md"
        output = self.temp_dir / "test-officecli-builder-prepared.md"
        image.write_bytes(b"png")
        source.write_text("# 标题\n\n![页面](test-officecli-builder-shot.png)\n", encoding="utf-8")
        images = prepare_manual_markdown(source, self.temp_dir, output)
        self.assertEqual(len(images), 1)
        self.assertIn("OCLI_IMAGE_0001", output.read_text(encoding="utf-8"))
        self.assertEqual(images[0]["path"], image.resolve())

    def test_environment_disables_updates_and_residents(self) -> None:
        old_update = os.environ.get("OFFICECLI_SKIP_UPDATE")
        old_resident = os.environ.get("OFFICECLI_NO_AUTO_RESIDENT")
        try:
            env = officecli_environment()
            self.assertEqual(env["OFFICECLI_SKIP_UPDATE"], "1")
            self.assertEqual(env["OFFICECLI_NO_AUTO_RESIDENT"], "1")
        finally:
            if old_update is None:
                os.environ.pop("OFFICECLI_SKIP_UPDATE", None)
            if old_resident is None:
                os.environ.pop("OFFICECLI_NO_AUTO_RESIDENT", None)

    def test_theme_fonts_are_normalized_without_direct_docx_writes(self) -> None:
        theme = (
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<a:themeElements><a:fontScheme>"
            '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface="等线"/><a:cs typeface=""/>'
            '<a:font script="Hans" typeface="等线 Light"/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="等线"/><a:cs typeface=""/></a:minorFont>'
            "</a:fontScheme></a:themeElements></a:theme>"
        )

        normalized = normalize_theme_fonts_xml(theme)

        self.assertEqual(theme_font_slots(normalized), THEME_FONT_TARGETS)
        self.assertNotIn("Calibri", normalized)
        self.assertNotIn("等线", normalized)
        self.assertNotIn('script="Hans"', normalized)

    def test_theme_update_uses_officecli_root_replacement_and_verifies_result(self) -> None:
        initial = (
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<a:themeElements><a:fontScheme>"
            '<a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface="等线"/><a:cs typeface=""/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="等线"/><a:cs typeface=""/></a:minorFont>'
            "</a:fontScheme></a:themeElements></a:theme>"
        )

        class FakeOfficeCli:
            def __init__(self) -> None:
                self.theme = initial
                self.raw_set_args: tuple[str, str, str] | None = None
                self.raw_parts: list[str] = []

            def raw(self, output: Path, part: str) -> dict[str, object]:
                self.raw_parts.append(part)
                return {"success": True, "data": self.theme}

            def raw_set(self, output: Path, part: str, xpath: str, action: str, xml: str) -> dict[str, object]:
                self.raw_set_args = (part, xpath, action)
                self.theme = xml
                return {"success": True}

        cli = FakeOfficeCli()
        normalize_docx_theme_fonts(cli, self.temp_dir / "sample.docx")  # type: ignore[arg-type]

        self.assertEqual(cli.raw_set_args, ("/theme", "/a:theme", "replace"))
        self.assertEqual(cli.raw_parts, ["/theme", "/theme"])
        self.assertEqual(theme_font_slots(cli.theme), THEME_FONT_TARGETS)


if __name__ == "__main__":
    unittest.main()
