#pragma once

#include "esp_err.h"

esp_err_t bus_sense_init(void);

/* Millivolts on the USB-A 24 V pin. Returns -1 when the ADC does not answer. */
int bus_sense_mv(void);
