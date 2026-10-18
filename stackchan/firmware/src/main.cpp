/*
 * Bench firmware for the M5Stack StackChan (CoreS3).
 *
 * Streams timestamped BMI270 IMU samples and takes head commands over USB serial, with no
 * Wi-Fi or cloud in the loop. All times are esp_timer microseconds since boot.
 *
 * Commands (one per line):
 *   P <seq>                 ping          -> P <seq> <t_us>
 *   H <seq> <yaw> <pitch>   head, degrees -> A <seq> <t_us> <yaw_raw> <pitch_raw>
 *   T <0|1>                 servo torque  -> OK T <0|1>
 *   R                       read head     -> R <t_us> <yaw_deg> <pitch_deg>
 *   S <0|1>                 IMU stream    -> OK S <0|1>
 *   D                       diagnostics
 * Stream and events:
 *   I <t_us> <ax> <ay> <az> <gx> <gy> <gz>   accel in g, gyro in deg/s
 *   Y <t_us> <count>                         rising edge on the sync input (G9, Port B)
 * Errors: E <message>
 *
 * The built-in microphone is not used: on this unit it did not respond to real sound, so
 * stimuli are timed from the host's commands instead.
 *
 * Hardware details follow M5Stack's StackChan firmware (MIT, github.com/m5stack/StackChan
 * 1b57655): servos are Feetech SCS on UART1 (TX 6, RX 7) at 1 Mbit/s, servo power is pin 0
 * of the PY32 IO expander at 0x6F, one raw step is 0.3125 degrees.
 */
#include <Arduino.h>
#include <M5Unified.h>
#include <esp_timer.h>

#include "SCSCL.h"

static constexpr const char* FW_VERSION = "bench-stackchan 3";

// Zero positions read from this unit's stock firmware log (NVS servo/zero_pos_1 and _2).
static constexpr int YAW_ID = 1, PITCH_ID = 2;
static constexpr int YAW_ZERO = 456, PITCH_ZERO = 597;
// Limits from the stock firmware, in degrees.
static constexpr float YAW_MIN = -128.0f, YAW_MAX = 128.0f;
static constexpr float PITCH_MIN = 3.0f, PITCH_MAX = 87.0f;
static constexpr int RAW_MIN = 0, RAW_MAX = 1000;

static constexpr uint8_t AW9523_ADDR = 0x58;
static constexpr uint8_t PY32_ADDR = 0x6F;
static constexpr uint8_t PY32_REG_VERSION = 0x02, PY32_REG_DIR_L = 0x03, PY32_REG_OUT_L = 0x05;
static constexpr uint8_t PY32_REG_PU_L = 0x09, PY32_REG_PD_L = 0x0B;
static constexpr uint32_t I2C_FREQ = 400000;

static constexpr int SYNC_PIN = 9;  // Port B; check the pin on the StackChan body before wiring

static QueueHandle_t sync_queue;
static volatile uint32_t sync_count = 0;

static SCSCL servo_bus;
static bool streaming = true;
static bool servo_power = false;

static inline int64_t now_us() { return esp_timer_get_time(); }

static int deg_to_raw(float deg, int zero)
{
    int raw = zero + (int)lroundf(deg * 16.0f / 5.0f);
    return raw < RAW_MIN ? RAW_MIN : (raw > RAW_MAX ? RAW_MAX : raw);
}

static float raw_to_deg(int raw, int zero) { return (raw - zero) * 5.0f / 16.0f; }

static void IRAM_ATTR sync_isr()
{
    int64_t t = esp_timer_get_time();
    sync_count++;
    BaseType_t woken = pdFALSE;
    xQueueSendFromISR(sync_queue, &t, &woken);
    if (woken) portYIELD_FROM_ISR();
}

static void pump_sync()
{
    int64_t t;
    while (xQueueReceive(sync_queue, &t, 0) == pdTRUE) Serial.printf("Y %lld %lu\n", t, (unsigned long)sync_count);
}

static bool py32_bit(uint8_t reg, uint8_t mask, bool on)
{
    return on ? M5.In_I2C.bitOn(PY32_ADDR, reg, mask, I2C_FREQ)
              : M5.In_I2C.bitOff(PY32_ADDR, reg, mask, I2C_FREQ);
}

// Same sequence as the stock io_expander_init: pin 0 (VM EN) output, pull-up, high.
static bool enable_servo_power()
{
    uint32_t start = millis();
    uint8_t version = 0;
    while (millis() - start < 1500) {
        version = M5.In_I2C.readRegister8(PY32_ADDR, PY32_REG_VERSION, I2C_FREQ);
        if (version != 0 && version != 0xFF) break;
        delay(200);
    }
    if (version == 0 || version == 0xFF) return false;
    bool ok = py32_bit(PY32_REG_DIR_L, 0x01, true);
    ok &= py32_bit(PY32_REG_PD_L, 0x01, false);
    ok &= py32_bit(PY32_REG_PU_L, 0x01, true);
    ok &= py32_bit(PY32_REG_OUT_L, 0x01, true);
    delay(200);
    return ok;
}

static void show_status(const char* line2)
{
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setCursor(10, 20);
    M5.Display.setTextSize(3);
    M5.Display.println("BENCH");
    M5.Display.setTextSize(2);
    M5.Display.println(FW_VERSION);
    M5.Display.println(line2);
}

