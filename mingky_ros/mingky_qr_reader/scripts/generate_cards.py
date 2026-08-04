#!/usr/bin/env python3
"""환자 카드용 고해상도 QR PNG + A4 인쇄용 PDF 생성.

- samples/{pid}.png       : 870×870 QR (ECC=H, box_size=30)
- samples/cards.pdf       : A4에 85×54mm 카드 3장 (컷 마크 포함)

usage:
    pip install -r scripts/requirements.txt
    python scripts/generate_cards.py
"""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_H

PATIENTS = [
    ("p001", "윤동수"),
    ("p002", "권민수"),
    ("p003", "김지우"),
]

BASE = Path(__file__).resolve().parent.parent
SAMPLES = BASE / "samples"

DPI = 300
MM_PER_INCH = 25.4


def mm(value: float) -> int:
    return round(value * DPI / MM_PER_INCH)


CARD_W, CARD_H = mm(85), mm(54)
A4_W, A4_H = mm(210), mm(297)
CARD_GAP = mm(10)

KOREAN_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_korean_font() -> str | None:
    for path in KOREAN_FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def make_qr(patient_id: str, box_size: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_H,
        box_size=box_size,
        border=4,
    )
    qr.add_data(patient_id)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def make_qr_fitting(patient_id: str, target_max_px: int) -> Image.Image:
    """target_max_px 안에 정확히 들어가는 box_size로 QR 생성 (모듈 왜곡 없음)."""
    probe = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=4, box_size=1)
    probe.add_data(patient_id)
    probe.make(fit=True)
    modules_total = probe.modules_count + 2 * probe.border
    box_size = max(target_max_px // modules_total, 1)
    return make_qr(patient_id, box_size=box_size)


def compose_card(
    patient_id: str,
    name: str,
    font_path: str | None,
) -> Image.Image:
    card = Image.new("RGB", (CARD_W, CARD_H), "white")
    draw = ImageDraw.Draw(card)
    draw.rectangle([(1, 1), (CARD_W - 2, CARD_H - 2)], outline="black", width=2)

    padding = mm(3)
    max_qr = CARD_H - 2 * padding
    qr_img = make_qr_fitting(patient_id, target_max_px=max_qr)
    qr_size = qr_img.size[0]
    qr_y = (CARD_H - qr_size) // 2
    card.paste(qr_img, (padding, qr_y))

    text_x = padding + qr_size + mm(5)
    text_right = CARD_W - padding
    text_area = text_right - text_x

    if font_path is None:
        name_font = ImageFont.load_default()
        id_font = ImageFont.load_default()
    else:
        name_font = ImageFont.truetype(font_path, mm(6))
        id_font = ImageFont.truetype(font_path, mm(4))

    def draw_fitted(text: str, y: int, font: ImageFont.FreeTypeFont, fill: str) -> None:
        active = font
        if font_path is not None:
            size = active.size
            while draw.textlength(text, font=active) > text_area and size > 8:
                size -= 1
                active = ImageFont.truetype(font_path, size)
        draw.text((text_x, y), text, fill=fill, font=active)

    draw_fitted(name, mm(15), name_font, "black")
    draw_fitted(f"ID: {patient_id}", mm(28), id_font, "black")
    draw_fitted("Mingky Care Pro", mm(38), id_font, "gray")
    return card


def compose_a4_sheet(cards: list[Image.Image]) -> Image.Image:
    page = Image.new("RGB", (A4_W, A4_H), "white")
    draw = ImageDraw.Draw(page)

    total_h = len(cards) * CARD_H + (len(cards) - 1) * CARD_GAP
    x = (A4_W - CARD_W) // 2
    y = (A4_H - total_h) // 2

    mark = mm(4)
    for card in cards:
        page.paste(card, (x, y))
        for cx, cy in [(x, y), (x + CARD_W, y), (x, y + CARD_H), (x + CARD_W, y + CARD_H)]:
            draw.line([(cx - mark, cy), (cx + mark, cy)], fill="lightgray", width=1)
            draw.line([(cx, cy - mark), (cx, cy + mark)], fill="lightgray", width=1)
        y += CARD_H + CARD_GAP

    return page


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    font_path = find_korean_font()
    if font_path is None:
        print("경고: 한글 폰트를 찾지 못했습니다. 기본 폰트로 대체 (한글이 깨질 수 있음).")
        print("      Ubuntu 이면 'sudo apt install fonts-nanum' 을 권장.")
    else:
        print(f"한글 폰트: {font_path}")

    cards = []
    for pid, name in PATIENTS:
        standalone = make_qr(pid, box_size=30)
        png_path = SAMPLES / f"{pid}.png"
        standalone.save(png_path)
        print(f"QR PNG 저장: {png_path.relative_to(BASE)} ({standalone.size[0]}px)")
        cards.append(compose_card(pid, name, font_path))

    sheet = compose_a4_sheet(cards)
    pdf_path = SAMPLES / "cards.pdf"
    sheet.save(pdf_path, "PDF", resolution=float(DPI))
    print(f"PDF 저장: {pdf_path.relative_to(BASE)}")


if __name__ == "__main__":
    main()
