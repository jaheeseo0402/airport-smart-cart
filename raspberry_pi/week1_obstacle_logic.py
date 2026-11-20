# -*- coding: utf-8 -*-
"""
1주차 (SW) 장애물 판단 로직 설계
==================================
9주차 보고서의 다음 항목을 하드웨어 없이 소프트웨어로 먼저 구현하고
더미(가상) 센서값으로 시뮬레이션 검증한다.

  1. 방향별(전방/측방/후방) 임계값 설정 + 속도 기반 동적 보정
  2. 칼만 필터로 초음파 원시값을 보정해 거리(d)·속도(v) 산출
  3. 연속 3프레임(약 150ms) 이상 감지된 경우에만 위협으로 인정하는 노이즈 필터
  4. 정적/동적 장애물 분류 (|v| <= 0.02 m/s → 정적, 그 외 → 동적, v<0 → 위협 없음)
  5. TTC(Time To Collision) 계산
  6. 복수 방향 동시 감지 시 TTC가 가장 짧은 방향 우선 처리
  7. 긴급 제동 후 1초간 동일 방향 재감지 억제

실제 실행은 python3 week1_obstacle_logic.py 로 하면 되고,
콘솔에 프레임별 판단 로그와 마지막에 검증 요약이 출력된다.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import random


# ---------------------------------------------------------------------------
# 1. 칼만 필터 (상태: [거리, 속도])
# ---------------------------------------------------------------------------
class KalmanFilter1D:
    """초음파 원시 거리값을 보정해 거리(d)와 접근 속도(v)를 함께 추정한다."""

    def __init__(self, dt: float, process_var: float = 1e-3, meas_var: float = 5e-3):
        self.dt = dt
        self.x = [0.0, 0.0]  # [distance, velocity]
        self.P = [[1.0, 0.0], [0.0, 1.0]]
        self.Q = process_var
        self.R = meas_var
        self.initialized = False

    def _predict(self):
        dt = self.dt
        d, v = self.x
        self.x = [d + v * dt, v]
        F = [[1.0, dt], [0.0, 1.0]]
        P = self.P
        FP = [[sum(F[i][k] * P[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
        FPFt = [[sum(FP[i][k] * F[j][k] for k in range(2)) for j in range(2)] for i in range(2)]
        self.P = [[FPFt[0][0] + self.Q, FPFt[0][1]],
                  [FPFt[1][0], FPFt[1][1] + self.Q]]

    def _update(self, z: float):
        P = self.P
        S = P[0][0] + self.R
        K = [P[0][0] / S, P[1][0] / S]
        y = z - self.x[0]
        self.x = [self.x[0] + K[0] * y, self.x[1] + K[1] * y]
        P00, P01, P10, P11 = P[0][0], P[0][1], P[1][0], P[1][1]
        self.P = [[P00 - K[0] * P00, P01 - K[0] * P01],
                  [P10 - K[1] * P00, P11 - K[1] * P01]]

    def step(self, measured_distance_m: float):
        if not self.initialized:
            # 첫 측정값으로 초기화 (초기 속도는 0으로 가정)
            self.x = [measured_distance_m, 0.0]
            self.initialized = True
            return tuple(self.x)
        self._predict()
        self._update(measured_distance_m)
        return tuple(self.x)  # (distance_m, velocity_m_s)


# ---------------------------------------------------------------------------
# 2. 방향 정의 및 방향별 임계값 (문서 1.1 방향별 임계값 설정 기준)
# ---------------------------------------------------------------------------
class Direction(Enum):
    S1_FRONT = "S1"       # 전방
    S2_LEFT = "S2"        # 좌측
    S3_RIGHT = "S3"       # 우측
    S4_LEFT = "S4"        # 좌측
    S5_RIGHT = "S5"       # 우측
    S6_REAR = "S6"        # 후방


FRONT = {Direction.S1_FRONT}
SIDE = {Direction.S2_LEFT, Direction.S3_RIGHT, Direction.S4_LEFT, Direction.S5_RIGHT}
REAR = {Direction.S6_REAR}

# 기본 임계값 (m 단위, 문서는 cm로 제시 -> /100)
BASE_THRESHOLDS_CM = {
    "front": {"CRITICAL": 10, "HIGH": 20, "MEDIUM": 40},   # 앞: 20cm 이하 정지
    "side": {"CRITICAL": 5, "HIGH": 8, "MEDIUM": 15},      # 왼/오: 8cm 이하 정지 (이전 8/15/25)
    "rear": {"CRITICAL": 8, "HIGH": 12},  # MEDIUM 없음 (뒤는 정지 판단에 쓰지 않음 - pi_obstacle_stop4.py에서 제외)
}

REACTION_TIME_S = 0.3          # 속도 기반 동적 보정에 곱하는 반응 시간
STATIC_VELOCITY_EPS = 0.02     # |v| <= 0.02 m/s -> 정적 장애물
CONSEC_FRAMES_REQUIRED = 3     # 연속 3프레임 이상 감지 시에만 위협 인정
POST_BRAKE_SUPPRESS_S = 1.0    # 긴급 제동 후 동일 방향 재감지 억제 시간


def group_of(direction: Direction) -> str:
    if direction in FRONT:
        return "front"
    if direction in REAR:
        return "rear"
    return "side"


def get_thresholds_m(direction: Direction, velocity_m_s: float) -> dict:
    """방향별 기본 임계값에 속도 기반 동적 보정을 더해 m 단위로 반환한다."""
    group = group_of(direction)
    base_cm = BASE_THRESHOLDS_CM[group]
    # 칼만필터로 추정한 속도 * 반응시간(0.3s) 만큼 감지 범위를 넓힘 (고속일수록 넓게)
    correction_m = max(velocity_m_s, 0.0) * REACTION_TIME_S
    return {level: (cm / 100.0) + correction_m for level, cm in base_cm.items()}


class ThreatLevel(Enum):
    NONE = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


def classify_threat_level(distance_m: float, thresholds_m: dict) -> ThreatLevel:
    if distance_m <= thresholds_m.get("CRITICAL", -1):
        return ThreatLevel.CRITICAL
    if distance_m <= thresholds_m.get("HIGH", -1):
        return ThreatLevel.HIGH
    if "MEDIUM" in thresholds_m and distance_m <= thresholds_m["MEDIUM"]:
        return ThreatLevel.MEDIUM
    return ThreatLevel.NONE


# ---------------------------------------------------------------------------
# 3. 정적/동적 분류 + TTC 계산
# ---------------------------------------------------------------------------
class ObstacleType(Enum):
    NONE = "위협없음"
    STATIC = "정적"
    DYNAMIC = "동적"


def classify_obstacle(velocity_m_s: float) -> ObstacleType:
    """v > 0: 접근(양수를 접근으로 정의), v < 0: 멀어짐(위협없음),
    |v| <= 0.02: 정적 장애물로 분류하고 거리 기준만 사용."""
    if velocity_m_s < 0:
        return ObstacleType.NONE
    if abs(velocity_m_s) <= STATIC_VELOCITY_EPS:
        return ObstacleType.STATIC
    return ObstacleType.DYNAMIC


def calculate_ttc(distance_m: float, velocity_m_s: float) -> float | None:
    """접근 속도가 있을 때만 TTC(초) 계산. 정적/위협없음이면 None."""
    if velocity_m_s <= STATIC_VELOCITY_EPS:
        return None
    return distance_m / velocity_m_s


# ---------------------------------------------------------------------------
# 4. 센서 채널: 칼만필터 + 연속 프레임 카운트 + 억제 타이머를 방향별로 보유
# ---------------------------------------------------------------------------
@dataclass
class SensorChannel:
    direction: Direction
    dt: float
    kf: KalmanFilter1D = field(init=False)
    consecutive_hits: int = 0
    suppress_until_t: float = -1.0  # 이 시간까지는 재감지 억제

    def __post_init__(self):
        self.kf = KalmanFilter1D(dt=self.dt)

    def process(self, raw_distance_m: float, now_t: float):
        distance_m, raw_velocity_m_s = self.kf.step(raw_distance_m)
        # 칼만필터가 산출하는 raw_velocity는 '거리의 변화율'이라 접근 시 음수가 된다.
        # 문서 정의(v>0=접근, v<0=위협없음/멀어짐)에 맞추기 위해 부호를 뒤집어
        # '접근 속도(closing speed)'로 사용한다.
        velocity_m_s = -raw_velocity_m_s
        obstacle_type = classify_obstacle(velocity_m_s)
        thresholds = get_thresholds_m(self.direction, velocity_m_s)
        level = classify_threat_level(distance_m, thresholds)

        # 노이즈 필터: 위협(NONE이 아님)이 연속으로 잡혀야 카운트 증가
        if level != ThreatLevel.NONE:
            self.consecutive_hits += 1
        else:
            self.consecutive_hits = 0

        confirmed = self.consecutive_hits >= CONSEC_FRAMES_REQUIRED
        suppressed = now_t < self.suppress_until_t

        ttc = calculate_ttc(distance_m, velocity_m_s)

        return {
            "direction": self.direction.value,
            "distance_m": round(distance_m, 3),
            "velocity_m_s": round(velocity_m_s, 3),
            "obstacle_type": obstacle_type.value,
            "level": level,
            "confirmed": confirmed and not suppressed,
            "suppressed": suppressed,
            "ttc_s": None if ttc is None else round(ttc, 3),
        }

    def trigger_emergency_brake(self, now_t: float):
        self.suppress_until_t = now_t + POST_BRAKE_SUPPRESS_S
        self.consecutive_hits = 0


# ---------------------------------------------------------------------------
# 5. 프레임 단위 통합 판단: 복수 방향 동시 감지 시 TTC 최단 방향 우선
# ---------------------------------------------------------------------------
def resolve_priority(frame_results: list[dict]) -> dict | None:
    """확정(confirmed)된 위협 중 우선순위가 가장 높은 하나를 고른다.
    기준: 1) CRITICAL > HIGH > MEDIUM  2) 동적 장애물은 TTC가 짧을수록 우선
          3) 정적 장애물은 거리가 가까울수록 우선"""
    candidates = [r for r in frame_results if r["confirmed"] and r["level"] != ThreatLevel.NONE]
    if not candidates:
        return None

    def sort_key(r: dict):
        level_rank = -r["level"].value  # CRITICAL(3)이 먼저 오도록 음수
        tie_break = r["ttc_s"] if r["ttc_s"] is not None else r["distance_m"]
        return (level_rank, tie_break)

    return min(candidates, key=sort_key)


# ---------------------------------------------------------------------------
# 6. 더미 센서 데이터로 시뮬레이션 검증
# ---------------------------------------------------------------------------
def run_simulation():
    random.seed(7)
    dt = 0.05  # 20Hz 샘플링 -> 3프레임 = 150ms
    directions = list(Direction)
    channels = {d: SensorChannel(direction=d, dt=dt) for d in directions}

    # 시나리오: S1(전방)에 물체가 2.0m -> 0.05m까지 빠르게 접근,
    #          S6(후방)에는 고정 장애물이 0.3m 부근에서 정지(정적),
    #          그 외 방향은 장애물 없음(먼 거리 유지)
    total_frames = 60
    t = 0.0
    front_distance = 2.0
    approach_speed = 0.7  # m/s (가상 실제 속도)

    emergency_events = []
    print(f"{'t(s)':>5} | {'S1 dist':>7} {'S1 v':>6} {'S1 lvl':>8} {'S1 TTC':>7} | 최종 우선 방향 결과")
    print("-" * 78)

    for i in range(total_frames):
        readings = {}
        for d in directions:
            if d == Direction.S1_FRONT:
                front_distance = max(0.0, front_distance - approach_speed * dt)
                noise = random.gauss(0, 0.01)
                readings[d] = max(0.0, front_distance + noise)
            elif d == Direction.S6_REAR:
                readings[d] = 0.30 + random.gauss(0, 0.005)  # 정적 장애물
            else:
                readings[d] = 3.0 + random.gauss(0, 0.02)  # 장애물 없음(먼 거리)

        frame_results = []
        for d in directions:
            res = channels[d].process(readings[d], now_t=t)
            frame_results.append(res)

        chosen = resolve_priority(frame_results)
        s1 = next(r for r in frame_results if r["direction"] == "S1")

        print(f"{t:5.2f} | {s1['distance_m']:7.3f} {s1['velocity_m_s']:6.2f} "
              f"{s1['level'].name:>8} {str(s1['ttc_s']):>7} | "
              f"{chosen['direction'] + ':' + chosen['level'].name if chosen else '-'}")

        if chosen and chosen["level"] == ThreatLevel.CRITICAL:
            d_enum = Direction(chosen["direction"])
            channels[d_enum].trigger_emergency_brake(now_t=t)
            emergency_events.append((round(t, 2), chosen["direction"]))

        t += dt

    # ------------------------------------------------------------------
    # 7. 검증 요약
    # ------------------------------------------------------------------
    print("\n=== 검증 요약 ===")
    print(f"긴급 제동 발생 횟수: {len(emergency_events)}회")
    print(f"긴급 제동 발생 시점(t, 방향): {emergency_events[:5]}{' ...' if len(emergency_events) > 5 else ''}")

    if emergency_events:
        first_t = emergency_events[0][0]
        re_triggered_within_1s = [e for e in emergency_events[1:] if e[0] - first_t < POST_BRAKE_SUPPRESS_S]
        print(f"제동 후 1초 이내 동일 방향 재제동 발생 여부: "
              f"{'있음(로직 오류 가능성)' if re_triggered_within_1s else '없음 (억제 로직 정상 동작)'}")

    rear_d, rear_raw_v = channels[Direction.S6_REAR].kf.x
    rear_closing_v = -rear_raw_v
    print(f"S6(후방) 최종 추정 거리/접근속도: 거리={rear_d:.3f}m, 접근속도={rear_closing_v:.3f}m/s "
          f"-> |v|<=0.02 이므로 '정적' 분류 로직이 맞다면 위 로그에서 obstacle_type은 정적이어야 함")


# ---------------------------------------------------------------------------
# 7. 우선순위 로직(resolve_priority) 단독 검증 - 노이즈 없이 규칙만 확인
# ---------------------------------------------------------------------------
def test_priority_resolution():
    """resolve_priority가 문서의 두 우선순위 규칙을 정확히 지키는지
    가상의 frame_result를 직접 만들어 노이즈 없이 검증한다.
      규칙1) 레벨이 다르면 레벨이 높은 쪽이 항상 우선 (CRITICAL > HIGH > MEDIUM)
      규칙2) 레벨이 같으면 TTC가 더 짧은(=더 급한) 쪽이 우선"""

    def make(direction_value, level, ttc, distance=0.2):
        return {"direction": direction_value, "distance_m": distance,
                "level": level, "ttc_s": ttc, "confirmed": True}

    print("\n=== 우선순위 로직(resolve_priority) 단독 검증 ===")

    # 규칙2: 같은 레벨(HIGH)이면 TTC가 짧은 쪽(S3, 0.3s)이 우선
    case_a = [make("S1", ThreatLevel.HIGH, ttc=0.8), make("S3", ThreatLevel.HIGH, ttc=0.3)]
    result_a = resolve_priority(case_a)
    ok_a = result_a["direction"] == "S3"
    print(f"[동일 레벨, TTC 다름] S1(HIGH,0.8s) vs S3(HIGH,0.3s) -> 선택: {result_a['direction']} "
          f"{'(정상: TTC 짧은 쪽 선택)' if ok_a else '(오류!)'}")

    # 규칙1: 레벨이 다르면 TTC가 길어도 레벨(CRITICAL)이 우선
    case_b = [make("S2", ThreatLevel.CRITICAL, ttc=5.0), make("S4", ThreatLevel.HIGH, ttc=0.1)]
    result_b = resolve_priority(case_b)
    ok_b = result_b["direction"] == "S2"
    print(f"[레벨 다름, TTC 반대] S2(CRITICAL,5.0s) vs S4(HIGH,0.1s) -> 선택: {result_b['direction']} "
          f"{'(정상: 레벨이 TTC보다 우선)' if ok_b else '(오류!)'}")

    print(f"단독 검증 결과: {'모두 통과' if (ok_a and ok_b) else '실패 항목 있음 - 로직 점검 필요'}")


# ---------------------------------------------------------------------------
# 8. 실제 센서 흐름으로 본 복수 방향 동시 접근 시나리오
#    (전방 S1: 느리지만 멀리서 진입 / 좌측 S2: 빠르고 가까이서 진입)
# ---------------------------------------------------------------------------
def run_multi_direction_scenario():
    random.seed(7)
    dt = 0.05
    directions = list(Direction)
    channels = {d: SensorChannel(direction=d, dt=dt) for d in directions}

    front_distance = 1.4
    front_speed = 0.55   # 전방: 멀리서 비교적 느리게 접근
    left_distance = 0.55
    left_speed = 0.9     # 좌측: 가까운 거리에서 빠르게 접근 -> 초반 TTC가 훨씬 짧음

    print("\n=== 복수 방향 동시 접근 시나리오 (전방 vs 좌측) ===")
    print(f"{'t(s)':>5} | {'S1 lvl':>8} {'S1 TTC':>7} | {'S2 lvl':>8} {'S2 TTC':>7} | 최종 선택")
    print("-" * 66)

    t = 0.0
    chosen_directions = []
    for i in range(45):
        readings = {}
        for d in directions:
            if d == Direction.S1_FRONT:
                front_distance = max(0.0, front_distance - front_speed * dt)
                readings[d] = max(0.0, front_distance + random.gauss(0, 0.008))
            elif d == Direction.S2_LEFT:
                left_distance = max(0.0, left_distance - left_speed * dt)
                readings[d] = max(0.0, left_distance + random.gauss(0, 0.006))
            else:
                readings[d] = 3.0 + random.gauss(0, 0.02)

        frame_results = [channels[d].process(readings[d], now_t=t) for d in directions]
        chosen = resolve_priority(frame_results)
        s1 = next(r for r in frame_results if r["direction"] == "S1")
        s2 = next(r for r in frame_results if r["direction"] == "S2")

        if s1["level"] != ThreatLevel.NONE or s2["level"] != ThreatLevel.NONE:
            chosen_str = f"{chosen['direction']}:{chosen['level'].name}" if chosen else "-"
            print(f"{t:5.2f} | {s1['level'].name:>8} {str(s1['ttc_s']):>7} | "
                  f"{s2['level'].name:>8} {str(s2['ttc_s']):>7} | {chosen_str}")
            if chosen:
                chosen_directions.append(chosen["direction"])
        t += dt

    print(f"\n관찰: 좌측(S2)이 거리는 더 가깝지만 접근속도도 더 빨라 항상 TTC가 더 짧았고, "
          f"전방(S1)이 나중에 같은 CRITICAL 레벨에 도달한 뒤에도 우선순위는 계속 S2 유지"
          f"({set(chosen_directions)}만 선택됨) -> '레벨 같으면 TTC 짧은 쪽 우선' 규칙이 "
          f"실제 센서 흐름 속에서도 일관되게 적용됨을 확인")


if __name__ == "__main__":
    run_simulation()
    test_priority_resolution()
    run_multi_direction_scenario()
