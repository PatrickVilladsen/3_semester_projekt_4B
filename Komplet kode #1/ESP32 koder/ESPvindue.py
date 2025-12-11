from machine import Pin, PWM
from time import sleep, time
import network
from umqtt.simple import MQTTClient
import json

'''
Skal dokumenteres mere, lige nu er det bare sammensat af flere gamle eksempler'''

#Konfiguration for nu
MQTT_SERVER = '192.168.4.1'
CLIENT_ID = 'esp32_vindue'
MQTT_TOPIC_COMMAND = 'vindue/command'
MQTT_TOPIC_STATUS = 'vindue/status'

SSID = 'RaspberryPi_AP'
PASSWORD = 'RPI12345'

# Hardware pins
STEPPER_PIN1 = #
STEPPER_PIN2 = #
STEPPER_PIN3 = #
STEPPER_PIN4 = #
SOLENOID_PIN = #
BUZZER_PIN = #

# Stepper motor indstillinger - skal rettes
STEPS_PR_SEKUND = 200
DELAY_MELLEM_STEPS = 1.0 / STEPS_PR_SEKUND
STEPS_HELT_ÅBEN = 1024

# Step sekvens - half step
STEP_SEKVENS = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1]
]

# Globale variabler
nuværende_position = 0
vindue_status = "lukket"

def opsæt_hardware():
    stepper_pins = [
        Pin(STEPPER_PIN1, Pin.OUT),
        Pin(STEPPER_PIN2, Pin.OUT),
        Pin(STEPPER_PIN3, Pin.OUT),
        Pin(STEPPER_PIN4, Pin.OUT)
    ]
    solenoid = Pin(SOLENOID_PIN, Pin.OUT)
    solenoid.value(0)
    buzzer = PWM(Pin(BUZZER_PIN), duty=0)
    
    return stepper_pins, solenoid, buzzer

def sluk_steppermotor(stepper_pins):
    for pin in stepper_pins:
        pin.value(0)

def kør_steps(stepper_pins, steps, retning=1):
    global nuværende_position
    
    step_count = len(STEP_SEKVENS)
    
    for _ in range(abs(steps)):
        for i in range(step_count):
            if retning > 0:
                step_index = i
            else:
                step_index = step_count - 1 - i
            
            for pin_idx, pin in enumerate(stepper_pins):
                pin.value(STEP_SEKVENS[step_index][pin_idx])
            
            sleep(DELAY_MELLEM_STEPS)
    
    nuværende_position += steps * retning
    if nuværende_position < 0:
        nuværende_position = 0
    elif nuværende_position > STEPS_HELT_ÅBEN:
        nuværende_position = STEPS_HELT_ÅBEN

def aktiver_solenoid(solenoid):
    solenoid.value(1)

def deaktiver_solenoid(solenoid):
    solenoid.value(0)

def buzzer_tone(buzzer, frequency, tone_duration, silence_duration):
    buzzer.duty(512)
    buzzer.freq(frequency)
    sleep(tone_duration)
    buzzer.duty(0)
    sleep(silence_duration)

def advarsel_åbner(buzzer):
    buzzer_tone(buzzer, 440, 0.2, 0.05)


def advarsel_lukker(buzzer):
    buzzer_tone(buzzer, 440, 0.2, 0.05)


def stop_buzzer(buzzer):
    buzzer.duty(0)

#
def åben_vindue(stepper_pins, solenoid, buzzer):
    global vindue_status, nuværende_position
    
    if nuværende_position >= STEPS_HELT_ÅBEN:
        return
    
    try:
        advarsel_åbner(buzzer)
        aktiver_solenoid(solenoid)
        sleep(0.5)
        
        steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
        kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        
        vindue_status = "aaben"
        
    except Exception as e:
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        stop_buzzer(buzzer)
        raise
#
def kort_åben_vindue(stepper_pins, solenoid, buzzer, client):
    global vindue_status, nuværende_position
    
    try:
        # 
        advarsel_åbner(buzzer)
        aktiver_solenoid(solenoid)
        sleep(0.5)
        
        if nuværende_position < STEPS_HELT_ÅBEN:
            steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
            kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        vindue_status = "aaben"
        
        # Send status
        send_status(client)
        
        # 5 minutter
        print("åbner")
        sleep(300)
        
        #Luk vinduet igen
        luk_vindue(stepper_pins, solenoid, buzzer)
        send_status(client)
        
        print("lukker")
        
    except Exception as e:
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        stop_buzzer(buzzer)
        raise

