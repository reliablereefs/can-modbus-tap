#pragma once

#include "driver/gpio.h"
#include "driver/i2c.h"

/* TWAI TX is a test point only. The transceiver TXD pin is tied to 3V3
 * on the board, so this GPIO cannot drive the bus even if firmware tries. */
#define TAP_TWAI_TX GPIO_NUM_5
#define TAP_TWAI_RX GPIO_NUM_4

#define TAP_I2C_SDA GPIO_NUM_6
#define TAP_I2C_SCL GPIO_NUM_7
#define TAP_I2C_PORT I2C_NUM_0
#define TAP_ADS_ADDR 0x48

#define TAP_LED GPIO_NUM_2

#define TAP_REC_SIZE 32
#define TAP_REC_MAGIC 0xC0FFEE42u
