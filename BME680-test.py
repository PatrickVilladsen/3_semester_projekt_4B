import bme680
import time

# Initialiser sensor (prøv begge adresser)
try:
    sensor = bme680.BME680(bme680.I2C_ADDR_PRIMARY)  # 0x76
except:
    sensor = bme680.BME680(bme680.I2C_ADDR_SECONDARY)  # 0x77

# Aktiver gas måling
sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)

print("Starter BME680 målinger\n")

while True:
    if sensor.get_sensor_data():
        print(f"Temp: {sensor.data.temperature:.1f}°C  "
              f"Tryk: {sensor.data.pressure:.0f}hPa  "
              f"Fugt: {sensor.data.humidity:.0f}%  "
              f"Gas: {sensor.data.gas_resistance:.0f}Ω")
    
    time.sleep(2)