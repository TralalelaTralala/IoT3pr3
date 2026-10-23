#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <ArduinoMqttClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <Wire.h>
#include <LittleFS.h>
#include <Adafruit_BME280.h>
#include <BH1750.h>
#include <NimBLEDevice.h>

#if __has_include("secrets.h")
#include "secrets.h"
#else
#define WIFI_SSID "YOUR_WIFI"
#define WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""
#endif

static const char* DEVICE_ID = "weather-station-01";
static const char* ROOT_TOPIC = "weather_station";
static constexpr uint32_t DEMO_PIN = 123456; // Change for a real deployment.
static constexpr int PIN_MQ135 = 34;
static constexpr int PIN_WIND_POT = 35;
static constexpr uint8_t MQTT_QOS = 1;

WiFiClient wifiClient;
MqttClient mqttClient(wifiClient);
WebServer server(80);
Adafruit_BME280 bme;
BH1750 bh1750;
bool bmeReady = false, lightReady = false;

struct Config {
  uint16_t intervalSeconds = 2;
  String location = "Навчальна лабораторія";
  float offsetTemp = 0, offsetHumidity = 0, offsetPressure = 0;
  float offsetLight = 0, offsetAqi = 0, offsetWind = 0;
  uint8_t mqttQos = 1;
} config;

struct Reading {
  float temperature = 22, humidity = 48, pressure = 1013, light = 380, aqi = 72, wind = 2.4;
  bool tempOk = true, humidityOk = true, pressureOk = true, lightOk = true, aqiOk = true, windOk = true;
  uint32_t sequence = 0;
  uint32_t sampleMs = 0;
} current;
Reading historyBuffer[60];
uint8_t historyCount = 0, historyWriteIndex = 0;

float filterWindow[6][5] = {};
uint8_t filterCount[6] = {}, filterIndex[6] = {};
uint32_t nextSample = 0, nextMqttTry = 0, nextHistorySample = 0, reconnectDelayMs = 1000;
uint32_t rateWindowStart = 0;
uint16_t requestsInWindow = 0;
IPAddress rateClient;
uint32_t bootMs = 0;
String lastConfigError;

NimBLECharacteristic *bleTemperature = nullptr, *bleHumidity = nullptr, *blePressure = nullptr;
NimBLECharacteristic *bleLight = nullptr, *bleAir = nullptr, *bleWind = nullptr;
NimBLECharacteristic *bleInterval = nullptr, *bleLocation = nullptr, *bleCalibration = nullptr, *bleDiagnostics = nullptr;

float medianPush(uint8_t channel, float value) {
  filterWindow[channel][filterIndex[channel]] = value;
  filterIndex[channel] = (filterIndex[channel] + 1) % 5;
  if (filterCount[channel] < 5) filterCount[channel]++;
  float sorted[5];
  for (uint8_t i = 0; i < filterCount[channel]; i++) sorted[i] = filterWindow[channel][i];
  for (uint8_t i = 0; i < filterCount[channel]; i++)
    for (uint8_t j = i + 1; j < filterCount[channel]; j++)
      if (sorted[j] < sorted[i]) { float t = sorted[i]; sorted[i] = sorted[j]; sorted[j] = t; }
  return sorted[filterCount[channel] / 2];
}

