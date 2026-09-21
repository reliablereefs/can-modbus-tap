#include "bus_sense.h"

#include "board.h"
#include "driver/i2c.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "bus";

/* ADS1115 single-shot, AIN0 vs GND, PGA ±4.096 V, 128 SPS, comparator off.
 * 15 OS=1, 14:12 MUX=100, 11:9 PGA=001, 8 MODE=1,
 * 7:5 DR=100, 4:2 comparator mode/pol/lat=0, 1:0 COMP_QUE=11. */
static const uint8_t k_config[3] = {0x01, 0xC3, 0x83};

esp_err_t bus_sense_init(void) {
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = TAP_I2C_SDA,
        .scl_io_num = TAP_I2C_SCL,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = 100000,
    };
    esp_err_t err = i2c_param_config(TAP_I2C_PORT, &conf);
    if (err != ESP_OK) {
        return err;
    }
    err = i2c_driver_install(TAP_I2C_PORT, I2C_MODE_MASTER, 0, 0, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c install failed: %s", esp_err_to_name(err));
    }
    return err;
}

int bus_sense_mv(void) {
    esp_err_t err = i2c_master_write_to_device(TAP_I2C_PORT, TAP_ADS_ADDR, k_config, sizeof k_config,
                                               pdMS_TO_TICKS(50));
    if (err != ESP_OK) {
        return -1;
    }
    vTaskDelay(pdMS_TO_TICKS(12));
    uint8_t ptr = 0x00;
    uint8_t raw[2] = {0, 0};
    err = i2c_master_write_read_device(TAP_I2C_PORT, TAP_ADS_ADDR, &ptr, 1, raw, sizeof raw, pdMS_TO_TICKS(50));
    if (err != ESP_OK) {
        return -1;
    }
    int16_t code = (int16_t)(((uint16_t)raw[0] << 8) | raw[1]);
    /* 91.0k over 12.0k. adc_mv = code/8, bus_mv = adc_mv * 103/12 = code * 103/96. */
    return (int)(((int32_t)code * 103 + (code >= 0 ? 48 : -48)) / 96);
}