static void handle_line(char* line)
{
    char cmd = line[0];
    if (cmd == 'P') {
        unsigned long seq = strtoul(line + 1, nullptr, 10);
        Serial.printf("P %lu %lld\n", seq, now_us());
    } else if (cmd == 'H') {
        unsigned long seq;
        float yaw, pitch;
        if (sscanf(line + 1, "%lu %f %f", &seq, &yaw, &pitch) != 3) {
            Serial.println("E bad H, want: H <seq> <yaw> <pitch>");
            return;
        }
        if (!servo_power) {
            Serial.println("E servo power is off");
            return;
        }
        yaw = constrain(yaw, YAW_MIN, YAW_MAX);
        pitch = constrain(pitch, PITCH_MIN, PITCH_MAX);
        int yraw = deg_to_raw(yaw, YAW_ZERO), praw = deg_to_raw(pitch, PITCH_ZERO);
        servo_bus.WritePos(YAW_ID, yraw, 20, 0);
        servo_bus.WritePos(PITCH_ID, praw, 20, 0);
        Serial.printf("A %lu %lld %d %d\n", seq, now_us(), yraw, praw);
    } else if (cmd == 'T') {
        int on = atoi(line + 1) ? 1 : 0;
        servo_bus.EnableTorque(YAW_ID, on);
        servo_bus.EnableTorque(PITCH_ID, on);
        Serial.printf("OK T %d\n", on);
    } else if (cmd == 'R') {
        int y = servo_bus.ReadPos(YAW_ID), p = servo_bus.ReadPos(PITCH_ID);
        if (y < 0 || p < 0) {
            Serial.printf("E read failed yaw=%d pitch=%d\n", y, p);
            return;
        }
        Serial.printf("R %lld %.2f %.2f\n", now_us(), raw_to_deg(y, YAW_ZERO), raw_to_deg(p, PITCH_ZERO));
    } else if (cmd == 'S') {
        streaming = atoi(line + 1) != 0;
        Serial.printf("OK S %d\n", streaming ? 1 : 0);
    } else if (cmd == 'D') {
        Serial.printf("D board=%d imu_type=%d sda=%d scl=%d", (int)M5.getBoard(), (int)M5.Imu.getType(),
                      M5.In_I2C.getSDA(), M5.In_I2C.getSCL());
        // Probe only the chips this firmware uses: AXP2101, AW9523, BMI270, PY32.
        Serial.printf(" axp90=%02X", M5.In_I2C.readRegister8(0x34, 0x90, I2C_FREQ));
        for (uint8_t a : {0x34, 0x36, 0x40, 0x58, 0x69, 0x6F}) {
            Serial.printf(" %02X=%d", a, M5.In_I2C.scanID(a) ? 1 : 0);
        }
        Serial.printf(" py32_ver=%02X servo_power=%d\n",
                      M5.In_I2C.readRegister8(PY32_ADDR, PY32_REG_VERSION, I2C_FREQ), servo_power);
    } else if (cmd) {
        Serial.printf("E unknown command '%c'\n", cmd);
    }
}

void setup()
{
    auto cfg = M5.config();
    cfg.internal_spk = false;
    cfg.internal_mic = false;
    M5.begin(cfg);
    Serial.begin(115200);

    // Stock board init sets these AW9523 bits (P0: 0-2, P1: 7); keep them set.
    M5.In_I2C.bitOn(AW9523_ADDR, 0x02, 0b00000111, I2C_FREQ);
    M5.In_I2C.bitOn(AW9523_ADDR, 0x03, 0b10000000, I2C_FREQ);

    servo_power = enable_servo_power();
    bool bus_ok = servo_bus.begin(UART_NUM_1, 1000000, 6, 7);
    int ping_yaw = servo_bus.Ping(YAW_ID), ping_pitch = servo_bus.Ping(PITCH_ID);

    char status[64];
    snprintf(status, sizeof status, "servo pwr %s bus %s ids %d,%d", servo_power ? "on" : "OFF",
             bus_ok ? "ok" : "FAIL", ping_yaw, ping_pitch);
    show_status(status);

    sync_queue = xQueueCreate(32, sizeof(int64_t));
    pinMode(SYNC_PIN, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(SYNC_PIN), sync_isr, RISING);
    Serial.printf("HELLO %s imu=%s servo_power=%d servo_bus=%d ping=%d,%d zero=%d,%d\n",
                  FW_VERSION, M5.Imu.isEnabled() ? "ok" : "none", servo_power, bus_ok, ping_yaw, ping_pitch, YAW_ZERO,
                  PITCH_ZERO);
}

void loop()
{
    static char buf[96];
    static size_t len = 0;
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (len) {
                buf[len] = 0;
                handle_line(buf);
                len = 0;
            }
        } else if (len < sizeof buf - 1) {
            buf[len++] = c;
        }
    }

    pump_sync();

    if (streaming && M5.Imu.update()) {
        int64_t t = now_us();
        auto d = M5.Imu.getImuData();
        Serial.printf("I %lld %.4f %.4f %.4f %.2f %.2f %.2f\n", t, d.accel.x, d.accel.y, d.accel.z, d.gyro.x,
                      d.gyro.y, d.gyro.z);
    }
}
