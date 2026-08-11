"""LCD image renderer tests."""

from mingky_lcd_status.renderer import HEIGHT, WIDTH, render_view
from mingky_lcd_status.view_model import DisplayView


def test_renderer_creates_lcd_sized_rgb_image():
    """Renderer output matches the landscape input expected by the driver."""
    image = render_view(DisplayView(
        eyebrow='안내 중',
        title='CT로 이동합니다',
        route_from='X-ray',
        route_to='CT',
        instruction='로봇을 따라와 주세요',
        accent='green',
    ))

    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == 'RGB'
