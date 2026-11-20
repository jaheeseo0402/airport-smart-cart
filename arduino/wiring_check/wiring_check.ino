// 배선 확인용 코드 (아두이노 우노) - 라인센서 2개 + 초음파 4개 + 모터
// 시리얼 모니터(9600)에서 사용. 모터 시험은 바퀴를 바닥에서 띄우고 하세요.
//
// 0.5초마다 센서 값이 출력됩니다.
//   라인 L=0 R=1  : 센서가 보는 값 (흰/검정에서 값이 바뀌는지 확인)
//   앞/왼/오/뒤 : 초음파 거리(cm), "없음"이면 응답 없음(배선 확인)
//
// 모터 시험 명령 (시리얼 모니터에 한 글자 입력 후 전송)
//   L : 왼쪽 바퀴 앞으로 1초
//   R : 오른쪽 바퀴 앞으로 1초
//   B : 두 바퀴 앞으로 1초
//   S : 정지

const int LEFT_PIN1  = 10;
const int LEFT_PIN2  = 9;
const int RIGHT_PIN1 = 6;
const int RIGHT_PIN2 = 5;

const int IR_L = 2;
const int IR_R = 3;

const int NUM = 4;
const int TRIG[NUM] = {7, 11, A0, A2};
const int ECHO[NUM] = {8, 12, A1, A3};
const char* NAME[NUM] = {"앞", "왼", "오", "뒤"};

const int TEST_SPEED = 150;
unsigned long lastPrint = 0;

void stopMotors() {
  analogWrite(LEFT_PIN1, 0);  analogWrite(LEFT_PIN2, 0);
  analogWrite(RIGHT_PIN1, 0); analogWrite(RIGHT_PIN2, 0);
}

float readCm(int trig, int echo) {
  digitalWrite(trig, LOW);  delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);
  unsigned long d = pulseIn(echo, HIGH, 12000);
  if (d == 0) return -1;
  return d * 0.0343f / 2.0f;
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
  Serial.println("배선 확인 시작 (모터 명령: L / R / B / S)");
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'L' || c == 'l') {
      Serial.println("왼쪽 바퀴 1초");
      analogWrite(LEFT_PIN1, TEST_SPEED); analogWrite(LEFT_PIN2, 0);
      delay(1000); stopMotors();
    } else if (c == 'R' || c == 'r') {
      Serial.println("오른쪽 바퀴 1초");
      analogWrite(RIGHT_PIN1, TEST_SPEED); analogWrite(RIGHT_PIN2, 0);
      delay(1000); stopMotors();
    } else if (c == 'B' || c == 'b') {
      Serial.println("두 바퀴 1초");
      analogWrite(LEFT_PIN1, TEST_SPEED);  analogWrite(LEFT_PIN2, 0);
      analogWrite(RIGHT_PIN1, TEST_SPEED); analogWrite(RIGHT_PIN2, 0);
      delay(1000); stopMotors();
    } else if (c == 'S' || c == 's') {
      stopMotors();
      Serial.println("정지");
    }
  }

  if (millis() - lastPrint >= 500) {
    lastPrint = millis();
    Serial.print("라인 L=");
    Serial.print(digitalRead(IR_L));
    Serial.print(" R=");
    Serial.print(digitalRead(IR_R));
    Serial.print(" | ");
    for (int i = 0; i < NUM; i++) {
      float cm = readCm(TRIG[i], ECHO[i]);
      Serial.print(NAME[i]);
      Serial.print("=");
      if (cm < 0) Serial.print("없음");
      else Serial.print(cm, 1);
      Serial.print(" ");
      delay(30);
    }
    Serial.println();
  }
}
