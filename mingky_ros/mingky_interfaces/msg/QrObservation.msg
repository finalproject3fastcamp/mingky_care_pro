# 후방 카메라에서 관측한 QR 한 개의 현재 상태.
# 이벤트가 아니라 매 프레임 덮어쓰는 실시간 센서 값이다.
std_msgs/Header header
bool visible
string data
float32 distance
# YOLO 박스와 현재 환자 QR을 결합할 때 쓰는 영상 픽셀 중심.
float32 center_x
float32 center_y
