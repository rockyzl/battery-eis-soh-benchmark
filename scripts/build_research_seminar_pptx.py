# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "Pillow>=11,<12",
#   "python-pptx>=1.0,<2",
# ]
# ///
"""Build the 21-slide KIT B-versus-D_CORE paired development seminar deck.

The Markdown source remains authoritative.  This script intentionally fails if
the source no longer contains exactly 21 slides or 21 Speaker notes blocks.
It creates an editable PPTX and same-layout PNG previews for visual QA.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]
from pptx import Presentation  # type: ignore[import-not-found]
from pptx.dml.color import RGBColor  # type: ignore[import-not-found]
from pptx.enum.shapes import MSO_SHAPE  # type: ignore[import-not-found]
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN  # type: ignore[import-not-found]
from pptx.util import Inches, Pt  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/slides/battery-early-warning-research-seminar.md"
OUTPUT = (
    ROOT / "docs/slides/battery-early-warning-research-seminar-kit-b-vs-dcore-development-oof.pptx"
)
PREVIEW_DIR = ROOT / "docs/slides/rendered-kit-b-vs-dcore-development-oof"
FONT_PATH = Path("/home/lu2/.local/share/fonts/chinese-windows/msyh.ttc")

W, H = 13.333, 7.5
PX = 120
NAVY = "0B1628"
INK = "172033"
MUTED = "627089"
PAPER = "F7F9FC"
WHITE = "FFFFFF"
CYAN = "27C2D1"
BLUE = "3478F6"
GREEN = "31B57A"
ORANGE = "F2A93B"
RED = "E45B5B"
PURPLE = "7656D6"
PALE_BLUE = "EAF2FF"
PALE_CYAN = "E8F9FA"
PALE_GREEN = "EAF8F1"
PALE_ORANGE = "FFF5E4"
PALE_RED = "FDEEEE"
GRID = "DCE3EE"


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def parse_source() -> tuple[list[str], list[str]]:
    text = SOURCE.read_text(encoding="utf-8")
    chunks = re.split(r"\n---\n", text)
    titles: list[str] = []
    notes: list[str] = []
    for chunk in chunks:
        headings = re.findall(r"^#{1,2}\s+(.+)$", chunk, flags=re.MULTILINE)
        if not headings:
            continue
        titles.append(headings[0].strip())
        match = re.search(r"<!--\s*Speaker notes:\s*(.*?)\s*-->", chunk, flags=re.DOTALL)
        notes.append(re.sub(r"\s+", " ", match.group(1)).strip() if match else "")
    if len(titles) != 21 or len(notes) != 21 or not all(notes):
        raise RuntimeError(
            f"Expected 21 slides and notes blocks in {SOURCE}; got {len(titles)} titles/{len(notes)} notes"
        )
    return titles, notes


class Deck:
    def __init__(self, preview_dir: Path):
        # python-pptx and Pillow are isolated PEP 723 script dependencies and do not
        # ship usable type information in the repository's core environment.
        self.prs: Any = Presentation()
        self.prs.slide_width = Inches(W)
        self.prs.slide_height = Inches(H)
        self.blank = self.prs.slide_layouts[6]
        self.preview_dir = preview_dir
        preview_dir.mkdir(parents=True, exist_ok=True)
        self.slide: Any | None = None
        self.canvas: Any | None = None
        self.draw: Any | None = None
        self.index = 0

    def _font(self, size: float, bold: bool = False):
        # The available YaHei collection renders CJK reliably. Synthetic bold is
        # handled by PowerPoint; previews use the same face for stable line wraps.
        return ImageFont.truetype(str(FONT_PATH), max(8, round(size * 1.37)))

    def new(self, title: str, section: str, notes: str, dark: bool = False):
        if self.slide is not None:
            self.finish_slide()
        self.index += 1
        self.slide = self.prs.slides.add_slide(self.blank)
        bg = NAVY if dark else PAPER
        self.slide.background.fill.solid()
        self.slide.background.fill.fore_color.rgb = rgb(bg)
        self.canvas = Image.new("RGB", (round(W * PX), round(H * PX)), f"#{bg}")
        self.draw = ImageDraw.Draw(self.canvas)
        self.slide.notes_slide.notes_text_frame.text = notes
        if self.index > 1:
            self.text(section.upper(), 0.6, 0.26, 2.3, 0.22, 8.5, CYAN, bold=True)
            self.text(title, 0.6, 0.55, 12.0, 0.55, 22, WHITE if dark else INK, bold=True)
            self.line(0.6, 1.16, 12.73, 1.16, CYAN, 1.5)

    def finish_slide(self):
        if self.slide is None:
            return
        if self.index > 1:
            self.text(
                "DEVELOPMENT OOF  ·  B vs D_CORE/EIS  ·  NO PROTECTED TEST  ·  NO SELECTION",
                0.6,
                7.12,
                9.4,
                0.16,
                7.5,
                MUTED,
                bold=True,
            )
            self.text(f"{self.index:02d} / 21", 11.75, 7.09, 0.95, 0.18, 8, MUTED, align="right")
        out = self.preview_dir / f"slide-{self.index:02d}.png"
        self.canvas.save(out, quality=94)

    def save(self, path: Path):
        self.finish_slide()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.prs.core_properties.title = "电池衰减预警：KIT B vs D_CORE/EIS development OOF 研究"
        self.prs.core_properties.subject = (
            "21-slide paired B vs D_CORE research seminar; development evidence only"
        )
        self.prs.core_properties.author = "Lu Zhang"
        self.prs.core_properties.comments = (
            "Generated from docs/slides/battery-early-warning-research-seminar.md; "
            "DEVELOPMENT OOF, paired B vs D_CORE/EIS, no protected test, no selection."
        )
        self.prs.save(path)

    def rect(self, x, y, w, h, fill, line=None, radius=0.08, width=1):
        shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
        shape = self.slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
        shape.line.color.rgb = rgb(line or fill)
        shape.line.width = Pt(width)
        self.draw.rounded_rectangle(
            [round(x * PX), round(y * PX), round((x + w) * PX), round((y + h) * PX)],
            radius=round(radius * PX),
            fill=f"#{fill}",
            outline=f"#{line or fill}",
            width=max(1, round(width * PX / 72)),
        )
        return shape

    def circle(self, x, y, d, fill, line=None, width=1):
        shape = self.slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
        shape.line.color.rgb = rgb(line or fill)
        shape.line.width = Pt(width)
        self.draw.ellipse(
            [x * PX, y * PX, (x + d) * PX, (y + d) * PX],
            fill=f"#{fill}",
            outline=f"#{line or fill}",
        )
        return shape

    def line(self, x1, y1, x2, y2, color=GRID, width=1.5):
        shape = self.slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(x1),
            Inches(y1),
            Inches(max(0.01, x2 - x1)),
            Inches(max(0.01, y2 - y1)),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(color)
        shape.line.fill.background()
        self.draw.line(
            [x1 * PX, y1 * PX, x2 * PX, y2 * PX],
            fill=f"#{color}",
            width=max(1, round(width * PX / 72)),
        )

    def text(
        self, text, x, y, w, h, size, color=INK, bold=False, align="left", valign="top", margin=0.02
    ):
        box = self.slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = box.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Inches(margin)
        tf.margin_top = tf.margin_bottom = Inches(margin)
        tf.vertical_anchor = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }[valign]
        lines = str(text).split("\n")
        for i, line_text in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = line_text
            p.font.name = "Microsoft YaHei"
            p.font.size = Pt(size)
            p.font.bold = bold
            p.font.color.rgb = rgb(color)
            p.alignment = {
                "left": PP_ALIGN.LEFT,
                "center": PP_ALIGN.CENTER,
                "right": PP_ALIGN.RIGHT,
            }[align]
            p.space_after = Pt(0)
        # Pillow preview with deterministic wrapping.
        font = self._font(size, bold)
        max_px = max(1, int(w * PX - margin * 2 * PX))
        wrapped: list[str] = []
        for raw in lines:
            if not raw:
                wrapped.append("")
                continue
            current = ""
            for ch in raw:
                test = current + ch
                if self.draw.textlength(test, font=font) <= max_px or not current:
                    current = test
                else:
                    wrapped.append(current)
                    current = ch
            wrapped.append(current)
        spacing = max(2, round(size * 0.18))
        bbox = self.draw.multiline_textbbox(
            (0, 0), "\n".join(wrapped), font=font, spacing=spacing, align=align
        )
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = x * PX + margin * PX
        if align == "center":
            tx = x * PX + (w * PX - tw) / 2
        elif align == "right":
            tx = (x + w) * PX - margin * PX - tw
        ty = y * PX + margin * PX
        if valign == "middle":
            ty = y * PX + (h * PX - th) / 2
        elif valign == "bottom":
            ty = (y + h) * PX - margin * PX - th
        self.draw.multiline_text(
            (tx, ty), "\n".join(wrapped), font=font, fill=f"#{color}", spacing=spacing, align=align
        )
        return box

    def badge(self, text, x, y, w, fill=PALE_CYAN, color=INK):
        self.rect(x, y, w, 0.34, fill, fill, 0.17)
        self.text(text, x + 0.05, y + 0.03, w - 0.1, 0.22, 8.5, color, bold=True, align="center")

    def card(self, x, y, w, h, title, body="", accent=BLUE, fill=WHITE, value=None):
        self.rect(x, y, w, h, fill, GRID, 0.12, 0.8)
        self.rect(x, y, 0.07, h, accent, accent, 0)
        if value is not None:
            self.text(value, x + 0.23, y + 0.19, w - 0.42, 0.45, 22, accent, bold=True)
            self.text(title, x + 0.23, y + 0.69, w - 0.42, 0.3, 10, INK, bold=True)
            if body:
                self.text(body, x + 0.23, y + 1.05, w - 0.42, h - 1.2, 8.7, MUTED)
        else:
            self.text(title, x + 0.23, y + 0.18, w - 0.42, 0.34, 12, INK, bold=True)
            if body:
                self.text(body, x + 0.23, y + 0.65, w - 0.42, h - 0.82, 9.2, MUTED)

    def flow(self, items, x, y, total_w, box_h=0.82, accent=BLUE, font=10):
        gap = 0.36
        bw = (total_w - gap * (len(items) - 1)) / len(items)
        for i, item in enumerate(items):
            xx = x + i * (bw + gap)
            self.rect(xx, y, bw, box_h, WHITE, GRID, 0.1)
            self.text(
                item,
                xx + 0.08,
                y + 0.08,
                bw - 0.16,
                box_h - 0.16,
                font,
                INK,
                bold=True,
                align="center",
                valign="middle",
            )
            if i < len(items) - 1:
                self.text("→", xx + bw, y + 0.2, gap, 0.3, 16, accent, bold=True, align="center")

    def bullet_list(self, items, x, y, w, row_h=0.55, size=10.5, accent=CYAN):
        for i, item in enumerate(items):
            yy = y + i * row_h
            self.circle(x, yy + 0.12, 0.13, accent)
            self.text(item, x + 0.27, yy, w - 0.27, row_h - 0.02, size, INK)

    def metric_table(self, x, y, headers, rows, widths, row_h=0.55, highlights=None):
        highlights = highlights or set()
        xx = x
        for j, (head, cw) in enumerate(zip(headers, widths)):
            self.rect(xx, y, cw, row_h, NAVY, NAVY, 0)
            self.text(
                head,
                xx + 0.05,
                y + 0.1,
                cw - 0.1,
                row_h - 0.16,
                9,
                WHITE,
                bold=True,
                align="center",
                valign="middle",
            )
            xx += cw
        for i, row in enumerate(rows):
            xx = x
            yy = y + (i + 1) * row_h
            for j, (val, cw) in enumerate(zip(row, widths)):
                fill = PALE_GREEN if (i, j) in highlights else (WHITE if i % 2 == 0 else "F0F4F9")
                self.rect(xx, yy, cw, row_h, fill, GRID, 0, 0.5)
                self.text(
                    str(val),
                    xx + 0.05,
                    yy + 0.08,
                    cw - 0.1,
                    row_h - 0.14,
                    9.2,
                    GREEN if (i, j) in highlights else INK,
                    bold=(i, j) in highlights,
                    align="center",
                    valign="middle",
                )
                xx += cw

    def bar_chart(self, x, y, w, h, labels, values, colors, max_value=None, value_fmt="{:.3f}"):
        max_value = max_value or max(values) * 1.1
        left = x + 0.75
        bottom = y + h - 0.55
        top = y + 0.25
        chart_h = bottom - top
        for frac in [0, 0.25, 0.5, 0.75, 1]:
            yy = bottom - chart_h * frac
            self.line(left, yy, x + w - 0.15, yy, GRID, 0.7)
            self.text(f"{max_value * frac:.1f}", x, yy - 0.11, 0.62, 0.2, 7.5, MUTED, align="right")
        gap = (w - 1.1) / len(values)
        bw = gap * 0.52
        for i, (lab, val, col) in enumerate(zip(labels, values, colors)):
            bx = left + i * gap + (gap - bw) / 2
            bh = chart_h * val / max_value
            self.rect(bx, bottom - bh, bw, bh, col, col, 0)
            self.text(
                value_fmt.format(val),
                bx - 0.12,
                bottom - bh - 0.3,
                bw + 0.24,
                0.24,
                8,
                col,
                bold=True,
                align="center",
            )
            self.text(lab, bx - 0.22, bottom + 0.1, bw + 0.44, 0.38, 7.7, INK, align="center")


def build(output: Path, preview_dir: Path) -> None:
    titles, notes = parse_source()
    d = Deck(preview_dir)

    # 1 — Title
    d.new(titles[0], "", notes[0], dark=True)
    d.badge("KIT 228 电芯 · RESEARCH SEMINAR", 0.68, 0.52, 2.7, fill="15365B", color=CYAN)
    d.text("电池衰减预警", 0.7, 1.28, 11.5, 0.62, 30, WHITE, bold=True)
    d.text("EIS 能让未来衰减预测更好吗？", 0.7, 1.98, 11.5, 0.68, 30, CYAN, bold=True)
    d.text(
        "condition-held-out paired development OOF · capacity baseline vs D_CORE/EIS",
        0.72,
        2.86,
        11.4,
        0.34,
        12,
        "A8B8CE",
    )
    for x, val, label in [
        (0.72, "228", "physical cells"),
        (3.35, "76", "operating conditions"),
        (5.98, "H3", "exact future check-up"),
    ]:
        d.rect(x, 3.62, 2.28, 1.28, "14243C", "29405F", 0.12)
        d.text(val, x + 0.18, 3.82, 1.92, 0.47, 24, CYAN, bold=True)
        d.text(label, x + 0.18, 4.42, 1.92, 0.25, 8.5, "A8B8CE")
    d.rect(8.64, 3.62, 3.95, 1.28, "281D27", "67414B", 0.12)
    d.text("当前证据边界", 8.88, 3.83, 3.45, 0.28, 10, ORANGE, bold=True)
    d.text(
        "B vs D_CORE paired development run\n无 protected test · 未做 selection",
        8.88,
        4.2,
        3.45,
        0.55,
        10,
        WHITE,
        bold=True,
    )
    d.text(
        "Lu Zhang  ·  independent public-data research project", 0.72, 6.52, 6.8, 0.28, 9, "A8B8CE"
    )
    d.text(
        "DEVELOPMENT OOF  ·  B vs D_CORE/EIS  ·  NO PROTECTED TEST  ·  NO SELECTION",
        0.72,
        7.05,
        8.7,
        0.2,
        7.5,
        CYAN,
        bold=True,
    )
    d.text("01 / 21", 11.75, 7.02, 0.85, 0.2, 8, "A8B8CE", align="right")

    # 2
    d.new(titles[1], "研究问题", notes[1])
    d.card(
        0.7, 1.52, 4.2, 2.3, "现在知道什么？", "容量、当前 SoH\n历史趋势、使用条件", BLUE, PALE_BLUE
    )
    d.text("→", 5.03, 2.22, 0.7, 0.5, 28, CYAN, bold=True, align="center")
    d.card(
        5.9,
        1.52,
        6.7,
        2.3,
        "真正要提前知道什么？",
        "未来第三次完成的检查时，SoH 会下降多少？\n哪些电芯值得优先复测或人工审查？",
        GREEN,
        PALE_GREEN,
    )
    d.rect(0.7, 4.22, 11.9, 0.74, PALE_ORANGE, ORANGE, 0.1)
    d.text("测量结果 ≠ 未来预测", 0.95, 4.4, 2.4, 0.3, 13, ORANGE, bold=True)
    d.text("本项目预测连续衰减量，再派生 future SoH。", 3.42, 4.42, 8.75, 0.28, 10, INK)
    d.bullet_list(
        ["不预测热失控、火灾、内短路或所有故障", "“优先复测”只是业务价值假设，尚未验证维护效果"],
        0.82,
        5.38,
        11.5,
        0.58,
        10.5,
    )

    # 3
    d.new(titles[2], "数据单位", notes[2])
    d.text("76 conditions", 0.8, 1.5, 2.2, 0.4, 18, BLUE, bold=True, align="center")
    d.text("×", 3.02, 1.5, 0.45, 0.4, 18, MUTED, bold=True, align="center")
    d.text("3 replicate cells", 3.48, 1.5, 2.5, 0.4, 18, PURPLE, bold=True, align="center")
    d.text("=", 6.0, 1.5, 0.45, 0.4, 18, MUTED, bold=True, align="center")
    d.text("228 cells", 6.48, 1.5, 2.1, 0.4, 18, GREEN, bold=True, align="center")
    d.metric_table(
        0.72,
        2.18,
        ["层级", "数据含义", "拆分规则"],
        [
            ["Condition", "一组固定老化条件", "完整 hold out"],
            ["Cell", "真实 18650 电芯", "同 condition 不拆"],
            ["Check-up", "同一电芯的重复诊断", "不是独立电芯"],
            ["Spectrum / row", "检查细节", "不是独立样本"],
        ],
        [2.25, 5.0, 4.7],
        0.72,
        {(0, 2), (1, 2)},
    )
    d.rect(0.72, 5.9, 11.95, 0.58, PALE_RED, RED, 0.08)
    d.text(
        "科学独立规模由 76 conditions / 228 cells 决定，不由上百万频率行决定。",
        0.95,
        6.06,
        11.5,
        0.26,
        10.2,
        RED,
        bold=True,
        align="center",
    )

    # 4
    d.new(titles[3], "三类老化", notes[3])
    modes = [
        ("CALENDAR", "16 conditions · 48 cells", "静置老化", "elapsed days", BLUE),
        ("CYCLIC", "35 conditions · 99 cells", "受控充放电", "EFC + elapsed days", GREEN),
        (
            "DRIVING PROFILE",
            "11 conditions · 33 cells",
            "WLTP-derived 动态功率",
            "EFC；距离需可追溯",
            PURPLE,
        ),
    ]
    for i, (name, n, desc, exp, col) in enumerate(modes):
        x = 0.72 + i * 4.03
        d.card(x, 1.58, 3.72, 3.45, name, f"{n}\n\n{desc}\n\n主要 exposure\n{exp}", col, WHITE)
    d.rect(0.72, 5.36, 11.78, 0.9, PALE_ORANGE, ORANGE, 0.1)
    d.text("分别训练 · 分别报告", 0.96, 5.58, 2.55, 0.3, 13, ORANGE, bold=True)
    d.text(
        "本页是本次 paired H3 支持；check-up index 只是顺序，模式误差不能混成一个“全局最好模型”。",
        3.46,
        5.61,
        8.58,
        0.3,
        10,
        INK,
    )

    # 5
    d.new(titles[4], "本次运行", notes[4])
    d.flow(
        ["Q0", "SoH 历史", "衰减斜率", "B baseline", "D_CORE + EIS", "H3 loss"],
        0.7,
        1.58,
        11.92,
        0.88,
        BLUE,
        9,
    )
    d.rect(0.72, 2.85, 7.28, 2.28, WHITE, GRID, 0.12)
    d.text("PAIRED COHORT · B vs D_CORE", 0.98, 3.08, 4.7, 0.28, 11, BLUE, bold=True)
    d.bullet_list(
        [
            "228 cells / 76 conditions / 3 replicates",
            "3,999 capacity endpoints · 3,993 EIS acquisitions",
            "2,919 matched B/D_CORE H3 snapshots",
        ],
        0.98,
        3.55,
        6.55,
        0.46,
        10,
    )
    d.rect(8.36, 2.85, 4.24, 2.28, PALE_RED, RED, 0.12)
    d.text("本次明确未使用", 8.65, 3.1, 3.6, 0.3, 12, RED, bold=True)
    d.text(
        "pulse · O2 classification\nRUL · protected labels",
        8.65,
        3.68,
        3.62,
        0.85,
        13,
        INK,
        bold=True,
        align="center",
    )
    d.badge("O2 / RUL = false", 9.2, 4.62, 2.62, PALE_RED, RED)
    d.text(
        "D_CORE = B + 54 个当前检查 EIS core values；只做配对诊断",
        0.78,
        5.68,
        11.7,
        0.42,
        14,
        ORANGE,
        bold=True,
        align="center",
    )

    # 6
    d.new(titles[5], "目标定义", notes[5])
    equations = [
        ("1", "SoH_t (%)", "100 × Capacity_t / Q0"),
        ("2", "H3 loss", "SoH_t − SoH_t+3"),
        ("3", "Future SoH", "current SoH − predicted loss"),
    ]
    for i, (num, name, eq) in enumerate(equations):
        x = 0.72 + i * 4.02
        d.rect(x, 1.56, 3.72, 1.58, WHITE, GRID, 0.12)
        d.circle(x + 0.22, 1.83, 0.42, [BLUE, GREEN, PURPLE][i])
        d.text(num, x + 0.22, 1.91, 0.42, 0.2, 10, WHITE, bold=True, align="center")
        d.text(name, x + 0.78, 1.76, 2.65, 0.3, 12, INK, bold=True)
        d.text(eq, x + 0.78, 2.29, 2.65, 0.3, 10, [BLUE, GREEN, PURPLE][i], bold=True)
    d.card(
        0.72, 3.52, 3.7, 2.15, "Q0", "每个电芯冻结的首个有效标准容量 endpoint。", BLUE, PALE_BLUE
    )
    d.card(
        4.8,
        3.52,
        3.7,
        2.15,
        "H3",
        "未来第三次完成的 check-up；不是三天，也不是三次 cycle。",
        GREEN,
        PALE_GREEN,
    )
    d.card(
        8.88,
        3.52,
        3.7,
        2.15,
        "缺失与符号",
        "负 loss 保留；末端无 exact H3 标为 no_h3_followup。",
        ORANGE,
        PALE_ORANGE,
    )
    d.badge("单位：SoH percentage points (pp)", 4.3, 6.04, 4.7, PALE_CYAN, INK)

    # 7
    d.new(titles[6], "EDA / 样本构造", notes[6])
    d.metric_table(
        0.72,
        1.52,
        ["状态", "数量", "含义"],
        [
            ["eligible_f1_b", "2,919", "足够历史 + exact H3 truth"],
            ["insufficient_history", "456", "前两个 check-up 不足"],
            ["no_h3_followup", "624", "右边界无未来第三次检查"],
            ["合计", "3,999", "与 source manifest 对齐"],
        ],
        [3.1, 1.65, 4.95],
        0.64,
        {(0, 1), (3, 1)},
    )
    d.rect(10.0, 1.52, 2.56, 3.2, NAVY, NAVY, 0.12)
    d.text("2,919", 10.26, 1.96, 2.04, 0.56, 28, CYAN, bold=True, align="center")
    d.text("eligible snapshots", 10.25, 2.62, 2.05, 0.28, 9, WHITE, bold=True, align="center")
    d.text("不是独立电池数", 10.25, 3.16, 2.05, 0.28, 10, ORANGE, bold=True, align="center")
    d.text("来自 180 个满足条件的电芯", 10.24, 3.68, 2.08, 0.5, 8.5, "C5D1E0", align="center")
    mode_counts = [1134, 1190, 595]
    mode_labels = ["Calendar", "Cyclic", "Profile"]
    mode_colors = [BLUE, GREEN, PURPLE]
    total = sum(mode_counts)
    mode_x = 0.72
    d.text("可训练快照按模式", 0.72, 5.05, 2.3, 0.28, 11, INK, bold=True)
    for mode_count, mode_label, mode_color in zip(mode_counts, mode_labels, mode_colors):
        ww = 11.84 * mode_count / total
        d.rect(mode_x, 5.52, ww, 0.6, mode_color, mode_color, 0)
        d.text(
            f"{mode_label}  {mode_count:,}",
            mode_x,
            5.68,
            ww,
            0.24,
            9,
            WHITE,
            bold=True,
            align="center",
        )
        mode_x += ww
    d.text(
        "缺失原因必须分开记录，不能统一填 0 或无解释丢弃。",
        0.72,
        6.42,
        11.84,
        0.28,
        10,
        RED,
        bold=True,
        align="center",
    )

    # 8
    d.new(titles[7], "特征工程", notes[7])
    groups = [
        ("状态", "当前 / 上一次 / 上上次 SoH", BLUE),
        ("趋势", "最近一次与两个间隔 signed loss", GREEN),
        ("进程", "check-up index · elapsed days · throughput", PURPLE),
        ("D_CORE / EIS", "27 frequencies × compensated Re / Im = 54 values", ORANGE),
    ]
    for i, (title, body, col) in enumerate(groups):
        x = 0.72 + (i % 2) * 6.0
        y = 1.5 + (i // 2) * 1.52
        d.card(x, y, 5.72, 1.18, title, body, col, WHITE)
    d.rect(0.72, 4.72, 11.74, 1.2, NAVY, NAVY, 0.12)
    d.text("PREFIX-ONLY", 0.98, 5.02, 2.2, 0.34, 15, CYAN, bold=True)
    d.text("预测时点 t 的输入只能来自 t 及以前", 3.15, 5.0, 4.7, 0.34, 12, WHITE, bold=True)
    d.text("不读 t+1 … t+3 future values", 8.15, 5.02, 3.95, 0.3, 11, ORANGE, bold=True)
    d.text(
        "B 与 D_CORE 使用同一快照/label/fold；EIS 缺失用训练折规则处理，所有 preprocessing 只在 training conditions 拟合。",
        0.82,
        6.22,
        11.5,
        0.4,
        9.5,
        MUTED,
        align="center",
    )

    # 9
    d.new(titles[8], "验证设计", notes[8])
    d.text("每种 ageing mode", 0.72, 1.55, 2.6, 0.34, 14, BLUE, bold=True)
    d.text("→", 3.12, 1.55, 0.4, 0.34, 16, CYAN, bold=True, align="center")
    d.text("3 个 outer condition-group folds", 3.55, 1.55, 4.4, 0.34, 14, INK, bold=True)
    for i in range(3):
        x = 0.75 + i * 4.02
        d.rect(x, 2.25, 3.72, 2.15, WHITE, GRID, 0.12)
        d.badge(f"FOLD {i + 1}", x + 0.22, 2.48, 1.05, PALE_CYAN, BLUE)
        d.text("TRAIN", x + 0.25, 3.04, 1.05, 0.25, 10, GREEN, bold=True)
        d.text("其余 conditions\n拟合 preprocessing + models", x + 1.28, 2.93, 2.1, 0.65, 9, INK)
        d.text("HOLD OUT", x + 0.25, 3.77, 1.05, 0.25, 10, RED, bold=True)
        d.text("完整 condition\n3 cells + 全部 check-ups", x + 1.28, 3.66, 2.1, 0.65, 9, INK)
    d.rect(0.72, 4.86, 11.76, 0.78, PALE_RED, RED, 0.1)
    d.text(
        "禁止 random row / random check-up / fragmented-cell split",
        0.95,
        5.07,
        11.25,
        0.3,
        12,
        RED,
        bold=True,
        align="center",
    )
    d.text(
        "这是 development OOF：模拟“新的 operating condition”，不是 protected external test。",
        0.72,
        6.08,
        11.75,
        0.38,
        10,
        INK,
        bold=True,
        align="center",
    )

    # 10
    d.new(titles[9], "变量选择", notes[9])
    d.rect(0.72, 1.5, 4.0, 4.85, NAVY, NAVY, 0.12)
    d.text("LOCKED", 1.0, 1.9, 3.45, 0.54, 27, CYAN, bold=True, align="center")
    d.text("B: 12 + D_CORE: 54 EIS", 0.88, 2.58, 3.7, 0.35, 13, WHITE, bold=True, align="center")
    d.text(
        "pre-specified features\n+ leakage rules",
        1.0,
        3.22,
        3.45,
        0.72,
        12,
        "BAC8DA",
        align="center",
    )
    d.badge("selection_performed = false", 1.18, 4.4, 3.05, "362B3D", ORANGE)
    d.text(
        "原因：每个 mode 的可用独立 conditions 有限",
        1.0,
        5.14,
        3.45,
        0.52,
        10,
        WHITE,
        bold=True,
        align="center",
    )
    d.card(
        5.08,
        1.5,
        3.55,
        1.35,
        "不按 correlation 筛变量",
        "不使用全数据 outcome 做筛选",
        RED,
        PALE_RED,
    )
    d.card(8.96, 1.5, 3.55, 1.35, "不看 holdout 改变量", "不删除“不好看”的输入", RED, PALE_RED)
    d.card(
        5.08, 3.12, 3.55, 1.35, "ElasticNet shrinkage", "用正则化控制线性复杂度", GREEN, PALE_GREEN
    )
    d.card(8.96, 3.12, 3.55, 1.35, "HGB 固定复杂度", "浅树、有限 leaves", PURPLE, "F1EEFB")
    d.card(
        5.08,
        4.74,
        7.43,
        1.61,
        "没有调参 / ensemble",
        "未来如要筛变量，只能在 condition-grouped inner CV 中重新冻结方案。",
        ORANGE,
        PALE_ORANGE,
    )

    # 11
    d.new(titles[10], "模型对照", notes[10])
    methods = [
        ("Persistence", "未来不下降", "最低门槛", MUTED),
        ("Recent slope", "最近下降速度 × 3", "强透明基线", GREEN),
        ("ElasticNet", "正则化线性回归", "线性组合", BLUE),
        ("HGB", "受约束树模型", "非线性 / 交互", PURPLE),
    ]
    for i, (name, desc, role, col) in enumerate(methods):
        x = 0.7 + i * 3.02
        d.card(x, 1.53, 2.75, 2.65, name, f"{desc}\n\n作用：{role}", col, WHITE)
    d.flow(["3 modes", "3 folds", "B + D_CORE", "54 fits"], 1.03, 4.72, 11.0, 0.92, CYAN, 11)
    d.text(
        "每种 mode：B 与 D_CORE 各 3 folds × 2 fitted models = 12 fits",
        1.0,
        5.92,
        5.45,
        0.3,
        10,
        INK,
        bold=True,
        align="center",
    )
    d.text(
        "Baselines 只在 B 报告；D_CORE 只比较 ElasticNet / HGB",
        6.7,
        5.92,
        5.0,
        0.3,
        10,
        MUTED,
        align="center",
    )
    d.badge(
        "这不是模型大赛：复杂模型必须稳定超过 recent slope 才值得增加复杂度",
        1.35,
        6.45,
        10.65,
        PALE_ORANGE,
        ORANGE,
    )

    # 12
    d.new(titles[11], "可追溯产物", notes[11])
    d.text("2,919", 0.78, 1.62, 2.0, 0.58, 27, BLUE, bold=True, align="center")
    d.text("eligible snapshots", 0.78, 2.23, 2.0, 0.3, 9, MUTED, align="center")
    d.text("×", 2.85, 1.78, 0.42, 0.4, 22, MUTED, bold=True, align="center")
    d.text("6", 3.36, 1.62, 1.2, 0.58, 27, PURPLE, bold=True, align="center")
    d.text("view × model streams", 3.0, 2.23, 1.92, 0.3, 9, MUTED, align="center")
    d.text("=", 5.02, 1.78, 0.42, 0.4, 22, MUTED, bold=True, align="center")
    d.text("23,352", 5.55, 1.62, 2.28, 0.58, 27, GREEN, bold=True, align="center")
    d.text("OOF prediction rows", 5.4, 2.23, 2.6, 0.3, 9, MUTED, align="center")
    d.rect(8.5, 1.5, 4.0, 1.28, PALE_GREEN, GREEN, 0.12)
    d.text("54 / 54", 8.75, 1.78, 1.6, 0.4, 19, GREEN, bold=True)
    d.text("planned / completed fits", 10.33, 1.89, 1.8, 0.28, 9, INK)
    d.flow(
        ["source", "snapshot", "fold", "prediction", "summary", "manifest"],
        0.72,
        3.33,
        11.78,
        0.86,
        BLUE,
        8.5,
    )
    d.text(
        "SHA-256 lineage binds every stage",
        3.5,
        4.38,
        6.3,
        0.34,
        13,
        BLUE,
        bold=True,
        align="center",
    )
    d.card(
        0.72,
        5.02,
        7.52,
        1.3,
        "RUN",
        "kit-f1-paired-b-dcore-dev-v2-20260802T201053-cdt",
        BLUE,
        WHITE,
    )
    d.card(8.55, 5.02, 3.95, 1.3, "MANIFEST", "status = validated", GREEN, PALE_GREEN)
    d.text(
        "validated = 结构 / 数量 / hash / 契约通过；不等于科学发表或生产批准。",
        0.75,
        6.57,
        11.72,
        0.28,
        9.5,
        RED,
        bold=True,
        align="center",
    )

    # 13
    d.new(titles[12], "评估协议", notes[12])
    d.flow(
        ["每个 held-out condition", "计算 MAE", "每个 condition 等权", "跨 condition 平均"],
        0.72,
        1.55,
        11.76,
        0.9,
        BLUE,
        10,
    )
    d.rect(0.72, 2.86, 4.0, 2.45, NAVY, NAVY, 0.12)
    d.text("PRIMARY", 0.98, 3.17, 3.48, 0.26, 10, CYAN, bold=True, align="center")
    d.text("Condition-balanced\nMAE", 0.98, 3.72, 3.48, 0.82, 22, WHITE, bold=True, align="center")
    d.text("单位：SoH pp", 0.98, 4.75, 3.48, 0.28, 10, "B7C6D9", align="center")
    sec = [("Median AE", GREEN), ("RMSE", PURPLE), ("Signed bias", ORANGE), ("R²", BLUE)]
    for i, (name, col) in enumerate(sec):
        x = 5.08 + (i % 2) * 3.78
        y = 2.86 + (i // 2) * 1.32
        d.card(x, y, 3.47, 1.05, name, "secondary", col, WHITE)
    d.rect(5.08, 5.61, 7.25, 0.72, PALE_ORANGE, ORANGE, 0.1)
    d.text(
        "MAE 与 R² 可能排序不同；不能事后挑好看的指标。",
        5.32,
        5.81,
        6.77,
        0.28,
        10,
        ORANGE,
        bold=True,
        align="center",
    )
    d.text(
        "当前运行没有 per-prediction interval。", 0.72, 6.33, 4.0, 0.3, 9.5, MUTED, align="center"
    )

    # 14
    d.new(titles[13], "结果 · Calendar", notes[13])
    labels = ["B\nElastic", "D\nElastic", "B\nHGB", "D\nHGB"]
    mae = [0.209, 0.260, 0.139, 0.138]
    d.bar_chart(0.72, 1.42, 5.7, 3.75, labels, mae, [BLUE, ORANGE, PURPLE, GREEN], 0.32)
    d.metric_table(
        6.72,
        1.52,
        ["View / model", "MAE", "Median AE", "RMSE", "R²"],
        [
            ["B / Elastic", "0.209", "0.121", "0.362", "0.766"],
            ["D / Elastic", "0.260", "0.124", "0.485", "0.580"],
            ["B / HGB", "0.139", "0.058", "0.284", "0.856"],
            ["D / HGB", "0.138", "0.059", "0.283", "0.857"],
        ],
        [1.7, 0.78, 1.05, 0.78, 0.76],
        0.63,
        {(3, 1), (3, 2), (3, 3), (3, 4)},
    )
    d.badge("16 conditions · 48 cells · 1,134 snapshots", 7.28, 4.77, 4.75, PALE_CYAN, INK)
    d.rect(0.72, 5.54, 11.72, 0.82, PALE_GREEN, GREEN, 0.1)
    d.text(
        "D_CORE−B MAE：Elastic +0.051 pp [−0.020, +0.144]；HGB −0.001 pp [−0.013, +0.009]。",
        0.97,
        5.77,
        11.22,
        0.32,
        12,
        GREEN,
        bold=True,
        align="center",
    )
    d.text(
        "不能推导为 protected-test 或真实车队性能。",
        0.72,
        6.55,
        11.72,
        0.25,
        9.5,
        RED,
        bold=True,
        align="center",
    )

    # 15
    d.new(titles[14], "结果 · Cyclic", notes[14])
    rows = [["ElasticNet", "5.113", "5.918", "+0.804"], ["HGB", "5.809", "5.184", "−0.625"]]
    d.metric_table(
        0.72,
        1.52,
        ["模型", "B MAE", "D_CORE MAE", "D − B (pp)"],
        rows,
        [2.1, 1.55, 1.85, 1.85],
        0.7,
        {(1, 3)},
    )
    d.rect(8.05, 1.52, 4.45, 3.5, NAVY, NAVY, 0.12)
    d.text("CYCLIC HGB", 8.33, 1.88, 3.88, 0.34, 14, CYAN, bold=True, align="center")
    d.text("−0.625 pp", 8.34, 2.52, 3.86, 0.46, 22, WHITE, bold=True, align="center")
    d.text("D_CORE − B MAE", 8.34, 3.04, 3.86, 0.26, 8, "AFC1D5", align="center")
    d.line(8.7, 3.53, 11.85, 3.53, "37506F", 1)
    d.text("[−1.056, −0.241]", 8.34, 3.82, 3.86, 0.46, 18, WHITE, bold=True, align="center")
    d.text("paired 95% bootstrap CI", 8.34, 4.34, 3.86, 0.26, 8, "AFC1D5", align="center")
    d.card(
        0.72,
        5.17,
        5.72,
        1.08,
        "CYCLIC SUPPORT",
        "35 conditions · 99 cells · 1,190 matched snapshots",
        GREEN,
        PALE_GREEN,
    )
    d.card(
        6.77,
        5.17,
        5.73,
        1.08,
        "ElasticNet warning",
        "D_CORE worsened MAE +0.804 pp [ +0.285, +1.361 ]",
        PURPLE,
        "F1EEFB",
    )
    d.text(
        "同一 EIS 表示在不同模型里给出相反方向：这是 model-dependent diagnostic，而不是 EIS 总结论。",
        0.72,
        6.52,
        11.78,
        0.28,
        10.5,
        ORANGE,
        bold=True,
        align="center",
    )

    # 16
    d.new(titles[15], "结果 · Driving profile 与 EIS结论", notes[15])
    reasons = [
        ("01", "Profile / Elastic", "D_CORE−B = −0.330 pp；95% CI [−1.208, +0.433]，跨 0。"),
        ("02", "Profile / HGB", "D_CORE−B = +0.215 pp；95% CI [+0.036, +0.384]。"),
        ("03", "Calendar / HGB", "D_CORE−B = −0.001 pp；95% CI [−0.013, +0.009]，没有清楚增量。"),
        ("04", "Cyclic / HGB", "D_CORE−B = −0.625 pp；95% CI [−1.056, −0.241]。"),
        (
            "05",
            "解释边界",
            "Profile 只有 11 eligible conditions；temperature/EIS context仍有残余限制。",
        ),
    ]
    for i, (num, title, body) in enumerate(reasons):
        y = 1.48 + i * 0.93
        d.circle(0.75, y + 0.04, 0.52, [BLUE, GREEN, PURPLE, ORANGE, RED][i])
        d.text(num, 0.75, y + 0.16, 0.52, 0.2, 8.5, WHITE, bold=True, align="center")
        d.text(title, 1.46, y, 2.0, 0.3, 11, INK, bold=True)
        d.text(body, 3.25, y, 9.05, 0.48, 9.5, MUTED)
    d.rect(0.72, 6.24, 11.76, 0.64, NAVY, NAVY, 0.08)
    d.text(
        "唯一稳妥结论：D_CORE/EIS 的表现依赖 ageing mode 和 model family；未做 selection 或 promotion。",
        0.95,
        6.42,
        11.3,
        0.28,
        11.5,
        WHITE,
        bold=True,
        align="center",
    )

    # 17
    d.new(titles[16], "证据边界", notes[16])
    limitations = [
        ("NO", "protected / external test"),
        ("NO", "publication / resume approval"),
        ("NO", "O2 / failure classification"),
        ("NO", "pulse / auxiliary diagnostics"),
        ("NO", "RUL / prediction interval"),
        ("NO", "maintenance / production proof"),
    ]
    for i, (flag, label) in enumerate(limitations):
        x = 0.72 + (i % 3) * 4.02
        y = 1.5 + (i // 3) * 1.48
        d.rect(x, y, 3.7, 1.12, WHITE, GRID, 0.1)
        d.badge(flag, x + 0.18, y + 0.2, 0.75, PALE_RED, RED)
        d.text(label, x + 1.05, y + 0.29, 2.35, 0.48, 10, INK, bold=True, valign="middle")
    d.rect(0.72, 4.77, 11.75, 1.25, "2A1C25", "2A1C25", 0.12)
    d.text(
        "scientific_result = false", 0.98, 5.02, 3.35, 0.32, 12, ORANGE, bold=True, align="center"
    )
    d.text(
        "formal_model_result = false", 4.55, 5.02, 3.35, 0.32, 12, ORANGE, bold=True, align="center"
    )
    d.text(
        "publication_ready = false", 8.12, 5.02, 3.85, 0.32, 12, ORANGE, bold=True, align="center"
    )
    d.text(
        "paired development delta ≠ 最终 EIS 增量结论",
        0.72,
        6.37,
        11.75,
        0.34,
        12,
        RED,
        bold=True,
        align="center",
    )

    # 18
    d.new(titles[17], "产品表达", notes[17])
    d.flow(
        [
            "228-cell roster",
            "选择 ageing mode",
            "沿 check-up 回放",
            "当前 SoH + 历史",
            "H3 prediction",
            "truth 到期解锁",
        ],
        0.72,
        1.58,
        11.76,
        0.9,
        BLUE,
        8.7,
    )
    d.rect(0.72, 2.91, 11.76, 2.38, WHITE, GRID, 0.12)
    d.text("PAST / AVAILABLE", 0.98, 3.2, 2.1, 0.24, 9, BLUE, bold=True)
    d.line(1.02, 3.9, 11.95, 3.9, GRID, 2)
    for i, (lab, col) in enumerate(
        [
            ("t−2", BLUE),
            ("t−1", BLUE),
            ("t NOW", GREEN),
            ("t+1", MUTED),
            ("t+2", MUTED),
            ("t+3 TRUTH", PURPLE),
        ]
    ):
        x = 1.05 + i * 2.08
        d.circle(x, 3.72, 0.36, col)
        d.text(lab, x - 0.42, 4.2, 1.2, 0.3, 8.5, col, bold=True, align="center")
    d.text("prediction issued", 4.2, 3.18, 2.2, 0.24, 9, GREEN, bold=True, align="center")
    d.text(
        "truth locked until available", 9.7, 3.18, 2.2, 0.24, 9, PURPLE, bold=True, align="center"
    )
    d.badge("insufficient_history", 1.02, 5.68, 2.42, PALE_ORANGE, ORANGE)
    d.badge("no_h3_followup", 3.75, 5.68, 2.42, PALE_RED, RED)
    d.badge("development replay", 6.48, 5.68, 2.42, PALE_CYAN, BLUE)
    d.badge("final mode disabled", 9.21, 5.68, 2.42, "EFEAF8", PURPLE)
    d.text(
        "UI 必须区分预测、实测 truth 与不可用状态；不能制造“数字孪生”确定性。",
        0.72,
        6.45,
        11.76,
        0.3,
        10,
        INK,
        bold=True,
        align="center",
    )

    # 19
    d.new(titles[18], "业务价值假设", notes[18])
    d.flow(
        [
            "scheduled check-up",
            "future-loss estimate",
            "evidence packet",
            "prioritize retest",
            "engineering review",
            "human decision",
        ],
        0.72,
        1.52,
        11.76,
        0.88,
        GREEN,
        8.8,
    )
    items = [
        ("把注意力放在快速衰减或证据异常电芯", BLUE),
        ("同时呈现 current health、future estimate、数据质量与来源", GREEN),
        ("记录每次复测、审查和人工决定", PURPLE),
        ("用 missed cases、review burden、lead time、成本做前瞻评估", ORANGE),
    ]
    for i, (body, col) in enumerate(items):
        x = 0.72 + (i % 2) * 6.0
        y = 2.85 + (i // 2) * 1.42
        d.card(x, y, 5.72, 1.12, f"{i + 1:02d}", body, col, WHITE)
    d.rect(0.72, 5.91, 11.74, 0.66, PALE_RED, RED, 0.1)
    d.text(
        "模型不直接换电池；“是否省钱、是否少漏检”都尚未验证。",
        0.95,
        6.09,
        11.28,
        0.3,
        11.5,
        RED,
        bold=True,
        align="center",
    )

    # 20
    d.new(titles[19], "下一轮实验", notes[19])
    experiments = [
        ("01", "错误分析", "解释 slope 胜出的 conditions", RED),
        ("02", "Horizon sensitivity", "新 protocol 下比较 H1/H3/H5", BLUE),
        ("03", "EIS incremental", "同 snapshot / fold / model 比 B vs D_CORE", PURPLE),
        ("04", "变量稳定性", "condition-grouped inner CV", GREEN),
        ("05", "外部验证", "冻结后申请一次 protected evaluation", ORANGE),
        ("06", "O2 / noise", "先取得 capacity technical repeats", CYAN),
    ]
    for i, (num, title, body, col) in enumerate(experiments):
        x = 0.72 + (i % 2) * 6.0
        y = 1.47 + (i // 2) * 1.58
        d.rect(x, y, 5.72, 1.28, WHITE, GRID, 0.1)
        d.circle(x + 0.2, y + 0.25, 0.55, col)
        d.text(num, x + 0.2, y + 0.4, 0.55, 0.2, 8.5, WHITE, bold=True, align="center")
        d.text(title, x + 0.96, y + 0.18, 2.3, 0.3, 11, INK, bold=True)
        d.text(body, x + 0.96, y + 0.63, 4.42, 0.38, 9, MUTED)
    d.rect(0.72, 6.35, 11.74, 0.48, NAVY, NAVY, 0.08)
    d.text(
        "先解释失败模式，再检验 EIS 增量，最后讨论选择、告警与部署。",
        0.94,
        6.46,
        11.3,
        0.25,
        10.5,
        WHITE,
        bold=True,
        align="center",
    )

    # 21
    d.new(titles[20], "结论", notes[20], dark=True)
    d.text("我们完成了一个可审计的第一步", 0.75, 1.47, 6.0, 0.5, 22, WHITE, bold=True)
    done = [
        "228 cells / 76 conditions inventory",
        "exact-H3 target + prefix-only B and paired D_CORE views",
        "54-fit condition-held-out paired development run",
        "2,919 matched snapshots · 23,352 OOF rows",
        "EIS增量依赖 mode / model，而不是全局赢家",
    ]
    for i, item in enumerate(done):
        y = 2.18 + i * 0.68
        d.circle(0.78, y + 0.08, 0.25, GREEN)
        d.text(str(i + 1), 0.78, y + 0.1, 0.25, 0.18, 7, WHITE, bold=True, align="center")
        d.text(item, 1.18, y, 5.3, 0.38, 10.5, WHITE, bold=True)
    d.rect(7.18, 1.48, 5.38, 3.85, "18273D", "2C4565", 0.12)
    d.text("仍未完成", 7.55, 1.82, 4.64, 0.34, 16, ORANGE, bold=True)
    for i, item in enumerate(
        [
            "protected / external test",
            "模型选择",
            "EIS replication",
            "O2 告警",
            "生产影响",
            "公开发布批准",
        ]
    ):
        yy = 2.48 + i * 0.43
        d.circle(7.62, yy + 0.11, 0.13, ORANGE)
        d.text(item, 7.91, yy, 3.95, 0.35, 10.5, WHITE)
    d.rect(0.75, 5.89, 11.8, 0.75, "112F43", "112F43", 0.1)
    d.text(
        "可靠的研究结论，有时是知道简单方法在哪里更强。",
        1.0,
        6.1,
        11.3,
        0.34,
        14,
        CYAN,
        bold=True,
        align="center",
    )

    d.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--preview-dir", type=Path, default=PREVIEW_DIR)
    args = parser.parse_args()
    build(args.output.resolve(), args.preview_dir.resolve())
    print(args.output.resolve())
    print(args.preview_dir.resolve())


if __name__ == "__main__":
    main()
