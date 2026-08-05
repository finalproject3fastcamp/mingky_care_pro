# Nav2 실험 프로파일

여기에는 `nav2_params.yaml`의 일부를 덮어쓰는 실험용 YAML을 둡니다.

프로파일 하나에는 검증하려는 파라미터 묶음 하나만 넣습니다. 예를 들면:

- `01_costmap_resolution_0025.yaml`
- `02_inflation_radius_020.yaml`
- `03_inflation_radius_025.yaml`
- `04_cost_scaling_025.yaml`

실험 실행 도구가 기본 `nav2_params.yaml`과 프로파일을 합쳐 실험별 유효 설정을
만듭니다. 유효 설정 원문과 해시는 SQLite에 함께 기록됩니다.
