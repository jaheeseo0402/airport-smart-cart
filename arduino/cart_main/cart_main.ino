// 라인 센서 2개 + 모터 + 초음파 4개(앞, 왼쪽, 오른쪽, 뒤) (아두이노 우노)
// 라인: 왼쪽=2, 오른쪽=3 / 모터: A-1A=5, A-2A=6, B-1A=9, B-2A=10
// 초음파: 앞 TRIG=7 ECHO=8 / 왼쪽 TRIG=11 ECHO=12 / 오른쪽 TRIG=A0 ECHO=A1 / 뒤 TRIG=A2 ECHO=A3
// 아두이노 자체 안전 정지: 앞 20cm / 옆 8cm 이하면 파이 명령과 상관없이 정지, 앞 30cm 초과(+옆 12cm 초과)면 해제
// 100ms마다 "D:앞,왼,오,뒤" 전송 (측정 실패는 -1.0, 파이 코드와 같은 형식)
// 시리얼 모니터(9600): S 정지 / G 시작 / I 센서값 출력
// ※ 바퀴가 뒤로 돌고 왼쪽/오른쪽 모터도 바뀌어 있어서
//    왼쪽 <-> 오른쪽 모터를 바꾸고 방향도 뒤집음

const int LEFT_PIN1  = 10;  // 원래 5
const int LEFT_PIN2  = 9;   // 원래 6
const int RIGHT_PIN1 = 6;   // 원래 9
const int RIGHT_PIN2 = 5;   // 원래 10

const int IR_L = 2;
const int IR_R = 3;

const int NUM = 4;
const int TRIG[NUM] = {7, 11, A0, A2};
const int ECHO[NUM] = {8, 12, A1, A3};

// ★ 검은 선 위에서 찍히는 값(0 또는 1)을 센서별로 넣으세요 ★
const int BLACK_L = 1;
const int BLACK_R = 1;

// 센서별 최대 측정 거리(cm): 앞, 왼, 오, 뒤 (이보다 먼 값은 -1 = 측정 없음). 데모용으로 좁게 설정
const float MAX_CM[NUM] = {80, 60, 60, 60};

// ---- 아두이노 자체 안전 정지 기준 (파이 명령보다 우선) ----
const float FRONT_STOP_CM   = 20;   // 앞 20cm 이하면 정지
const float SIDE_STOP_CM    = 8;    // 왼/오 8cm 이하면 정지
const float FRONT_RESUME_CM = 30;   // 앞이 30cm보다 멀어져야 다시 출발
const float SIDE_RESUME_CM  = 12;   // 옆으로 멈춘 경우, 옆이 12cm보다 멀어져야 출발 (벽에 붙어 멈췄다 갔다 하는 것 방지)
// 뒤 센서는 정지에 쓰지 않음 (앞으로만 주행)
const int NEAR_FRAMES = 2;          // 연속 2번 가까우면 정지 (잡음 한 번에 멈추는 것 방지)

// 바퀴 속도 (0~255): 왼쪽이 느려서 왼쪽을 더 높게 맞춤. 직진하며 한쪽으로 휘면 숫자를 조금씩 바꾸세요.
// 시리얼 모니터에서 + 를 보내면 왼쪽 속도 +5, - 를 보내면 왼쪽 속도 -5 (다시 업로드하지 않고 시험용)
int leftSpeed  = 170;
int rightSpeed = 150;              // 0~255
const unsigned long SENSOR_GAP_MS = 25;    // 센서 1개 측정 간격 (4개 = 100ms 주기, 파이의 DT=0.1과 같아야 함)

bool running = false;
unsigned long lastCycle = 0;
float dist[NUM];
int sensorIdx = 0;
bool blocked = false;          // true면 아두이노가 스스로 정지시킨 상태
int nearCnt[3] = {0, 0, 0};    // 앞, 왼, 오가 연속으로 가까웠던 횟수

void driveMotor(int pin1, int pin2, bool forward, int speed) {
  if (forward) { analogWrite(pin1, speed); analogWrite(pin2, 0); }
  else         { analogWrite(pin1, 0);     analogWrite(pin2, speed); }
}

void stopMotors() {
  analogWrite(LEFT_PIN1, 0);  analogWrite(LEFT_PIN2, 0);
  analogWrite(RIGHT_PIN1, 0); analogWrite(RIGHT_PIN2, 0);
}

float readCm(int trig, int echo, float maxCm) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long timeoutUs = (unsigned long)(maxCm * 58.3f);   // 거리(cm) -> 대기시간(us)
  unsigned long d = pulseIn(echo, HIGH, timeoutUs);
  if (d == 0) return -1;
  return d * 0.0343f / 2.0f;
}

// 방금 잰 센서(i)로 안전 정지/해제를 판단한다. -1(측정 없음)은 "멀다"로 본다.
void updateSafety(int i) {
  if (i > 2) return;                       // 뒤(3번)는 정지에 쓰지 않음
  float d = dist[i];
  bool near = (d > 0) && (d <= (i == 0 ? FRONT_STOP_CM : SIDE_STOP_CM));
  if (near) { if (nearCnt[i] < 9) nearCnt[i]++; } else nearCnt[i] = 0;

  if (!blocked) {
    if (nearCnt[i] >= NEAR_FRAMES) {
      blocked = true;
      stopMotors();
      Serial.println("SAFE:STOP");
    }
  } else {
    bool frontClear = (dist[0] <= 0) || (dist[0] > FRONT_RESUME_CM);
    bool leftClear  = (dist[1] <= 0) || (dist[1] > SIDE_RESUME_CM);
    bool rightClear = (dist[2] <= 0) || (dist[2] > SIDE_RESUME_CM);
    if (frontClear && leftClear && rightClear) {
      blocked = false;
      Serial.println("SAFE:GO");
    }
  }
}