void collectSensors() {
  float temp = bmeReady ? bme.readTemperature() : current.temperature;
  float humidity = bmeReady ? bme.readHumidity() : current.humidity;
  float pressure = bmeReady ? bme.readPressure() / 100.0F : current.pressure;
  float light = lightReady ? bh1750.readLightLevel() : current.light;
  int gasRaw = analogRead(PIN_MQ135);
  float aqi = map(gasRaw, 0, 4095, 0, 300); // A teaching index; MQ-135 requires calibration for real AQI/ppm.
  float wind = analogRead(PIN_WIND_POT) * (12.0F / 4095.0F); // Potentiometer stands in for an anemometer.
  current.tempOk = isfinite(temp) && temp >= -40 && temp <= 85;
  current.humidityOk = isfinite(humidity) && humidity >= 0 && humidity <= 100;
  current.pressureOk = isfinite(pressure) && pressure >= 300 && pressure <= 1200;
  current.lightOk = lightReady && isfinite(light) && light >= 0;
  current.aqiOk = isfinite(aqi);
  current.windOk = isfinite(wind) && wind >= 0;
  current.temperature = medianPush(0, temp) + config.offsetTemp;
  current.humidity = constrain(medianPush(1, humidity) + config.offsetHumidity, 0, 100);
  current.pressure = medianPush(2, pressure) + config.offsetPressure;
  current.light = max(0.0F, medianPush(3, light) + config.offsetLight);
  current.aqi = constrain(medianPush(4, aqi) + config.offsetAqi, 0, 500);
  current.wind = max(0.0F, medianPush(5, wind) + config.offsetWind);
  current.sequence++;
  current.sampleMs = millis();
  // Keep one point per minute so /api/history represents the latest hour
  // even when the live sensor sampling interval is only a few seconds.
  if (historyCount == 0 || (int32_t)(millis() - nextHistorySample) >= 0) {
    historyBuffer[historyWriteIndex] = current;
    historyWriteIndex = (historyWriteIndex + 1) % 60;
    if (historyCount < 60) historyCount++;
    nextHistorySample = millis() + 60000UL;
  }
}

void makeSensorJson(JsonDocument& doc) {
  doc["device_id"] = DEVICE_ID;
  doc["location"] = config.location;
  doc["timestamp_ms"] = millis();
  doc["sequence"] = current.sequence;
  JsonObject sensors = doc["sensors"].to<JsonObject>();
  const char* names[] = {"temperature", "humidity", "pressure", "light", "air_quality", "wind_speed"};
  const char* units[] = {"°C", "%", "hPa", "lx", "AQI", "m/s"};
  float values[] = {current.temperature, current.humidity, current.pressure, current.light, current.aqi, current.wind};
  bool health[] = {current.tempOk, current.humidityOk, current.pressureOk, current.lightOk, current.aqiOk, current.windOk};
  for (uint8_t i = 0; i < 6; i++) {
    JsonObject entry = sensors[names[i]].to<JsonObject>();
    entry["value"] = serialized(String(values[i], 2));
    entry["unit"] = units[i];
    entry["health"] = health[i];
  }
}

String serializeSnapshot() { JsonDocument doc; makeSensorJson(doc); String out; serializeJson(doc, out); return out; }
String sensorEnvelope(uint8_t i) {
  JsonDocument doc;
  const char* names[] = {"temperature", "humidity", "pressure", "light", "air_quality", "wind_speed"};
  const char* units[] = {"°C", "%", "hPa", "lx", "AQI", "m/s"};
  float values[] = {current.temperature, current.humidity, current.pressure, current.light, current.aqi, current.wind};
  bool health[] = {current.tempOk, current.humidityOk, current.pressureOk, current.lightOk, current.aqiOk, current.windOk};
  doc["device_id"] = DEVICE_ID; doc["location"] = config.location; doc["timestamp_ms"] = millis(); doc["sequence"] = current.sequence;
  doc["value"] = values[i]; doc["unit"] = units[i]; doc["health"] = health[i];
  String out; serializeJson(doc, out); return out;
}

bool mqttPublish(const String& topic, const String& payload, bool retain = true, uint8_t qos = MQTT_QOS) {
  if (!mqttClient.connected()) return false;
  if (!mqttClient.beginMessage(topic, payload.length(), retain, qos)) return false;
  mqttClient.print(payload);
  return mqttClient.endMessage() == 1;
}

void publishSnapshot() {
  const char* names[] = {"temperature", "humidity", "pressure", "light", "air_quality", "wind_speed"};
  for (uint8_t i = 0; i < 6; i++) mqttPublish(String(ROOT_TOPIC) + "/sensors/" + names[i], sensorEnvelope(i), true, config.mqttQos);
  JsonDocument status; status["device_id"] = DEVICE_ID; status["voltage"] = 3.96; status["unit"] = "V"; status["timestamp_ms"] = millis();
  String battery; serializeJson(status, battery); mqttPublish(String(ROOT_TOPIC) + "/status/battery", battery, true, config.mqttQos);
  JsonDocument diag; diag["overall"] = (bmeReady && lightReady) ? "OK" : "DEGRADED";
  diag["bme280"] = bmeReady; diag["bh1750"] = lightReady; diag["mq135_analog"] = true; diag["wind_potentiometer"] = true;
  diag["uptime_seconds"] = millis() / 1000; diag["samples"] = current.sequence;
  String diagnostics; serializeJson(diag, diagnostics); mqttPublish(String(ROOT_TOPIC) + "/status/diagnostics", diagnostics, true, config.mqttQos);
  JsonDocument heartbeat; heartbeat["device_id"] = DEVICE_ID; heartbeat["timestamp_ms"] = millis(); heartbeat["sequence"] = current.sequence;
  String beat; serializeJson(heartbeat, beat); mqttPublish(String(ROOT_TOPIC) + "/status/heartbeat", beat, false, 0);
}

