#!/usr/bin/env python3
"""Build final DOCX/TXT files from confirmed Markdown drafts with OfficeCLI."""

from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path
from typing import Any

from common import ensure_dir, read_json, safe_filename
from officecli_backend import OfficeCli, OfficeCliError, issue_count, json_data, structural_error_count


def parse_application_lines(md_path: Path) -> tuple[list[str], list[str]]:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    fields = [line.strip() for line in lines if line.strip().startswith("➤")]
    warnings = [line for line in fields if "待用户确认" in line]
    return fields, warnings


def parse_application_field(md_path: Path, field_name: str) -> str:
    if not md_path.exists():
        return ""
    prefix = f"➤{field_name}："
    for line in md_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def application_version(draft_dir: Path) -> str:
    version = parse_application_field(draft_dir / "申请表信息.md", "版本号")
    return "" if "待用户确认" in version else version


def application_software_name(draft_dir: Path) -> str:
    name = parse_application_field(draft_dir / "申请表信息.md", "软件全称")
    return "" if "待用户确认" in name else name


def write_application_txt(draft_dir: Path, out_dir: Path) -> tuple[Path | None, list[str]]:
    md_path = draft_dir / "申请表信息.md"
    if not md_path.exists():
        return None, ["缺少草稿/申请表信息.md"]
    fields, warnings = parse_application_lines(md_path)
    out_path = out_dir / "申请表信息.txt"
    out_path.write_text("\n".join(fields) + "\n", encoding="utf-8")
    return out_path, warnings


