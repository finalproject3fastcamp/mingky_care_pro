from mingky_qr_reader.camera_power_policy import camera_demanded


def test_camera_demand_combines_qr_preview_and_safety_consumers():
    assert camera_demanded(
        scan_enabled=True, preview_enabled=False, image_topic_enabled=False)
    assert camera_demanded(
        scan_enabled=False, preview_enabled=True, image_topic_enabled=False)
    assert camera_demanded(
        scan_enabled=False, preview_enabled=False, image_topic_enabled=True)
    assert not camera_demanded(
        scan_enabled=False, preview_enabled=False, image_topic_enabled=False)