void publishOnline(bool online) { mqttPublish(String(ROOT_TOPIC) + "/status/online", online ? "true" : "false", true, 1); }

void onMqttMessage(int messageSize) {
  String topic = mqttClient.messageTopic();
  String payload; while (mqttClient.available()) payload += (char)mqttClient.read();
  if (messageSize > 1024) return;
  if (topic == String(ROOT_TOPIC) + "/config/interval") {
    int value = payload.toInt(); if (value >= 1 && value <= 60) config.intervalSeconds = value;
  } else if (topic == String(ROOT_TOPIC) + "/config/location") {
    if (payload.length() > 0 && payload.length() <= 80) config.location = payload;
  } else if (topic == String(ROOT_TOPIC) + "/config/calibration" || topic == String(ROOT_TOPIC) + "/commands/config") {
    JsonDocument doc;
    if (deserializeJson(doc, payload) == DeserializationError::Ok) {
      if (doc["interval_seconds"].is<int>()) { int n = doc["interval_seconds"]; if (n >= 1 && n <= 60) config.intervalSeconds = n; }
      if (doc["location"].is<const char*>()) config.location = String((const char*)doc["location"]);
      JsonObject cal = doc["calibration"].as<JsonObject>();
      if (!cal.isNull()) {
        config.offsetTemp = cal["temperature"] | config.offsetTemp; config.offsetHumidity = cal["humidity"] | config.offsetHumidity;
        config.offsetPressure = cal["pressure"] | config.offsetPressure; config.offsetLight = cal["light"] | config.offsetLight;
        config.offsetAqi = cal["air_quality"] | config.offsetAqi; config.offsetWind = cal["wind_speed"] | config.offsetWind;
      }
    }
  }
}

bool mqttConnect() {
  const String willTopic = String(ROOT_TOPIC) + "/status/online";
  const String willPayload = "false";
  mqttClient.beginWill(willTopic, willPayload.length(), true, 1); mqttClient.print(willPayload); mqttClient.endWill();
  mqttClient.setKeepAliveInterval(30);
  if (strlen(MQTT_USERNAME)) mqttClient.setUsernamePassword(MQTT_USERNAME, MQTT_PASSWORD);
  bool ok = mqttClient.connect(MQTT_HOST, MQTT_PORT);
  if (ok) {
    mqttClient.onMessage(onMqttMessage);
    mqttClient.subscribe(String(ROOT_TOPIC) + "/config/#", 1);
    mqttClient.subscribe(String(ROOT_TOPIC) + "/commands/config", 1);
    publishOnline(true); publishSnapshot(); reconnectDelayMs = 1000;
  }
  return ok;
}

