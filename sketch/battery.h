#pragma once
// Slow servo-rail voltage sampling and independent Uno Q battery indicators.
// This is an under-load estimate, not a fuel gauge (Microduck: 6.6 V -> 0%, 8.2 V -> 100%).
#include <Arduino_LED_Matrix.h>
#include <string.h>
#include "protocol.h"

static constexpr uint16_t BATTERY_EMPTY_MV = 6600;
static constexpr uint16_t BATTERY_FULL_MV = 8200;
static constexpr uint32_t BATTERY_STALE_US = 5000000u;

// Shared only across control, Bridge report and indicator threads.
static struct k_mutex batteryMu;
static BatteryPacket batteryState = {};
// These are owned exclusively by the control task.
static bool batteryHasSample = false;
static uint16_t batteryFilteredMv = 0;
static uint32_t batteryLastSampleUs = 0;
static Arduino_LED_Matrix batteryMatrix;

static uint8_t batteryPercent(uint16_t mv) {
  if (mv <= BATTERY_EMPTY_MV) return 0;
  if (mv >= BATTERY_FULL_MV) return 100;
  return (uint8_t)(((uint32_t)(mv - BATTERY_EMPTY_MV) * 100u + 800u) / 1600u);
}

static void batteryAcceptVoltage(uint8_t decivolts, uint32_t nowUs) {
  // STS/SCS present-voltage register 0x3E is in 0.1 V units.
  // Accept a 5 V bench supply as a valid 0% estimate; reject missing/corrupt data.
  if (decivolts < 40 || decivolts > 140) return;
  const uint16_t mv = (uint16_t)decivolts * 100u;
  batteryFilteredMv = batteryHasSample
      ? (uint16_t)(((uint32_t)batteryFilteredMv * 3u + mv + 2u) / 4u)
      : mv;  // EMA alpha = 0.25, no artificially low startup ramp.
  batteryHasSample = true;
  batteryLastSampleUs = nowUs;
  k_mutex_lock(&batteryMu, K_FOREVER);
  batteryState.millivolts = batteryFilteredMv;
  batteryState.percent = batteryPercent(batteryFilteredMv);
  batteryState.flags = 1; // valid
  k_mutex_unlock(&batteryMu);
}

static void batteryExpire(uint32_t nowUs) {
  if (!batteryHasSample || (uint32_t)(nowUs - batteryLastSampleUs) <= BATTERY_STALE_US)
    return;
  batteryHasSample = false;
  batteryFilteredMv = 0;
  k_mutex_lock(&batteryMu, K_FOREVER);
  batteryState.millivolts = 0;
  batteryState.percent = 0;
  batteryState.flags = 0; // unknown, NOT 0%
  k_mutex_unlock(&batteryMu);
}

static BatteryPacket batteryReadSnapshot() {
  BatteryPacket reading;
  k_mutex_lock(&batteryMu, K_FOREVER);
  reading = batteryState;
  k_mutex_unlock(&batteryMu);
  return reading;
}

// LED4 is the lower STM RGB indicator on Uno Q, active LOW.
// Color bands: green >= 60%, yellow 25-59%, red < 25%, flashing red <= 10%.
// Missing measurements flash blue, never red (unknown != empty).
static void batterySetLed(bool valid, uint8_t percent, bool flashOn) {
  bool red = false, green = false, blue = false;
  if (!valid) blue = flashOn;
  else if (percent <= 10) red = flashOn;
  else if (percent < 25) red = true;
  else if (percent < 60) { red = true; green = true; }
  else green = true;
  digitalWrite(LED4_R, red ? LOW : HIGH);
  digitalWrite(LED4_G, green ? LOW : HIGH);
  digitalWrite(LED4_B, blue ? LOW : HIGH);
}

static void batteryDrawMatrix(bool valid, uint8_t percent, bool flashOn) {
  uint8_t pixels[8 * 13] = {};
  if (!valid) {
    // A visible "?" instead of an empty battery when there is no servo response.
    static const uint8_t question[7] = {0x0e, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04};
    for (uint8_t y = 0; y < 7; ++y)
      for (uint8_t x = 0; x < 5; ++x)
        if (question[y] & (1u << (4 - x))) pixels[y * 13 + x + 4] = 1;
  } else {
    for (uint8_t x = 1; x <= 10; ++x) {
      pixels[1 * 13 + x] = 1;
      pixels[6 * 13 + x] = 1;
    }
    for (uint8_t y = 2; y <= 5; ++y) {
      pixels[y * 13 + 1] = 1;
      pixels[y * 13 + 10] = 1;
    }
    pixels[3 * 13 + 11] = pixels[4 * 13 + 11] = 1;
    const uint8_t bars = (uint8_t)(((uint16_t)percent * 8u + 99u) / 100u);
    if (percent > 10 || flashOn)
      for (uint8_t y = 2; y <= 5; ++y)
        for (uint8_t x = 0; x < bars; ++x) pixels[y * 13 + x + 2] = 1;
  }
  batteryMatrix.draw(pixels);
}

static void batteryInit() {
  k_mutex_init(&batteryMu);
  memcpy(batteryState.magic, "DQB1", 4);
  pinMode(LED4_R, OUTPUT);
  pinMode(LED4_G, OUTPUT);
  pinMode(LED4_B, OUTPUT);
  batterySetLed(false, 0, true);
  batteryMatrix.begin();
  batteryMatrix.setGrayscaleBits(1);
}

static void batteryDisplayTask(void *, void *, void *) {
  for (;;) {
    const BatteryPacket b = batteryReadSnapshot();
    const bool valid = (b.flags & 1) != 0;
    const bool flashOn = ((k_uptime_get_32() / 500u) & 1u) == 0;
    batterySetLed(valid, b.percent, flashOn);
    batteryDrawMatrix(valid, b.percent, flashOn);
    k_msleep(250);
  }
}
