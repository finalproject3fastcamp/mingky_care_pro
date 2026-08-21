"""Pure demand policy for front-camera power control."""


def camera_demanded(
    *, scan_enabled: bool, preview_enabled: bool, image_topic_enabled: bool,
) -> bool:
    """Return whether any QR, operator, or safety consumer needs frames."""
    return scan_enabled or preview_enabled or image_topic_enabled