bool rateAllowed() {
  IPAddress ip = server.client().remoteIP();
  uint32_t now = millis();
  if (now - rateWindowStart >= 60000 || ip != rateClient) { rateClient = ip; rateWindowStart = now; requestsInWindow = 0; }
  if (requestsInWindow >= 60) return false;
  requestsInWindow++; return true;
}
void corsHeaders() { server.sendHeader("Access-Control-Allow-Origin", "*"); server.sendHeader("Access-Control-Allow-Headers", "Content-Type"); server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS"); }
bool apiGuard() { corsHeaders(); if (rateAllowed()) return true; server.send(429, "application/json", "{\"error\":\"rate limit exceeded\"}"); return false; }
void sendJson(const String& body, int code = 200) { corsHeaders(); server.send(code, "application/json; charset=utf-8", body); }

void updateBle() {
  if (!bleTemperature) return;
  int16_t t = (int16_t)roundf(current.temperature * 100);
  uint16_t h = (uint16_t)roundf(current.humidity * 100);
  uint32_t p = (uint32_t)roundf(current.pressure * 100); // Pa
  bleTemperature->setValue((uint8_t*)&t, sizeof(t)); bleHumidity->setValue((uint8_t*)&h, sizeof(h)); blePressure->setValue((uint8_t*)&p, sizeof(p));
  bleLight->setValue(String(current.light, 1)); bleAir->setValue(String(current.aqi, 0)); bleWind->setValue(String(current.wind, 2));
  bleTemperature->notify(); bleHumidity->notify(); blePressure->notify(); bleLight->notify(); bleAir->notify(); bleWind->notify();
  bleInterval->setValue(String(config.intervalSeconds)); bleLocation->setValue(config.location);
  JsonDocument cal; cal["temperature"] = config.offsetTemp; cal["humidity"] = config.offsetHumidity; cal["pressure"] = config.offsetPressure;
  cal["light"] = config.offsetLight; cal["air_quality"] = config.offsetAqi; cal["wind_speed"] = config.offsetWind;
  String calText; serializeJson(cal, calText); bleCalibration->setValue(calText);
  JsonDocument diag; diag["bme280"] = bmeReady; diag["bh1750"] = lightReady; diag["sequence"] = current.sequence;
  String diagText; serializeJson(diag, diagText); bleDiagnostics->setValue(diagText); bleDiagnostics->notify();
}

class BlePasskeyCallbacks : public NimBLEServerCallbacks {
  uint32_t onPassKeyDisplay() override { Serial.printf("BLE pairing PIN: %06lu\n", (unsigned long)DEMO_PIN); return DEMO_PIN; }
};
class ConfigWriteCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& connInfo) override {
    String uuid = characteristic->getUUID().toString().c_str();
    std::string value = characteristic->getValue();
    String text(value.c_str());
    if (uuid == "7a1b0201-3f4d-4c82-9b67-0f12a58c0001") { int n = text.toInt(); if (n >= 1 && n <= 60) config.intervalSeconds = n; }
    else if (uuid == "7a1b0202-3f4d-4c82-9b67-0f12a58c0001") { if (text.length() && text.length() <= 80) config.location = text; }
    else if (uuid == "7a1b0203-3f4d-4c82-9b67-0f12a58c0001") {
      JsonDocument doc; if (deserializeJson(doc, text) == DeserializationError::Ok) {
        config.offsetTemp = doc["temperature"] | config.offsetTemp; config.offsetHumidity = doc["humidity"] | config.offsetHumidity;
        config.offsetPressure = doc["pressure"] | config.offsetPressure; config.offsetLight = doc["light"] | config.offsetLight;
        config.offsetAqi = doc["air_quality"] | config.offsetAqi; config.offsetWind = doc["wind_speed"] | config.offsetWind;
      }
    }
  }
};

