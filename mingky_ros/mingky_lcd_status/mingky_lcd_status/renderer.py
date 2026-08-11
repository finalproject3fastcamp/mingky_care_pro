"""320x240 Pinky LCD 화면 렌더러."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .view_model import DisplayView

WIDTH = 320
HEIGHT = 240

_FONT_CANDIDATES = (
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)

_COLORS = {
    'blue': '#2563EB',
    'green': '#16A34A',
    'yellow': '#D97706',
    'red': '#DC2626',
}


def resolve_font_path(explicit: str = '') -> str:
    """Return the configured font or an installed Korean-capable font."""
    candidates = (
        (explicit, *_FONT_CANDIDATES) if explicit else _FONT_CANDIDATES
    )
    return next((path for path in candidates if Path(path).is_file()), '')


def _font(path: str, size: int):
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def render_view(view: DisplayView, font_path: str = '') -> Image.Image:
    """LCD 드라이버가 회전하기 전의 320x240 RGB 이미지를 만든다."""
    path = resolve_font_path(font_path)
    image = Image.new('RGB', (WIDTH, HEIGHT), '#F8FAFC')
    draw = ImageDraw.Draw(image)
    accent = _COLORS.get(view.accent, _COLORS['blue'])

    draw.rounded_rectangle(
        (10, 10, WIDTH - 10, HEIGHT - 10), 16, fill='white')
    draw.rounded_rectangle((10, 10, 18, HEIGHT - 10), 4, fill=accent)
    draw.text((32, 24), view.eyebrow, font=_font(path, 15), fill=accent)
    draw.text((32, 51), view.title, font=_font(path, 24), fill='#0F172A')

    if view.route_to:
        route_from = view.route_from or '현재 위치'
        draw.rounded_rectangle(
            (28, 96, WIDTH - 28, 157), 10, fill='#EFF6FF')
        draw.text((42, 106), route_from, font=_font(path, 17), fill='#475569')
        draw.text((145, 106), '→', font=_font(path, 20), fill=accent)
        draw.text(
            (178, 106), view.route_to,
            font=_font(path, 19), fill='#0F172A')

    if view.instruction:
        draw.text(
            (32, 190), view.instruction,
            font=_font(path, 15), fill='#475569')

    return image
