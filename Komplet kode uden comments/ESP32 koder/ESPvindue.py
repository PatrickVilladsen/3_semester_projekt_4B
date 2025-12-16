

from machine import Pin, PWM
from time import sleep, time
import network
from umqtt.simple import MQTTClient
import json



MQTT_SERVER = '192.168.4.1'


ENHEDS_ID = 'esp32_vindue'


MQTT_TOPIC_COMMAND = 'vindue/kommando'


MQTT_TOPIC_STATUS = 'vindue/status'




SSID = 'RaspberryPi_AP'


PASSWORD = 'RPI12345'


WIFI_TIMEOUT = 20




STEPPER_PIN1 = 16


STEPPER_PIN2 = 17


STEPPER_PIN3 = 5


STEPPER_PIN4 = 18


SOLENOID_PIN = 22


BUZZER_PIN = 23




STEPS_PR_SEKUND = 200


DELAY_MELLEM_STEPS = 1.0 / STEPS_PR_SEKUND


STEPS_HELT_ÅBEN = 50




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



SOLENOID_AKTIVERING_DELAY = 1




BUZZER_PWM_DUTY = 150


BUZZER_TONE_ÅBNER = [
    (262, 0.45, 0.10),
    (392, 0.45, 0.10)
]


BUZZER_TONE_LUKKER = [
    (392, 0.45, 0.10),
    (262, 0.45, 0.10)
]




KORT_ÅBNING_VARIGHED = 300


MQTT_CHECK_INTERVAL = 0.1




MANUEL_STEP_FRAKTION = 5



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



def sluk_stepper_motor(stepper_pins):

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
    
    nuværende_position = max(0, min(nuværende_position, STEPS_HELT_ÅBEN))



def aktiver_solenoid(solenoid):

    solenoid.value(1)


def deaktiver_solenoid(solenoid):

    solenoid.value(0)



def buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone):

    buzzer.duty(BUZZER_PWM_DUTY)
    buzzer.freq(frekvens)
    sleep(tone_afspilningstid)
    
    buzzer.duty(0)
    sleep(stille_efter_tone)


def afspil_warning_åbner(buzzer):

    for frekvens, tone_afspilningstid, stille_efter_tone in BUZZER_TONE_ÅBNER:
        buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone)


def afspil_warning_lukker(buzzer):

    for frekvens, tone_afspilningstid, stille_efter_tone in BUZZER_TONE_LUKKER:
        buzzer_tone(buzzer, frekvens, tone_afspilningstid, stille_efter_tone)


def stop_buzzer(buzzer):

    buzzer.duty(0)



