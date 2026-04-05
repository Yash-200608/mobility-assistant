#include <Wire.h>
#include <SoftwareSerial.h>
#include <MPU9250.h>
#include <TinyGPS++.h>

#define US_FRONT_TRIG  2
#define US_FRONT_ECHO  3
#define US_FLOOR_TRIG  4
#define US_FLOOR_ECHO  5
#define SOS_BUTTON_PIN 6
#define GPS_RX_PIN    10
#define GPS_TX_PIN    11

class NullStream : public Stream {
public:
  int    available()                        { return 0;  }
  int    read()                             { return -1; }
  int    peek()                             { return -1; }
  void   flush()                            {}
  size_t write(uint8_t)                     { return 1;  }
  size_t write(const uint8_t*, size_t n)    { return n;  }
};
static NullStream nullStream;

MPU9250       mpu;
TinyGPSPlus   gps;
SoftwareSerial gpsSerial(GPS_RX_PIN, GPS_TX_PIN);

static float pitch      = 0.0f;
static float roll       = 0.0f;
static unsigned long lastLoopMs = 0;

static int frontFail = 0;
static int floorFail = 0;

static void drainGPS() {
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }
}

static const int MAX_FAIL = 10;

static long readUltrasonic(uint8_t trigPin, uint8_t echoPin, int &failCount) {
  
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  
  unsigned long duration = pulseIn(echoPin, HIGH, 5882UL);

  if (duration == 0) {
    failCount++;
    if (failCount >= MAX_FAIL) return -1L;   
    return 999L;                              
  }

  failCount = 0;
  return (long)(duration * 0.034f / 2.0f);   
}

void setup() {
  
  Serial.begin(57600);

  gpsSerial.begin(9600);
  Wire.begin();

  pinMode(US_FRONT_TRIG, OUTPUT); digitalWrite(US_FRONT_TRIG, LOW);
  pinMode(US_FRONT_ECHO, INPUT);
  pinMode(US_FLOOR_TRIG, OUTPUT); digitalWrite(US_FLOOR_TRIG, LOW);
  pinMode(US_FLOOR_ECHO, INPUT);
  pinMode(SOS_BUTTON_PIN, INPUT_PULLUP);

  
  mpu.verbose(false);
  if (!mpu.setup(0x68)) {
    
    
  }

  lastLoopMs = millis();
}

void loop() {
  
  drainGPS();

  
  unsigned long now = millis();
  float dt = (now - lastLoopMs) * 0.001f;        
  
  if (dt <= 0.0f || dt > 0.5f) dt = 0.05f;
  lastLoopMs = now;

  
  int sosState = (digitalRead(SOS_BUTTON_PIN) == LOW) ? 1 : 0;

  
  
  long usFront = readUltrasonic(US_FRONT_TRIG, US_FRONT_ECHO, frontFail);
  drainGPS();
  long usFloor = readUltrasonic(US_FLOOR_TRIG, US_FLOOR_ECHO, floorFail);
  drainGPS();

  
  mpu.update();

  float ax = mpu.getAccX();
  float ay = mpu.getAccY();
  float az = mpu.getAccZ();
  float gx = mpu.getGyroX();   
  float gy = mpu.getGyroY();
  float gz = mpu.getGyroZ();

  float accelMag = sqrtf(ax*ax + ay*ay + az*az);

  
  float accPitch = atan2f(ay, sqrtf(ax*ax + az*az)) * (180.0f / (float)M_PI);
  float accRoll  = atan2f(-ax, az)                   * (180.0f / (float)M_PI);
  pitch = 0.98f * (pitch + gx * dt) + 0.02f * accPitch;
  roll  = 0.98f * (roll  + gy * dt) + 0.02f * accRoll;

  
  bool    gpsFix = gps.location.isValid();
  double  gpsLat = gpsFix ? gps.location.lat()  : 0.0;
  double  gpsLng = gpsFix ? gps.location.lng()  : 0.0;
  float   gpsSpd = gps.speed.isValid()  ? gps.speed.kmph()  : 0.0f;

  
  
  Serial.print(F("{\"us_front\":"));  Serial.print(usFront);
  Serial.print(F(",\"us_floor\":"));  Serial.print(usFloor);
  Serial.print(F(",\"ax\":"));        Serial.print(ax,       2);
  Serial.print(F(",\"ay\":"));        Serial.print(ay,       2);
  Serial.print(F(",\"az\":"));        Serial.print(az,       2);
  Serial.print(F(",\"gx\":"));        Serial.print(gx,       2);
  Serial.print(F(",\"gy\":"));        Serial.print(gy,       2);
  Serial.print(F(",\"gz\":"));        Serial.print(gz,       2);
  Serial.print(F(",\"pitch\":"));     Serial.print(pitch,    2);
  Serial.print(F(",\"roll\":"));      Serial.print(roll,     2);
  Serial.print(F(",\"accel_mag\":")); Serial.print(accelMag, 2);
  Serial.print(F(",\"gps_lat\":"));   Serial.print(gpsLat,   6);
  Serial.print(F(",\"gps_lng\":"));   Serial.print(gpsLng,   6);
  Serial.print(F(",\"gps_spd\":"));   Serial.print(gpsSpd,   2);
  Serial.print(F(",\"gps_fix\":"));   Serial.print(gpsFix ? 1 : 0);
  Serial.print(F(",\"sos\":"));       Serial.print(sosState);
  Serial.println(F("}"));

  
  
  unsigned long elapsed = millis() - now;
  if (elapsed < 50UL) {
    unsigned long waitUntil = now + 50UL;
    while (millis() < waitUntil) drainGPS();
  }
}