// 센서를 한 번에 하나씩 재서 라인 추종이 길게 멈추지 않게 한다
void measureNext() {
  dist[sensorIdx] = readCm(TRIG[sensorIdx], ECHO[sensorIdx], MAX_CM[sensorIdx]);
  updateSafety(sensorIdx);
  sensorIdx++;
  if (sensorIdx >= NUM) {
    sensorIdx = 0;
    Serial.print("D:");
    Serial.print(dist[0], 1);   // 앞
    Serial.print(",");
    Serial.print(dist[1], 1);   // 왼쪽
    Serial.print(",");
    Serial.print(dist[2], 1);   // 오른쪽
    Serial.print(",");
    Serial.println(dist[3], 1); // 뒤
  }
}

// 라인을 놓쳤을 때(급커브 등) 마지막으로 라인이 있던 쪽으로 제자리 회전해서 다시 찾는 설정
const int SEARCH_SPEED = 130;                  // 회전 속도 (0~255)
const unsigned long SIDE_MEMORY_MS = 1500;     // 마지막으로 한쪽만 검정이던 시점부터 이 시간 안에 놓쳤을 때만 찾기
const unsigned long SEARCH_MAX_MS  = 3000;     // 이 시간 동안 못 찾으면 정지
int lastSide = 0;                              // -1: 왼쪽 센서가 마지막으로 라인을 봄, 1: 오른쪽
unsigned long lastSideTime = 0;
unsigned long lostSince = 0;

void followLine() {
  bool l = (digitalRead(IR_L) == BLACK_L);
  bool r = (digitalRead(IR_R) == BLACK_R);

  if (l && r) {
    lostSince = 0;
    driveMotor(LEFT_PIN1, LEFT_PIN2, true, leftSpeed);
    driveMotor(RIGHT_PIN1, RIGHT_PIN2, true, rightSpeed);
  } else if (l && !r) {
    lostSince = 0; lastSide = -1; lastSideTime = millis();
    analogWrite(LEFT_PIN1, 0); analogWrite(LEFT_PIN2, 0);
    driveMotor(RIGHT_PIN1, RIGHT_PIN2, true, rightSpeed);
  } else if (r && !l) {
    lostSince = 0; lastSide = 1; lastSideTime = millis();
    driveMotor(LEFT_PIN1, LEFT_PIN2, true, leftSpeed);
    analogWrite(RIGHT_PIN1, 0); analogWrite(RIGHT_PIN2, 0);
  } else {
    // 둘 다 흰색: 방금 전까지 한쪽이 라인을 보고 있었다면 그쪽으로 제자리 회전해서 라인을 다시 찾는다
    unsigned long now = millis();
    if (lostSince == 0) {                       // 놓친 직후에만 "방금 라인을 봤는지" 확인
      if (lastSide == 0 || now - lastSideTime > SIDE_MEMORY_MS) { stopMotors(); return; }
      lostSince = now;
    }
    if (now - lostSince > SEARCH_MAX_MS) { stopMotors(); return; }
    if (lastSide < 0) {          // 왼쪽으로 회전: 왼쪽 바퀴 뒤로, 오른쪽 바퀴 앞으로
      driveMotor(LEFT_PIN1, LEFT_PIN2, false, SEARCH_SPEED);
      driveMotor(RIGHT_PIN1, RIGHT_PIN2, true, SEARCH_SPEED);
    } else {                     // 오른쪽으로 회전
      driveMotor(LEFT_PIN1, LEFT_PIN2, true, SEARCH_SPEED);
      driveMotor(RIGHT_PIN1, RIGHT_PIN2, false, SEARCH_SPEED);
    }
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(LEFT_PIN1, OUTPUT);  pinMode(LEFT_PIN2, OUTPUT);
  pinMode(RIGHT_PIN1, OUTPUT); pinMode(RIGHT_PIN2, OUTPUT);
  stopMotors();

  pinMode(IR_L, INPUT);
  pinMode(IR_R, INPUT);
  for (int i = 0; i < NUM; i++) {
    pinMode(TRIG[i], OUTPUT);
    pinMode(ECHO[i], INPUT);
    digitalWrite(TRIG[i], LOW);
  }

  Serial.println("READY - 5초 뒤 시작");
  for (int i = 5; i >= 1; i--) {
    Serial.println(i);
    delay(1000);
  }
  running = true;
  Serial.println("라인 추종 시작");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'S' || c == 's') { running = false; stopMotors(); Serial.println("ACK:S"); }
    else if (c == 'G' || c == 'g') { running = true; Serial.println("ACK:G"); }
    else if (c == '+') { leftSpeed = min(255, leftSpeed + 5); Serial.print("왼쪽 속도 "); Serial.println(leftSpeed); }
    else if (c == '-') { leftSpeed = max(0, leftSpeed - 5);   Serial.print("왼쪽 속도 "); Serial.println(leftSpeed); }
    else if (c == 'I' || c == 'i') {
      Serial.print("IR 왼쪽/오른쪽 = ");
      Serial.print(digitalRead(IR_L)); Serial.print(" ");
      Serial.println(digitalRead(IR_R));
    }
  }

  unsigned long now = millis();
  if (now - lastCycle >= SENSOR_GAP_MS) {
    lastCycle = now;
    measureNext();
  }

  if (running && !blocked) followLine();
  delay(5);
}
