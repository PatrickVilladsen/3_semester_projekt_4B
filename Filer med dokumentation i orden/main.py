#!/usr/bin/env python3
import signal
import sys
import traceback
from indoor_sensor import BME680Sensor
from mqtt import mqtt_client
from app import start_web_server, set_bme680_sensor, notify_websocket_clients
from sync_client import sync_client
from database import db

#Denne kodes formål er at være limen til hele vores backend

# Global reference til vores bme sensor så den kan tilgås senere i koden.
# Sættes til none da værdien oprettes senere, så none er bare en placeholder
bme680_sensor = None

# Sikrer at vi lukker tingene korrekt - hedder graceful shutdown 
#Det gør at komponenterne ikke bliver låst i en "fail-state"
def signal_kontrol(sig, frame):    
    # Stopper vores forskellige tråde
    if bme680_sensor:
        bme680_sensor.stop()
    
    mqtt_client.stop()

    sync_client.stop()
    
    #Rengør database for alt som er ældre end 7 dage
    try:
        db.cleanup_old_data(days=7)
    except Exception as e:
        db.log_error('MAIN_SHUTDOWN', f"Fejl under database oprydning: {e}")
    
    sys.exit(0)

# Dette er vores hovedprogram der sørger for alle koderne starter op
def main():
    # Giver mulighed for at ændre værdien for sensoren
    global bme680_sensor
    
    # Dette er de signaler som udløser vores graceful shutdown
    # Signint er er signal interrupt som er ctrl + c
    signal.signal(signal.SIGINT, signal_kontrol)
    # sigterm er signal termination som er for kill-kommando
    signal.signal(signal.SIGTERM, signal_kontrol)
    
    #Nu starter vi vores sensor og mqtt op
    try:
        # Vi åbner for at bme sensoren kan få en ny værdi
        bme680_sensor = BME680Sensor()
        
        # Callback setup, vi fortæller hvilken vej dataen fra sensoren skal til
        # I dette tilfælde skal det bruges i funktionen notify_websocket_clients som er i app.py
        # Fungerer lidt som en adresse på hvor data'en skal sendes hen.
        bme680_sensor.set_websocket_callback(notify_websocket_clients)
        
        # Her starter vi så objektet som en seperat thread - der kunne lige så godt så threading.Thread i parantesen. 
        bme680_sensor.start()
        
        # Bruges til at at gøre sensorens værdi redigerbart i app.py koden
        set_bme680_sensor(bme680_sensor)

        '''
        Så for lige at gennem processen der sker med BME eksemplet.
        Vi har bme680_sensor som vi startede med at oprette i toppen.
        I starten af main gjorde vi så at dens værdi kunne ændres.
        Vi kobler den så på BME680Sensor-klassen fra indoor_sensor.py.
        Dette gør at dens værdi bliver styret fra den kode.
        Vi sætter så efterfølgende en callback up til vores app.py
        Det gør at funktionen "notift_websocket_clients" modtager dataen
        Vi starter så tråden op bagefter - den læser nu dataen
        Den sidste del med set_bme680_sensor bruges til at gøre
        værdierne fra sensorne redigerbart i app.py, da bme680_sensor_instance
        gøres globalt i set_bme680_sensor funktionen. '''
        
        # Start MQTT client thread        
        # Callback setup, vi fortæller hvilken vej dataen fra sensoren skal til - ligesom med BME
        mqtt_client.set_websocket_callback(notify_websocket_clients)
        
        #Samme som BME længere oppe
        mqtt_client.start()

        bme680_sensor.set_mqtt_client(mqtt_client)
        
        sync_client.start()

        # her kører vi funktionen "start_web_server" fra app.py
        start_web_server()
        
    except Exception as e:
        db.log_error('MAIN', str(e))
        # Stopper koden ved fejl med (1) som betyder failure
        full_error = traceback.format_exc()
        db.log_error('MAIN_TRACE', full_error)
        sys.exit(1)

# Kører koden
if __name__ == "__main__":
    main()