def read_json_if_exists(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def confirmation_issues(workdir: Path) -> list[str]:
    draft_dir = workdir / "草稿"
    issues: list[str] = []
    business = read_json_if_exists(draft_dir / "业务理解.json")
    if not business or not business.get("user_confirmed"):
        issues.append("业务理解尚未确认：请确认 草稿/业务理解.md 后记录 `business` 门禁")
    selection = read_json_if_exists(draft_dir / "代码文件选择.json")
    if not selection or not selection.get("user_confirmed"):
        issues.append("代码文件选择尚未确认：请确认 草稿/代码文件选择.json 后记录 `code-selection` 门禁")
    screenshot = read_json_if_exists(workdir / "截图方式确认.json")
    if not screenshot.get("screenshot_method_confirmed"):
        issues.append("截图方式尚未确认：请选择截图方式后记录 `screenshot-method` 门禁")
    app_md = draft_dir / "申请表信息.md"
    if app_md.exists():
        _, warnings = parse_application_lines(app_md)
        if warnings:
            issues.append("申请表信息仍包含“待用户确认”字段")
    else:
        issues.append("缺少 草稿/申请表信息.md")
    app_confirmation = read_json_if_exists(draft_dir / "申请表字段确认.json")
    if not app_confirmation.get("application_fields_confirmed"):
        issues.append("申请表字段尚未确认：请补全字段后记录 `application-fields` 门禁")
    markdown_confirmation = read_json_if_exists(draft_dir / "最终生成确认.json")
    if not markdown_confirmation.get("markdown_confirmed"):
        issues.append("Markdown 草稿尚未最终确认：请确认全部草稿后记录 `markdown` 门禁")
    return issues


def parse_code_pages(md_path: Path) -> list[tuple[int, list[str]]]:
    pages: list[tuple[int, list[str]]] = []
    current_no: int | None = None
    current_lines: list[str] = []
    in_fence = False
    for raw in md_path.read_text(encoding="utf-8").splitlines():
        page_match = re.match(r"^##\s+第\s*(\d+)\s*页", raw.strip())
        if page_match:
            if current_no is not None:
                pages.append((current_no, current_lines))
            current_no = int(page_match.group(1))
            current_lines = []
            in_fence = False
            continue
        if raw.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if current_no is not None and in_fence:
            current_lines.append(raw)
    if current_no is not None:
        pages.append((current_no, current_lines))
    return pages


def header_commands(software_name: str, version: str) -> list[dict[str, Any]]:
    """Create a left title and right page-number region in the default header."""
    header_path = "/header[1]/p[1]"
    return [
        {"command": "add", "parent": "/", "type": "header", "props": {
            "type": "default", "text": f"{software_name} {version}", "align": "left",
            "font": "SimSun", "size": "9pt", "color": "#000000"}},
        {"command": "add", "parent": header_path, "type": "ptab",
         "props": {"align": "right", "relativeTo": "margin", "leader": "none"}},
        {"command": "add", "parent": header_path, "type": "run",
         "props": {"text": "第 ", "font": "SimSun", "size": "9pt", "color": "#000000"}},
        {"command": "add", "parent": header_path, "type": "field",
         "props": {"fieldType": "page", "font": "SimSun", "size": "9pt", "color": "#000000"}},
        {"command": "add", "parent": header_path, "type": "run",
         "props": {"text": " 页", "font": "SimSun", "size": "9pt", "color": "#000000"}},
    ]


def document_commands(software_name: str, version: str, *, code_mode: bool, page_start: int = 1) -> list[dict[str, Any]]:
    margin = "1.8cm" if code_mode else "2.5cm"
    default_size = "7pt" if code_mode else "10.5pt"
    commands: list[dict[str, Any]] = [
        {"command": "set", "path": "/", "props": {
            "docDefaults.font": "SimSun", "docDefaults.font.eastAsia": "SimSun",
            "docDefaults.font.hAnsi": "Times New Roman", "docDefaults.fontSize": default_size,
            "docDefaults.color": "#000000", "updateFields": "true",
            "title": f"{software_name} {version}"}},
        {"command": "set", "path": "/section[1]", "props": {
            "pageWidth": "21cm", "pageHeight": "29.7cm", "orientation": "portrait",
            "marginTop": margin, "marginBottom": margin, "marginLeft": margin, "marginRight": margin,
            "marginHeader": "0.9cm", "marginFooter": "0.9cm", "pageStart": str(page_start)}},
    ]
    commands.extend(header_commands(software_name, version))
    return commands


def code_paragraph_commands(pages: list[tuple[int, list[str]]]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for page_index, (_, lines) in enumerate(pages):
        for line_index, line in enumerate(lines):
            commands.append({"command": "add", "parent": "/body", "type": "paragraph", "props": {
                "text": line if line else " ", "font": "Consolas", "font.ea": "SimSun",
                "size": "7pt", "color": "#000000", "spaceBefore": "0pt", "spaceAfter": "0pt",
                "lineSpacing": "10pt", "lineRule": "exact", "keepLines": "true",
                "widowControl": "false", "wordWrap": "false",
                "pageBreakBefore": "true" if page_index > 0 and line_index == 0 else "false"}})
    return commands


def build_code_docx(cli: OfficeCli, md_path: Path, out_path: Path, software_name: str, version: str) -> int:
    pages = parse_code_pages(md_path)
    if not pages:
        raise ValueError(f"代码草稿没有可识别分页：{md_path}")
    commands = document_commands(software_name, version, code_mode=True, page_start=pages[0][0])
    commands.extend(code_paragraph_commands(pages))
    cli.create(out_path, commands)
    return len(pages)


IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def prepare_manual_markdown(md_path: Path, base_dir: Path, output: Path) -> list[dict[str, Any]]:
    """Replace local Markdown images with stable placeholders for later embedding."""
    images: list[dict[str, Any]] = []
    text = md_path.read_text(encoding="utf-8")
    text = re.sub(r"<!--[^>]*截图[^>]*-->", "【截图预留：请在此处插入当前功能页面或操作结果截图。】", text)

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1).strip() or "操作截图"
        raw_target = match.group(2).strip().strip("<>")
        target = re.sub(r'\s+["\'][^"\']*["\']\s*$', "", raw_target).strip()
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target):
            return f"\n\n【远程图片未嵌入：{alt}（{target}）】\n\n"
        image_path = (base_dir / target).resolve()
        if not image_path.is_file():
            return f"\n\n【截图缺失：{target}】\n\n"
        placeholder = f"OCLI_IMAGE_{len(images) + 1:04d}"
        images.append({"placeholder": placeholder, "path": image_path, "alt": alt})
        return f"\n\n{placeholder}\n\n"

    output.write_text(IMAGE_RE.sub(replace, text), encoding="utf-8")
    return images


