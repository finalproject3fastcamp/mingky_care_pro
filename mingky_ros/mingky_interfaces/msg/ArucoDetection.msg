# 단일 ArUco 마커의 카메라 optical frame 기준 검출 결과.

int32 marker_id
geometry_msgs/Pose pose

# 카메라 원점에서 마커 중심까지의 3차원 직선거리(m).
float32 distance

# optical frame 기준 전방 거리(+z)와 좌우 오프셋(+x는 영상 오른쪽).
float32 forward_distance
float32 lateral_offset

# 검출 코너와 추정 자세를 다시 투영했을 때의 평균 오차(px).
float32 reprojection_error
