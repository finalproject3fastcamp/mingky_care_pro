PRAGMA foreign_keys = ON;

-- 한 번의 지도·파라미터·목표 조합 실험.
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'succeeded', 'failed', 'aborted', 'cancelled')),
  label TEXT,
  robot_name TEXT,
  ros_domain_id INTEGER,
  git_commit TEXT,
  map_path TEXT NOT NULL,
  map_sha256 TEXT NOT NULL,
  profile_path TEXT,
  profile_sha256 TEXT,
  notes TEXT
);

-- 실험 시작·종료 시점의 실제 Nav2 파라미터. JSON 값으로 타입을 보존한다.
CREATE TABLE parameter_snapshots (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  captured_at TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('start', 'end')),
  node_name TEXT NOT NULL,
  parameters_json TEXT NOT NULL
);

-- RViz 또는 action client가 전송한 Nav2 목표와 최종 결과.
CREATE TABLE goals (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  action_goal_id TEXT,
  requested_at TEXT NOT NULL,
  finished_at TEXT,
  x REAL NOT NULL,
  y REAL NOT NULL,
  yaw REAL NOT NULL,
  result TEXT,
  message TEXT
);

-- planner/controller/recovery/rosout에서 추린 구조화 이벤트.
CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  recorded_at TEXT NOT NULL,
  level TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT,
  details_json TEXT
);

-- 2~5Hz의 가벼운 주행 요약. 원본 토픽은 rosbag에 보관한다.
CREATE TABLE samples (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  recorded_at TEXT NOT NULL,
  pose_x REAL,
  pose_y REAL,
  pose_yaw REAL,
  pose_covariance_xy REAL,
  odom_linear_x REAL,
  odom_angular_z REAL,
  cmd_linear_x REAL,
  cmd_angular_z REAL,
  scan_min_range REAL,
  global_plan_length REAL,
  local_plan_length REAL
);

-- rosbag, Foxglove 레이아웃, 스크린샷 등 대형 결과물은 경로만 연결한다.
CREATE TABLE artifacts (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT
);

CREATE INDEX idx_goals_run_id ON goals(run_id);
CREATE INDEX idx_events_run_id_time ON events(run_id, recorded_at);
CREATE INDEX idx_samples_run_id_time ON samples(run_id, recorded_at);