def body_children(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for result in json_data(payload).get("results", []):
        if isinstance(result, dict) and result.get("path") == "/body":
            children = result.get("children")
            return children if isinstance(children, list) else []
    return []


def manual_format_commands(children: list[dict[str, Any]], images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    image_by_placeholder = {item["placeholder"]: item for item in images}
    commands: list[dict[str, Any]] = []
    for child in children:
        if child.get("type") != "paragraph" or not child.get("path"):
            continue
        path = str(child["path"])
        text = str(child.get("text") or "")
        style = str(child.get("style") or child.get("format", {}).get("style") or "")
        fmt = child.get("format") if isinstance(child.get("format"), dict) else {}
        if text in image_by_placeholder:
            image = image_by_placeholder[text]
            commands.append({"command": "set", "path": path, "props": {"text": "", "align": "center"}})
            commands.append({"command": "add", "parent": path, "type": "picture", "props": {
                "src": str(image["path"]), "width": "15cm", "alt": str(image["alt"])}})
            continue
        props: dict[str, str] = {
            "font": "SimSun", "font.ea": "SimSun", "color": "#000000",
            "spaceAfter": "6pt", "lineSpacing": "1.5x", "widowControl": "true"}
        if style.lower().startswith("heading"):
            props.update({"font": "SimHei", "font.ea": "SimHei", "keepNext": "true"})
        elif fmt.get("listStyle"):
            props.update({"size": "10.5pt", "align": "left"})
        elif text.startswith("【截图") or text.startswith("【远程图片"):
            props.update({"size": "10.5pt", "align": "center"})
        else:
            props.update({"size": "10.5pt", "align": "justify", "firstLineChars": "200"})
        commands.append({"command": "set", "path": path, "props": props})
    return commands


def build_manual_docx(cli: OfficeCli, md_path: Path, out_path: Path, base_dir: Path, software_name: str, version: str) -> None:
    prepared: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix=".officecli-manual-", dir=out_path.parent,
            encoding="utf-8", delete=False
        ) as handle:
            prepared = Path(handle.name)
        images = prepare_manual_markdown(md_path, base_dir, prepared)
        commands = document_commands(software_name, version, code_mode=False)
        commands.append({"command": "add", "parent": "/", "type": "markdown", "props": {"src": str(prepared.resolve())}})
        cli.create(out_path, commands)
        children = body_children(cli.get(out_path, "/body"))
        cli.run_batch(out_path, manual_format_commands(children, images))
    finally:
        if prepared:
            prepared.unlink(missing_ok=True)


def docx_checks(cli: OfficeCli, outputs: list[Path], expected_pages: dict[Path, int], preview_dir: Path,
                render_preview: bool = True) -> list[str]:
    notes: list[str] = []
    if render_preview:
        ensure_dir(preview_dir)
    for output in outputs:
        validation = cli.validate(output)
        errors = structural_error_count(validation)
        if errors:
            raise OfficeCliError(f"{output.name} OpenXML 结构校验失败：{errors} 个错误")
        issues = cli.issues(output)
        notes.append(f"- `{output.name}`：OpenXML 结构错误 0 个；内容/格式提示 {issue_count(issues)} 个。")
        expected = expected_pages.get(output)
        stats = cli.stats(output, native_page_count=expected is not None)
        pages = json_data(stats).get("pages")
        if expected is not None:
            if pages is None:
                notes.append(f"- `{output.name}`：当前环境无法取得 Word 原生页数；提交前需在 Word/WPS 中复核 {expected} 页。")
            elif int(pages) != expected:
                raise OfficeCliError(f"{output.name} 实际分页为 {pages} 页，草稿要求 {expected} 页")
            else:
                notes.append(f"- `{output.name}`：Word 原生分页 {pages} 页，与草稿一致。")
        if render_preview:
            preview = preview_dir / f"{output.stem}.png"
            try:
                cli.screenshot(output, preview)
                if preview.exists():
                    notes.append(f"- `{output.name}`：已生成预览 `{preview.relative_to(output.parent).as_posix()}`。")
            except (OfficeCliError, OSError) as exc:
                notes.append(f"- `{output.name}`：预览未生成（{exc}）。")
        else:
            notes.append(f"- `{output.name}`：已按参数跳过预览截图，结构和分页校验仍已执行。")
    return notes


def build_all(workdir: Path, software_name: str, version: str, skip_preview: bool,
              officecli_path: str | None = None, allow_untested_officecli: bool = False) -> dict[str, Any]:
    workdir = ensure_dir(workdir)
    draft_dir = workdir / "草稿"
    final_dir = ensure_dir(workdir / "正式资料")
    app_name = application_software_name(draft_dir)
    app_version = application_version(draft_dir)
    final_software_name = app_name or software_name
    final_version = app_version or version
    safe_name = safe_filename(final_software_name)
    outputs: list[Path] = []
    warnings: list[str] = []
    expected_pages: dict[Path, int] = {}
    cli = OfficeCli(officecli_path, require_tested_version=not allow_untested_officecli)
    if app_name and app_name != software_name:
        warnings.append(f"命令参数软件名称为 {software_name}，正式资料已按申请表信息软件名称 {app_name} 生成")
    if app_version and app_version != version:
        warnings.append(f"命令参数版本号为 {version}，正式资料已按申请表信息版本号 {app_version} 生成")
    screenshot_confirmation = read_json_if_exists(workdir / "截图方式确认.json")
    screenshot_method = screenshot_confirmation.get("screenshot_method")
    screenshot_manifest = workdir / "截图/截图清单.json"
    if screenshot_method == "skip":
        warnings.append("用户选择暂不截图；操作手册已保留截图预留位置")
    elif screenshot_method and not screenshot_manifest.exists():
        warnings.append("操作手册截图未生成或未插入；操作手册应保留截图预留位置")
    elif screenshot_manifest.exists() and not (read_json_if_exists(screenshot_manifest).get("screenshots") or []):
        warnings.append("操作手册截图清单为空；操作手册应保留截图预留位置")
    app_txt, app_warnings = write_application_txt(draft_dir, final_dir)
    if app_txt:
        outputs.append(app_txt)
    warnings.extend(app_warnings)
    code_specs = [
        ("代码-前30页.md", f"{safe_name}-代码(前30页).docx"),
        ("代码-后30页.md", f"{safe_name}-代码(后30页).docx"),
        ("代码-全部.md", f"{safe_name}-代码(全部).docx"),
    ]
    for md_name, docx_name in code_specs:
        md_path = draft_dir / md_name
        if md_path.exists():
            out_path = final_dir / docx_name
            expected_pages[out_path] = build_code_docx(cli, md_path, out_path, final_software_name, final_version)
            outputs.append(out_path)
    manual_md = draft_dir / "操作手册.md"
    if manual_md.exists():
        manual_out = final_dir / f"{safe_name}_操作手册.docx"
        manual_source = manual_md
        renamed_manual: Path | None = None
        if app_name and app_name != software_name:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".md", prefix=".officecli-renamed-", dir=draft_dir,
                delete=False, encoding="utf-8"
            ) as handle:
                handle.write(manual_md.read_text(encoding="utf-8").replace(software_name, app_name))
                renamed_manual = Path(handle.name)
            manual_source = renamed_manual
        try:
            build_manual_docx(cli, manual_source, manual_out, draft_dir, final_software_name, final_version)
        finally:
            if renamed_manual:
                renamed_manual.unlink(missing_ok=True)
        outputs.append(manual_out)
    else:
        warnings.append("缺少草稿/操作手册.md")
    docx_outputs = [path for path in outputs if path.suffix.lower() == ".docx"]
    notes = docx_checks(
        cli, docx_outputs, expected_pages, final_dir / "预览", render_preview=not skip_preview)
    report = write_report(final_dir, outputs, warnings, notes, cli.version)
    return {"outputs": [str(path) for path in outputs], "warnings": warnings, "report": str(report)}


