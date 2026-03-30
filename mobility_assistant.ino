#include <Wire.h>
#include <SoftwareSerial.h>
#include <MPU9250.h>
#include <TinyGPS++.h>

#define US_FRONT_TRIG 2
#define US_FRONT_ECHO 3
#define US_FLOOR_TRIG 4
#define US_FLOOR_ECHO 5
#define FSR_LEFT_PIN A0
#define FSR_RIGHT_PIN A1
#define GPS_RX_PIN 10
#define GPS_TX_PIN 11

MPU9250 mpu;
TinyGPSPlus gps;
SoftwareSerial ss(GPS_RX_PIN, GPS_TX_PIN);

float pitch = 0.0;
float roll = 0.0;
unsigned long last_time = 0;

void setup() {
  Serial.begin(115200);
  ss.begin(9600);
  Wire.begin();
  pinMode(US_FRONT_TRIG, OUTPUT); pinMode(US_FRONT_ECHO, INPUT);
  pinMode(US_FLOOR_TRIG, OUTPUT); pinMode(US_FLOOR_ECHO, INPUT);
  mpu.setup(0x68);
  last_time = millis();
}

long readUltrasonic(int trigPin, int echoPin, int &failCount) {
  digitalWrite(trigPin, LOW); delayMicroseconds(2);
  digitalWrite(trigPin, HIGH); delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  long duration = pulseIn(echoPin, HIGH, 12000); 
  if (duration == 0) {
    failCount++;
    if (failCount > 10) return -1;  // Signal sensor failure
    return 999;
  }
  failCount = 0;
  return duration * 0.034 / 2;
}

void loop() {
  unsigned long current_time = millis();
  float dt = (current_time - last_time) / 1000.0;
  if (dt > 0.2) dt = 0.05;  // Clamp to prevent filter blowup on stalls
  last_time = current_time;

  static int front_fail = 0;
  static int floor_fail = 0;
  long us_front = readUltrasonic(US_FRONT_TRIG, US_FRONT_ECHO, front_fail);
  long us_floor = readUltrasonic(US_FLOOR_TRIG, US_FLOOR_ECHO, floor_fail);
  int fsr_left = analogRead(FSR_LEFT_PIN);
  int fsr_right = analogRead(FSR_RIGHT_PIN);

  mpu.update();
  float ax = mpu.getAccX(), ay = mpu.getAccY(), az = mpu.getAccZ();
  float gx = mpu.getGyroX(), gy = mpu.getGyroY(), gz = mpu.getGyroZ();
  float accel_mag = sqrt(ax*ax + ay*ay + az*az);
  
  float acc_pitch = atan2(ay, sqrt(ax*ax + az*az)) * 180.0 / PI;
  float acc_roll = atan2(-ax, az) * 180.0 / PI;
  pitch = 0.98 * (pitch + gx * dt) + 0.02 * acc_pitch;
  roll = 0.98 * (roll + gy * dt) + 0.02 * acc_roll;

  while (ss.available() > 0) gps.encode(ss.read());
  double gps_lat = gps.location.isValid() ? gps.location.lat() : 0.0;
  double gps_lng = gps.location.isValid() ? gps.location.lng() : 0.0;
  float gps_spd = gps.speed.isValid() ? gps.speed.kmph() : 0.0;
  int gps_fix = gps.location.isValid() ? 1 : 0;

  Serial.print(F("{\"us_front\":")); Serial.print(us_front);
  Serial.print(F(",\"us_floor\":")); Serial.print(us_floor);
  Serial.print(F(",\"fsr_left\":")); Serial.print(fsr_left);
  Serial.print(F(",\"fsr_right\":")); Serial.print(fsr_right);
  Serial.print(F(",\"ax\":")); Serial.print(ax, 2);
  Serial.print(F(",\"ay\":")); Serial.print(ay, 2);
  Serial.print(F(",\"az\":")); Serial.print(az, 2);
  Serial.print(F(",\"gx\":")); Serial.print(gx, 2);
  Serial.print(F(",\"gy\":")); Serial.print(gy, 2);
  Serial.print(F(",\"gz\":")); Serial.print(gz, 2);
  Serial.print(F(",\"pitch\":")); Serial.print(pitch, 2);
  Serial.print(F(",\"roll\":")); Serial.print(roll, 2);
  Serial.print(F(",\"accel_mag\":")); Serial.print(accel_mag, 2);
  Serial.print(F(",\"gps_lat\":")); Serial.print(gps_lat, 6);
  Serial.print(F(",\"gps_lng\":")); Serial.print(gps_lng, 6);
  Serial.print(F(",\"gps_spd\":")); Serial.print(gps_spd, 2);
  Serial.print(F(",\"gps_fix\":")); Serial.print(gps_fix);
  Serial.println(F("}"));

  unsigned long elapsed = millis() - current_time;
  if (elapsed < 50) delay(50 - elapsed);
}