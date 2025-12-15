#!/usr/bin/env python3
"""
Vores hovedprogram til automatisk vindues-styring system.

Dette modul er systemets "hjerte" der:
- Starter alle tråde (BME680, MQTT, Sync, WebServer)
- Konfigurerer callbacks mellem komponenter
- Håndterer graceful shutdown ved SIGINT/SIGTERM
- Logger startup og shutdown events

Arkitektur:
    Main Thread -> Starter alle worker threads -> Holder system kørende
    -> Signal received -> Graceful shutdown
    
    Thread Hierarki:
    Main (non-daemon)
    - BME680 Sensor (daemon)
    - MQTT Client (daemon)
    - Sync Client (daemon)
    - FastAPI/Uvicorn (blocking, non-daemon)

Signal Handling:
    SIGINT (Ctrl+C): Bruger interrupt - trigger graceful shutdown
    SIGTERM (kill): System shutdown - trigger graceful shutdown
    
    Graceful shutdown sikrer:
    - Alle threads stoppes ordentligt
    - Database cleanup udføres
    - Ingen data går tabt

Startup Sekvens:
    1. Signal handlers først (sikrer Ctrl+C virker fra start)
    2. BME680 sensor (skal kunne modtage callbacks)
    3. MQTT client (skal kunne sende kommandoer fra BME680)
    4. Sync client (kan starte når database har data)
    5. Webserver sidst (blokerer main thread)

Brug:
    python3 main.py
"""

import signal
import sys
import traceback
from typing import Optional, NoReturn

from indoor_sensor import bme680_sensor
from mqtt import mqtt_klient
from sync_client import sync_klient
from database import db
from config import ENHEDS_ID


# Global state for signal handler
sensor_reference: Optional[object] = None
"""Global reference til BME680 sensor så signal handler kan tilgå den."""


def signal_handler(sig: int, frame: Optional[object]) -> NoReturn:
    """
    Handler for OS signals der trigger graceful shutdown.
    
    Kaldes automatisk af OS når SIGINT (Ctrl+C) eller SIGTERM (kill) modtages.
    
    Shutdown Sekvens:
        1. Log shutdown initiation
        2. Stop BME680 sensor thread
        3. Stop MQTT client thread
        4. Stop sync client thread
        5. Cleanup gammel database data (7 dage)
        6. Exit med success code (0)
    
    Args:
        sig: Signal nummer (SIGINT=2, SIGTERM=15)
        frame: Stack frame (ikke brugt - skal være der for funktionalitet)
    """
    # Log shutdown
    signal_navn = 'SIGINT' if sig == signal.SIGINT else 'SIGTERM'
    db.gem_system_log(ENHEDS_ID, 'Main', f'Shutdown initieret via {signal_navn}')
    
    print(f"\n{signal_navn} starter graceful shutdown")
    
    # Stop BME680 sensor
    if sensor_reference is not None:
        try:
            print("Stopper BME680 sensor")
            sensor_reference.stop()
            db.gem_system_log(ENHEDS_ID, 'Main', 'BME680 sensor stoppet')
        except Exception as fejl:
            db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af BME680: {fejl}")
    
    # Stop MQTT client
    try:
        print("Stopper MQTT client")
        mqtt_klient.stop()
        db.gem_system_log(ENHEDS_ID, 'Main', 'MQTT client stoppet')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af MQTT: {fejl}")
    
    # Stop sync client
    try:
        print("Stopper sync client")
        sync_klient.stop()
        db.gem_system_log(ENHEDS_ID, 'Main', 'Sync client stoppet')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Fejl ved stop af sync: {fejl}")
    
    # Database cleanup
    try:
        print("Rydder gammel database data")
        db.ryd_gammel_data(dage=7)
        db.gem_system_log(ENHEDS_ID, 'Main', 'Database cleanup udført (7 dage)')
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'Main', f"Database cleanup fejl: {fejl}")
    
    # Exit
    print("Graceful shutdown fuldført")
    db.gem_system_log(ENHEDS_ID, 'Main', 'Graceful shutdown fuldført')
    sys.exit(0)


def registrer_signal_handlers() -> None:
    """
    Registrerer OS signal handlers for graceful shutdown.
    
    Konfigurerer Python til at kalde signal_handler() når SIGINT eller
    SIGTERM modtages. Dette overskriver Python's default handlers som
    ville terminere programmet abrupt uden cleanup.
    """
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    db.gem_system_log(ENHEDS_ID, 'Main', 'Signal handlers registreret')