def write_report(workdir: Path, outputs: list[Path], warnings: list[str], notes: list[str], officecli_version: str) -> Path:
    report = workdir / "生成报告.md"
    lines = ["# 生成报告", "", f"- DOCX 后端：OfficeCLI {officecli_version}", "- 自动更新：已禁用", "", "## 输出文件", ""]
    for path in outputs:
        size = path.stat().st_size if path.exists() else 0
        lines.append(f"- `{path.name}` ({size} bytes)")
    lines.extend(["", "## 警告", ""])
    lines.extend(f"- {warning}" for warning in warnings) if warnings else lines.append("- 无")
    lines.extend(["", "## DOCX 校验", "", *notes, ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default="软件著作权申请资料")
    parser.add_argument("--software-name", required=True)
    parser.add_argument("--version", default="V1.0")
    parser.add_argument("--officecli", help="OfficeCLI executable path; defaults to OFFICECLI_PATH or PATH")
    parser.add_argument("--allow-untested-officecli", action="store_true")
    parser.add_argument("--skip-preview", action="store_true")
    args = parser.parse_args()
    workdir = Path(args.workdir)
    issues = confirmation_issues(workdir)
    if issues:
        print("STOP_FOR_USER")
        print("NEXT_ACTION: 正式 Word/TXT 生成前必须完成以下确认：")
        for issue in issues:
            print(f"- {issue}")
        raise SystemExit(2)
    try:
        result = build_all(workdir, args.software_name, args.version, args.skip_preview,
                           args.officecli, args.allow_untested_officecli)
    except (OfficeCliError, ValueError) as exc:
        raise SystemExit(f"DOCX_BUILD_FAILED\n{exc}") from exc
    print(f"OK final materials: {workdir / '正式资料'}")
    for output in result["outputs"]:
        print(output)
    if result["warnings"]:
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"- {warning}")
    print(f"Report: {result['report']}")


if __name__ == "__main__":
    main()