def åben_vindue(stepper_pins, solenoid, buzzer):

    global vindue_status, nuværende_position
    
    if nuværende_position >= STEPS_HELT_ÅBEN:
        print("Vinduet er allerede fuldt åbent")
        return
    
    
    try:
        print("Åbner vindue")
        
        afspil_warning_åbner(buzzer)
        
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
        print("Kører {} steps".format(steps_at_køre))
        
        kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        deaktiver_solenoid(solenoid)
        
        sluk_stepper_motor(stepper_pins)
        
        vindue_status = "aaben"
        print("Vindue åbnet (position: {})".format(nuværende_position))
        
    except Exception as fejl:
        print("Fejl under åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def kort_åben_vindue(stepper_pins, solenoid, buzzer, client):

    global vindue_status, nuværende_position
    
    try:
        print("Kort åbning - {}sekunders timer".format(KORT_ÅBNING_VARIGHED))
        
        afspil_warning_åbner(buzzer)
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        if nuværende_position < STEPS_HELT_ÅBEN:
            steps_at_køre = STEPS_HELT_ÅBEN - nuværende_position
            kør_steps(stepper_pins, steps_at_køre, retning=1)
        
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        vindue_status = "aaben"
        
        send_status(client)
        print("Vindue åbnet - venter {}sekunder".format(KORT_ÅBNING_VARIGHED))
        
        start_tid = time()
        
        while (time() - start_tid) < KORT_ÅBNING_VARIGHED:
            client.check_msg()
            
            if vindue_status != "aaben":
                print("Timer afbrudt - vindue lukkes manuelt")
                return
            
            sleep(MQTT_CHECK_INTERVAL)
        
        print("Timer udløbet - lukker automatisk")
        luk_vindue(stepper_pins, solenoid, buzzer)
        send_status(client)
        
    except Exception as fejl:
        print("Fejl under kort åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def luk_vindue(stepper_pins, solenoid, buzzer):

    global vindue_status, nuværende_position
    
    if nuværende_position <= 0:
        print("Vinduet er allerede lukket")
        return
    
    try:
        print("Lukker vindue")
        
        afspil_warning_lukker(buzzer)
        
        aktiver_solenoid(solenoid)
        sleep(SOLENOID_AKTIVERING_DELAY)
        
        steps_at_køre = nuværende_position
        print("Kører {} steps".format(steps_at_køre))
        
        kør_steps(stepper_pins, steps_at_køre, retning=-1)
        
        deaktiver_solenoid(solenoid)
        
        sluk_stepper_motor(stepper_pins)
        
        vindue_status = "lukket"
        print("Vindue lukket (position: {})".format(nuværende_position))
        
    except Exception as fejl:
        print("Fejl under lukning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        stop_buzzer(buzzer)
        raise


def manuel_åben(stepper_pins, solenoid):

    global nuværende_position, vindue_status
    
    if nuværende_position >= STEPS_HELT_ÅBEN:
        print("Vindue allerede fuldt åbent")
        return
    
    try:
        print("Manuel åbning (1/{})".format(MANUEL_STEP_FRAKTION))
        
        aktiver_solenoid(solenoid)
        
        steps = STEPS_HELT_ÅBEN // MANUEL_STEP_FRAKTION
        
        kør_steps(stepper_pins, steps, retning=1)
        
        deaktiver_solenoid(solenoid)
        
        sluk_stepper_motor(stepper_pins)

        vindue_status = "aaben"
        
        if nuværende_position >= STEPS_HELT_ÅBEN:
            vindue_status = "aaben"
            print("Vindue nu fuldt åbent")
        else:
            print("Position: {}/{}".format(nuværende_position, STEPS_HELT_ÅBEN))
        
    except Exception as fejl:
        print("Fejl under manuel åbning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        raise


def manuel_luk(stepper_pins, solenoid):

    global nuværende_position, vindue_status
    
    if nuværende_position <= 0:
        print("Vindue allerede lukket")
        return
    
    try:
        print("Manuel lukning (1/{})".format(MANUEL_STEP_FRAKTION))
        
        aktiver_solenoid(solenoid)
        
        steps = STEPS_HELT_ÅBEN // MANUEL_STEP_FRAKTION
        
        kør_steps(stepper_pins, steps, retning=-1)
        
        deaktiver_solenoid(solenoid)
        
        sluk_stepper_motor(stepper_pins)
        
        if nuværende_position <= 0:
            vindue_status = "lukket"
            print("Vindue nu fuldt lukket")
        else:
            vindue_status = "aaben"
            print("Position: {}/{}".format(nuværende_position, STEPS_HELT_ÅBEN))
        
    except Exception as fejl:
        print("Fejl under manuel lukning: {}".format(fejl))
        deaktiver_solenoid(solenoid)
        sluk_stepper_motor(stepper_pins)
        raise



def forbind_wifi():

    print("Forbinder til WiFi: {}".format(SSID))
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if wlan.isconnected():
        print("WiFi allerede forbundet")
        print("IP adresse: {}".format(wlan.ifconfig()[0]))
        return wlan
    
    wlan.connect(SSID, PASSWORD)
    
    timeout = WIFI_TIMEOUT
    while not wlan.isconnected() and timeout > 0:
        sleep(1)
        timeout -= 1
    
    if not wlan.isconnected():
        raise Exception("WiFi timeout efter {}sekunder".format(WIFI_TIMEOUT))
    
    print("WiFi forbundet")
    print("IP adresse: {}".format(wlan.ifconfig()[0]))
    
    return wlan



def send_status(klient):

    try:
        status_data = json.dumps({
            'status': vindue_status,
            'position': nuværende_position,
            'max_position': STEPS_HELT_ÅBEN
        })
        
        klient.publish(MQTT_TOPIC_STATUS, status_data)
        print("Status sendt: {}".format(status_data))
        
    except Exception as fejl:
        raise Exception("Status send fejl: {}".format(str(fejl)))


def mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, klient):

    try:
        data = json.loads(msg)
        kommando = data.get('kommando', '')
        
        print("MQTT kommando modtaget: {}".format(kommando))
        
        if kommando == 'aaben':
            åben_vindue(stepper_pins, solenoid, buzzer)
            send_status(klient)
        
        elif kommando == 'kort_aaben':
            kort_åben_vindue(stepper_pins, solenoid, buzzer, klient)
        
        elif kommando == 'luk':
            luk_vindue(stepper_pins, solenoid, buzzer)
            send_status(klient)
        
        elif kommando == 'manuel_aaben':
            manuel_åben(stepper_pins, solenoid)
            send_status(klient)
        
        elif kommando == 'manuel_luk':
            manuel_luk(stepper_pins, solenoid)
            send_status(klient)
        
        else:
            print("Ukendt kommando: {}".format(kommando))
    
    except Exception as fejl:
        print("Callback fejl: {}".format(fejl))



def main():

    try:
        stepper_pins, solenoid, buzzer = opsæt_hardware()
        
        forbind_wifi()
        
        klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
        
        def callback_wrapper(topic, msg):

            mqtt_callback(topic, msg, stepper_pins, solenoid, buzzer, klient)
        
        klient.set_callback(callback_wrapper)
        klient.connect()
        
        print("MQTT forbundet til broker: {}".format(MQTT_SERVER))
        
        klient.subscribe(MQTT_TOPIC_COMMAND)
        print("Subscribed til: {}".format(MQTT_TOPIC_COMMAND))
        
        send_status(klient)
        
        while True:
            try:
                klient.check_msg()
                
                sleep(MQTT_CHECK_INTERVAL)
                
            except Exception as fejl:
                print("Poll fejl: {}".format(fejl))
                
                try:
                    klient.disconnect()
                except:
                    pass
                
                sleep(5)
                
                try:
                    print("Reconnecting til MQTT")
                    forbind_wifi()
                    klient = MQTTClient(ENHEDS_ID, MQTT_SERVER)
                    klient.set_callback(callback_wrapper)
                    klient.connect()
                    klient.subscribe(MQTT_TOPIC_COMMAND)
                    print("Reconnect succesfuld")
                    
                except:
                    sleep(10)
    
    except Exception as fejl:
        print("Større fejl: {}".format(fejl))
        
        try:
            sluk_stepper_motor(stepper_pins)
            deaktiver_solenoid(solenoid)
            stop_buzzer(buzzer)
        except:
            pass
        
        sleep(10)



if __name__ == "__main__":

    while True:
        try:
            main()
        except KeyboardInterrupt:
            break
        except:
            sleep(10)