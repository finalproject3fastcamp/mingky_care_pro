# mingky_dwell_guard

환자를 놓친 채 너무 오래 서 있으면 안내를 접고 충전소로 보낸다.

## 왜 필요한가

로봇은 환자를 놓치면 그 자리에서 **무기한** 기다린다. 자리를 지키는 것 자체는
옳다 — 옮기면 돌아온 환자가 로봇을 못 찾는다. 다만 아무도 안 오는 경우가 있고,
그러면 로봇이 복도를 계속 막고 서 있는다. 그 마지막 한 걸음만 여기서 더한다.

## 어떻게

이미 열려 있는 문을 두드린다. 의료진이 관제에서 안내를 취소할 때 쓰는 토픽에
같은 요청을 보낼 뿐이고, **기존 노드는 한 줄도 고치지 않는다.**

```
/person_follow/state 가 waiting 으로 N초 유지
    → /guide_manager/cancel_session  {"reason":"aborted","session_id":N}
    → mingky_guide_manager 가 세션을 끝내고 충전소로 복귀
```

| 방향 | 토픽 | 형식 |
|---|---|---|
| 듣기 | `/person_follow/state` | `std_msgs/String` |
| 듣기 | `/guide_manager/state` | `mingky_interfaces/GuideState` (TRANSIENT_LOCAL) |
| 보내기 | `/guide_manager/cancel_session` | `std_msgs/String` (JSON) |

## 파라미터

| 이름 | 기본값 | 뜻 |
|---|---|---|
| `enabled` | `true` | `false` 면 아무것도 하지 않는다 |
| `timeout_sec` | `180.0` | 포기까지 기다릴 시간. 시연은 짧게, 운영은 길게 |
| `notice_every_sec` | `30.0` | 남은 시간 로그 간격. `0` 이면 안 알림 |

```bash
ros2 run mingky_dwell_guard dwell_guard_node --ros-args -p timeout_sec:=15.0
```

## 알아 둘 것

**취소는 세션을 중단시킨다.** 그 세션은 `session.ended{end_reason:aborted}` 로
기록되고 **안내 완주율에 실패로 잡힌다.** 로봇이 포기하고 돌아간 것은 실제로
완주 실패이므로 그게 맞다고 보지만, 지표가 움직이는 일이라 팀이 알아야 한다.

사유를 따로 만들 수는 없다. 받는 쪽이 `aborted`/`robot_offline`/`system_failure`
만 받고 나머지는 버린다. 새 사유를 넣으려면 `mingky_guide_manager` 를 고쳐야 한다.

## 안전장치

- **안내 중일 때만** 개입한다. 세션이 없거나 끝났으면 아무것도 안 한다
- **한 번만** 보낸다. 시간이 지난 뒤에도 상태는 계속 `waiting` 이라, 매번 보내면
  취소 요청이 초당 수십 번 나간다
- 환자가 돌아오면 **처음부터 다시** 센다. 짧게 여러 번 놓친 것을 합산하면 잘
  따라오는데도 안내를 접게 된다
- 시계가 뒤로 가면 다시 센다. 실제보다 일찍 포기하지 않는다

## 되돌리기

노드를 안 띄우면 된다. 예전처럼 무기한 기다리는 동작으로 돌아갈 뿐이다.
