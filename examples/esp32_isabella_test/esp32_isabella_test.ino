// Laboratory-only ESP32 example: built-in LED + simple telemetry. Never commit credentials.
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
const char* WIFI_SSID = "SET_IN_LOCAL_SECRETS";
const char* WIFI_PASSWORD = "SET_IN_LOCAL_SECRETS";
const char* MQTT_HOST = "192.168.1.10"; // private LAN only
const int LED_PIN = 2;
WiFiClient network; PubSubClient mqtt(network);
void callback(char* topic, byte* payload, unsigned int length) {
  if (length > 128) return;
  StaticJsonDocument<128> document;
  if (deserializeJson(document, payload, length) != DeserializationError::Ok) return;
  const char* command = document["command"] | "";
  if (strcmp(command, "light_on") == 0) digitalWrite(LED_PIN, HIGH);
  else if (strcmp(command, "light_off") == 0) digitalWrite(LED_PIN, LOW);
}
void setup() {
  pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, LOW); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  mqtt.setServer(MQTT_HOST, 1883); mqtt.setCallback(callback);
  // Configure MQTT username/password locally before connect().
}
void loop() {
  if (mqtt.connected()) {
    mqtt.loop();
    mqtt.publish("isabella/home/esp32_lab_1/heartbeat", "{\"status\":\"ONLINE\"}");
    mqtt.publish("isabella/home/esp32_lab_1/telemetry", "{\"capability\":\"temperature\",\"value\":23.4,\"unit\":\"celsius\",\"timestamp\":\"REPLACE_WITH_UTC_ISO8601\"}");
  }
  delay(10000);
}