def luk_vindue(stepper_pins, solenoid, buzzer):
    global vindue_status, nuværende_position
    
    if nuværende_position <= 0:
        return
    
    try:
        advarsel_lukker(buzzer)
        aktiver_solenoid(solenoid)
        sleep(0.5)
        
        steps_at_køre = nuværende_position
        kør_steps(stepper_pins, steps_at_køre, retning=-1)
        
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        
        vindue_status = "lukket"
        
    except Exception as e:
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        stop_buzzer(buzzer)
        raise

def manuel_åben(stepper_pins, solenoid):
    """Manuel åbning - 1/5 ad gangen"""
    global nuværende_position, vindue_status
    
    if nuværende_position >= STEPS_HELT_ÅBEN:
        return
    
    try:
        aktiver_solenoid(solenoid)
        steps = STEPS_HELT_ÅBEN // 5
        kør_steps(stepper_pins, steps, retning=1)
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        
        if nuværende_position >= STEPS_HELT_ÅBEN:
            vindue_status = "aaben"
            
    except Exception as e:
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        raise

def manuel_luk(stepper_pins, solenoid):
    """Manuel lukning - 1/5 ad gangen"""
    global nuværende_position, vindue_status
    
    if nuværende_position <= 0:
        return
    
    try:
        aktiver_solenoid(solenoid)
        steps = STEPS_HELT_ÅBEN // 5
        kør_steps(stepper_pins, steps, retning=-1)
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        
        if nuværende_position <= 0:
            vindue_status = "lukket"
            
    except Exception as e:
        deaktiver_solenoid(solenoid)
        sluk_steppermotor(stepper_pins)
        raise

def forbind_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if not wlan.isconnected():
        wlan.connect(SSID, PASSWORD)
        timeout = 20
        while not wlan.isconnected() and timeout > 0:
            sleep(1)
            timeout -= 1
        
        if not wlan.isconnected():
            raise Exception("WiFi forbindelse error")
    
    return wlan

def send_status(client):
    try:
        status_data = json.dumps({
            'status': vindue_status,
            'position': nuværende_position,
            'max_position': STEPS_HELT_ÅBEN
        })
        client.publish(MQTT_TOPIC_STATUS, status_data)
    except Exception as e:
        raise Exception("Status send fejl: " + str(e))

def mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, client):
    try:
        data = json.loads(msg)
        kommando = data.get('command', '')
        
        if kommando == 'aaben':
            åben_vindue(stepper_pins, solenoid, buzzer)
            send_status(client)
        
        elif kommando == 'kort_aaben':
            kort_åben_vindue(stepper_pins, solenoid, buzzer, client)
        
        elif kommando == 'luk':
            luk_vindue(stepper_pins, solenoid, buzzer)
            send_status(client)
        
        elif kommando == 'manuel_aaben':
            manuel_åben(stepper_pins, solenoid)
            send_status(client)
        
        elif kommando == 'manuel_luk':
            manuel_luk(stepper_pins, solenoid)
            send_status(client)
    
    except Exception as e:
        pass

def main():
    try:
        stepper_pins, solenoid, buzzer = opsæt_hardware()
        forbind_wifi()
        
        client = MQTTClient(CLIENT_ID, MQTT_SERVER)
        
        def callback_wrapper(topic, msg):
            mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, client)
        
        client.set_callback(callback_wrapper)
        client.connect()
        client.subscribe(MQTT_TOPIC_COMMAND)
        send_status(client)
        
        while True:
            try:
                client.check_msg()
                sleep(0.1)
            except Exception as e:
                try:
                    client.disconnect()
                except:
                    pass
                sleep(5)
                try:
                    forbind_wifi()
                    client = MQTTClient(CLIENT_ID, MQTT_SERVER)
                    client.set_callback(callback_wrapper)
                    client.connect()
                    client.subscribe(MQTT_TOPIC_COMMAND)
                except:
                    sleep(10)
    
    except Exception as e:
        sleep(10)

if __name__ == "__main__":
    while True:
        try:
            main()
        except:
            sleep(10)