def initialiser_bme680_sensor() -> object:
    """
    Initialiserer og starter BME680 sensor thread.
    
    Konfigurerer callbacks til WebSocket og MQTT via dependency injection (DI).
    
    Returns:
        BME680Sensor instance der kører i egen thread
    """
    try:
        # Start sensor
        bme680_sensor.start()
        
        # Konfigurer WebSocket callback
        from app import notificer_websocket_klienter
        bme680_sensor.sæt_websocket_callback(notificer_websocket_klienter)
        
        # Konfigurer MQTT callback
        bme680_sensor.sæt_mqtt_klient(mqtt_klient)
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'BME680 sensor startet')
        return bme680_sensor
        
    except Exception as fejl:
        fejl_besked = f"BME680 initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def initialiser_mqtt_klient() -> None:
    """
    Initialiserer og starter MQTT client thread.
    
    Konfigurerer WebSocket callback og starter forbindelse til broker.
    """
    try:
        # Konfigurer WebSocket callback
        from app import notificer_websocket_klienter
        mqtt_klient.sæt_websocket_callback(notificer_websocket_klienter)
        
        # Start MQTT client
        mqtt_klient.start()
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'MQTT client startet')
        
    except Exception as fejl:
        fejl_besked = f"MQTT initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def initialiser_sync_klient() -> None:
    """
    Initialiserer og starter sync_client thread.
    
    Starter background thread der periodisk uploader data til remote server.
    Thread starter med 30 sekund delay for at give tid til netværk.
    """
    try:
        # Start sync client
        sync_klient.start()
        
        db.gem_system_log(
            ENHEDS_ID,
            'Main',
            'Sync client startet (30 sekunders initial delay)'
        )
        
    except Exception as fejl:
        fejl_besked = f"Sync initialiserings fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        raise RuntimeError(fejl_besked)


def start_webserver_blocking() -> NoReturn:
    """
    Starter FastAPI webserver i main thread (blocking).
    
    Dette er sidste kald i main() da start_webserver() blokerer indtil
    shutdown signal modtages.
    """
    try:
        from app import start_webserver
        
        db.gem_system_log(ENHEDS_ID, 'Main', 'Starter FastAPI webserver')
        print("Starter webserver - system nu fuldt operationelt")
        
        # Blocking call - kører indtil SIGINT/SIGTERM
        start_webserver()
        
    except Exception as fejl:
        fejl_besked = f"Webserver crash: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        
        # Log traceback for detaljeret error logging
        full_error = traceback.format_exc()
        db.gem_fejl(ENHEDS_ID, 'Main', full_error)
        
        sys.exit(1)


def main() -> NoReturn:
    """
    Hovedfunktionen - starter hele systemet.
    
    Koordinerer startup af alle komponenter i korrekt rækkefølge med
    dependency injection og error handling.
    
    Startup Order:
        1. Registrer signal handlers (sikrer Ctrl+C virker)
        2. BME680 sensor (skal kunne modtage callbacks)
        3. MQTT client (skal kunne sende kommandoer)
        4. Sync client (uploader data i baggrunden)
        5. Webserver (blokerer main thread)
    """
    # Opdater global reference for signal handler
    global sensor_reference
    
    try:
        # Signal handlers
        print("Registrerer signal handlers")
        registrer_signal_handlers()
        
        # Startup logging
        print("Starter automatisk vindues-styringssystem")
        # Skaber en opdeling i databasen så det er nemt at se hvornår der har været genstart
        # Bedre læsbarhed, men skaber også rækker som bare fungerer som fyld
        db.gem_system_log(ENHEDS_ID, 'Main', '-' * 50)
        db.gem_system_log(ENHEDS_ID, 'Main', 'System startup initieret')
        db.gem_system_log(ENHEDS_ID, 'Main', '-' * 50)
        
        # BME680 sensor
        print("Initialiserer BME680 indoor sensor")
        sensor_reference = initialiser_bme680_sensor()
        
        # MQTT client
        print("Initialiserer MQTT client")
        initialiser_mqtt_klient()
        
        # Sync client
        print("Initialiserer remote sync client")
        initialiser_sync_klient()
        
        # Webserver (blocking)
        start_webserver_blocking()
        
    except ImportError as fejl:
        fejl_besked = f"Import fejl - manglende dependency: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except RuntimeError as fejl:
        fejl_besked = f"Runtime fejl under startup: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except OSError as fejl:
        fejl_besked = f"OS fejl - hardware eller filesystem: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        sys.exit(1)
    
    except Exception as fejl:
        fejl_besked = f"Uventet fejl under startup: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'Main', fejl_besked)
        
        # Traceback logging for detaljeret error logging
        full_error = traceback.format_exc()
        db.gem_fejl(ENHEDS_ID, 'Main', full_error)
        
        sys.exit(1)


if __name__ == "__main__":
    """
    Entry point når script køres direkte.
    
    Usage:
        python3 main.py
        
        Vil blive integreret som en "systemfil" på vores lokal server
    """
    main()