void setupBle() {
  NimBLEDevice::init("WeatherStation-IoT");
  NimBLEDevice::setSecurityAuth(true, true, true); // bonding, MITM, Secure Connections
  NimBLEDevice::setSecurityPasskey(DEMO_PIN);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_DISPLAY_ONLY);
  NimBLEServer* bleServer = NimBLEDevice::createServer();
  bleServer->setCallbacks(new BlePasskeyCallbacks());
  bleServer->advertiseOnDisconnect(true);
  NimBLEService* weather = bleServer->createService("181A");
  bleTemperature = weather->createCharacteristic("2A6E", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  bleHumidity = weather->createCharacteristic("2A6F", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  blePressure = weather->createCharacteristic("2A6D", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  bleLight = weather->createCharacteristic("7a1b0101-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  bleAir = weather->createCharacteristic("7a1b0102-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  bleWind = weather->createCharacteristic("7a1b0103-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  weather->start();
  NimBLEService* conf = bleServer->createService("7a1b0001-3f4d-4c82-9b67-0f12a58c0001");
  bleInterval = conf->createCharacteristic("7a1b0201-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::READ_ENC | NIMBLE_PROPERTY::WRITE_ENC);
  bleLocation = conf->createCharacteristic("7a1b0202-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::READ_ENC | NIMBLE_PROPERTY::WRITE_ENC);
  bleCalibration = conf->createCharacteristic("7a1b0203-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::READ_ENC | NIMBLE_PROPERTY::WRITE_ENC);
  ConfigWriteCallbacks* writeCb = new ConfigWriteCallbacks();
  bleInterval->setCallbacks(writeCb); bleLocation->setCallbacks(writeCb); bleCalibration->setCallbacks(writeCb);
  conf->start();
  NimBLEService* diagnostic = bleServer->createService("7a1b0002-3f4d-4c82-9b67-0f12a58c0002");
  bleDiagnostics = diagnostic->createCharacteristic("7a1b0301-3f4d-4c82-9b67-0f12a58c0001", NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC);
  diagnostic->start();
  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID("181A"); advertising->addServiceUUID("7a1b0001-3f4d-4c82-9b67-0f12a58c0001");
  advertising->start();
}

void addCorsAndLimit() { if (!apiGuard()) return; }
void setupHttp() {
  server.on("/", HTTP_GET, []() { if (LittleFS.exists("/index.html")) { File f = LittleFS.open("/index.html", "r"); server.streamFile(f, "text/html; charset=utf-8"); f.close(); } else server.send(404, "text/plain", "Upload filesystem image with pio run -t uploadfs"); });
  server.on("/api/sensors", HTTP_GET, []() { if (!apiGuard()) return; sendJson(serializeSnapshot()); });
  server.on("/api/sensors/temperature", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(0)); });
  server.on("/api/sensors/humidity", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(1)); });
  server.on("/api/sensors/pressure", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(2)); });
  server.on("/api/sensors/light", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(3)); });
  server.on("/api/sensors/air_quality", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(4)); });
  server.on("/api/sensors/wind_speed", HTTP_GET, []() { if (!apiGuard()) return; sendJson(sensorEnvelope(5)); });
  server.on("/api/history", HTTP_GET, []() {
    if (!apiGuard()) return; JsonDocument doc; doc["minutes"] = 60; JsonArray rows = doc["samples"].to<JsonArray>();
    uint8_t start = historyCount == 60 ? historyWriteIndex : 0;
    for (uint8_t n = 0; n < historyCount; n++) {
      const Reading& r = historyBuffer[(start + n) % 60]; JsonObject row = rows.add<JsonObject>();
      row["sequence"] = r.sequence; row["timestamp_ms"] = r.sampleMs;
      JsonObject sensors = row["sensors"].to<JsonObject>();
      const char* names[] = {"temperature", "humidity", "pressure", "light", "air_quality", "wind_speed"};
      float values[] = {r.temperature, r.humidity, r.pressure, r.light, r.aqi, r.wind};
      const char* units[] = {"°C", "%", "hPa", "lx", "AQI", "m/s"};
      for (uint8_t i = 0; i < 6; i++) { JsonObject v = sensors[names[i]].to<JsonObject>(); v["value"] = values[i]; v["unit"] = units[i]; }
    }
    String out; serializeJson(doc, out); sendJson(out);
  });
  server.on("/api/config", HTTP_GET, []() {
    if (!apiGuard()) return; JsonDocument d; d["interval_seconds"] = config.intervalSeconds; d["location"] = config.location;
    JsonObject cal = d["calibration"].to<JsonObject>(); cal["temperature"] = config.offsetTemp; cal["humidity"] = config.offsetHumidity;
    cal["pressure"] = config.offsetPressure; cal["light"] = config.offsetLight; cal["air_quality"] = config.offsetAqi; cal["wind_speed"] = config.offsetWind;
    String out; serializeJson(d, out); sendJson(out);
  });
  server.on("/api/config", HTTP_POST, []() {
    if (!apiGuard()) return; JsonDocument doc; auto err = deserializeJson(doc, server.arg("plain"));
    if (err) { sendJson("{\"error\":\"invalid JSON\"}", 400); return; }
    if (doc["interval_seconds"].is<int>()) { int n = doc["interval_seconds"]; if (n < 1 || n > 60) { sendJson("{\"error\":\"interval_seconds must be 1..60\"}", 400); return; } config.intervalSeconds = n; }
    if (doc["location"].is<const char*>()) { String loc = doc["location"].as<String>(); if (loc.length() < 1 || loc.length() > 80) { sendJson("{\"error\":\"invalid location\"}", 400); return; } config.location = loc; }
    JsonObject cal = doc["calibration"].as<JsonObject>(); if (!cal.isNull()) { config.offsetTemp = cal["temperature"] | config.offsetTemp; config.offsetHumidity = cal["humidity"] | config.offsetHumidity; config.offsetPressure = cal["pressure"] | config.offsetPressure; config.offsetLight = cal["light"] | config.offsetLight; config.offsetAqi = cal["air_quality"] | config.offsetAqi; config.offsetWind = cal["wind_speed"] | config.offsetWind; }
    JsonDocument result; result["ok"] = true; result["interval_seconds"] = config.intervalSeconds; result["location"] = config.location; String out; serializeJson(result, out); sendJson(out); publishSnapshot(); updateBle();
  });
  server.on("/api/status", HTTP_GET, []() { if (!apiGuard()) return; JsonDocument d; d["device_id"] = DEVICE_ID; d["location"] = config.location; d["mqtt_connected"] = mqttClient.connected(); d["ble"] = "GATT advertising"; d["uptime_seconds"] = millis() / 1000; String out; serializeJson(d, out); sendJson(out); });
  server.on("/api/diagnostics", HTTP_GET, []() { if (!apiGuard()) return; JsonDocument d; d["overall"] = bmeReady && lightReady ? "OK" : "DEGRADED"; d["bme280"] = bmeReady; d["bh1750"] = lightReady; d["mq135_analog"] = true; d["wind_potentiometer"] = true; d["samples"] = current.sequence; String out; serializeJson(d, out); sendJson(out); });
  server.on("/api/docs", HTTP_GET, []() { if (!apiGuard()) return; sendJson("{\"openapi\":\"3.0.3\",\"info\":{\"title\":\"Weather Station API\",\"version\":\"1.0.0\"},\"paths\":{\"/api/sensors\":{\"get\":{}},\"/api/sensors/{type}\":{\"get\":{}},\"/api/history\":{\"get\":{}},\"/api/config\":{\"post\":{}},\"/api/status\":{\"get\":{}},\"/api/diagnostics\":{\"get\":{}}}}"); });
  server.on("/api/ble/profile", HTTP_GET, []() { if (!apiGuard()) return; sendJson("{\"service\":\"181A\",\"configuration\":\"7a1b0001-3f4d-4c82-9b67-0f12a58c0001\",\"diagnostics\":\"7a1b0002-3f4d-4c82-9b67-0f12a58c0002\",\"security\":\"LE Secure Connections + MITM + passkey\"}"); });
  server.on("/api/config", HTTP_OPTIONS, []() { corsHeaders(); server.send(204); });
  server.onNotFound([]() { corsHeaders(); if (server.method() == HTTP_OPTIONS) { server.send(204); return; } server.send(404, "application/json", "{\"error\":\"not found\"}"); });
  server.begin();
}

