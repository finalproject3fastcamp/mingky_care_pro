# Pinky 웹 모델 생성

웹 카드에서 사용하는 `frontend/public/models/pinky.glb`는 ROS 시각화 모델을
하나의 GLB로 합친 뒤 meshopt로 압축한 결과물이다.

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash

python3 -m venv /tmp/pinky-web-model
/tmp/pinky-web-model/bin/pip install -r tools/pinky_web_model/requirements.txt

xacro pinky/pinky_description/urdf/pinky.urdf.xacro -o /tmp/pinky-web.urdf
/tmp/pinky-web-model/bin/python tools/pinky_web_model/export_glb.py \
  /tmp/pinky-web.urdf /tmp/pinky-web.glb

cd frontend
npx @gltf-transform/cli optimize /tmp/pinky-web.glb \
  public/models/pinky.glb --compress meshopt --texture-compress webp
```

Three.js 로더에는 `MeshoptDecoder`가 연결되어 있어야 최적화된 모델을
열 수 있다.