void setup() {
  Serial.begin(115200); bootMs = millis(); analogReadResolution(12); Wire.begin(21, 22);
  bmeReady = bme.begin(0x76) || bme.begin(0x77);
  lightReady = bh1750.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi"); uint32_t wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 20000) { delay(300); Serial.print('.'); }
  if (WiFi.status() == WL_CONNECTED) Serial.printf("\nWi-Fi IP: %s\n", WiFi.localIP().toString().c_str());
  setupHttp(); setupBle(); collectSensors(); updateBle(); mqttConnect();
  Serial.println("WeatherStation-IoT ready. Demo BLE PIN: 123456 (change before real use).");
}

void loop() {
  server.handleClient();
  if (mqttClient.connected()) mqttClient.poll();
  if (WiFi.status() == WL_CONNECTED && !mqttClient.connected() && millis() >= nextMqttTry) {
    if (!mqttConnect()) { nextMqttTry = millis() + reconnectDelayMs; reconnectDelayMs = min<uint32_t>(reconnectDelayMs * 2, 30000); }
  }
  if (millis() >= nextSample) {
    collectSensors(); publishSnapshot(); updateBle(); nextSample = millis() + config.intervalSeconds * 1000UL;
    Serial.printf("#%lu T %.1f C RH %.1f %% P %.1f hPa light %.0f lx AQI %.0f wind %.1f m/s MQTT %s\n",
      (unsigned long)current.sequence, current.temperature, current.humidity, current.pressure, current.light, current.aqi, current.wind,
      mqttClient.connected() ? "online" : "offline");
  }
}
