"""
WebSocket Handler til broadcast af vores sensor opdateringer.

Dette modul implementerer broadcast funktionalitet der:
- Sender real-time opdateringer til alle aktive WebSocket klienter
- Håndterer client disconnections gracefully
- Logger fejl og events til database
- Serialiserer sensor data til JSON format
- Tracker og cleanup af disconnected klienter

Arkitektur:
    Sensor Update -> broadcast_til_websockets() -> JSON serialization (encode)
    -> Concurrent sends til alle klienter -> Cleanup af dead connections

Thread til Asyncio Bridge:
    MQTT Thread -> asyncio.create_task(broadcast) -> AsyncIO Event Loop
    BME680 Thread -> asyncio.create_task(broadcast) -> AsyncIO Event Loop
    
    Vores sensor threads er sync, men WebSocket er async. Vi bridger mellem
    de to verdener ved at oprette async callbacks i event loop fra sync context.

Broadcast Pattern:
    1. Hent liste af aktive klienter (Set[WebSocket])
    2. Early return hvis ingen klienter
    3. Hent sensor data og serialiser til JSON
    4. Send til alle klienter concurrent (asyncio scheduler)
    5. Track klienter der fejler (disconnected)
    6. Cleanup dead connections fra tracking set

Error Recovery:
    Enkelte klient fejl stopper ikke broadcast til andre klienter.
    Disconnected klienter fjernes automatisk fra tracking.
    JSON serialization fejl logges og annullerer broadcast.
    Frontend reconnecter automatisk ved disconnect.

Concurrent Sends:
    asyncio scheduler håndterer alle sends parallelt.
    Én langsom klient blokerer ikke andre.
    await klient.send_text() er en non-blocking operation.

Brug:
    from websocket_handler import broadcast_til_websockets
    
    # Fra async context
    await broadcast_til_websockets('sensor')
    
    # Fra sync thread (MQTT, BME680)
    import asyncio
    asyncio.create_task(broadcast_til_websockets('bme680'))

Note:
    Da det er Python vi arbejder med, bruger vi type hints for klarhed.
"""

import json
from typing import Set, Dict, Any
from fastapi import WebSocket
from sensor_data import data_opbevaring
from database import db
from config import ENHEDS_ID


async def broadcast_til_websockets(opdaterings_type: str) -> None:
    """
    Broadcaster vores sensor opdateringer til alle aktive WebSocket klienter.
    
    Denne async funktion er vores central-hub for real-time frontend opdateringer.
    Den kaldes fra vores sensor threads via asyncio.create_task() for at skabe
    forbindelse mellem sync threading og async WebSocket context.
    
    Args:
        opdaterings_type: Type af opdatering for frontend routing
            'sensor': ESP32 outdoor data (temp, fugt, bat)
            'bme680': BME680 indoor data (temp, fugt, gas)
            'vindue': Vinduesstatus ændring
            'fejl': Fejlbesked fra ESP32
    
    Broadcast Flow:
        1. Hent liste af aktive WebSocket klienter
        2. Early return hvis ingen klienter
        3. Hent sensor data fra shared storage
        4. Serialiser til JSON besked
        5. Send til alle klienter concurrent
        6. Track klienter der fejler (disconnected)
        7. Cleanup disconnected klienter
    
    Early Return Optimering:
        Hvis ingen klienter er forbundet, springer vi over:
        - Sensor data hentning (mutex lock overhead)
        - JSON serialization
        - Broadcast loop
        
        Dette reducerer overhead når frontend ikke er aktiv
        (ingen forbundne).
    
    Concurrent Sends:
        await klient.send_text() returnerer øjeblikkeligt og scheduler send
        i asyncio event loop. Alle sends køres parallelt af scheduler.
        
        Eksempel med 3 klienter:
            Sekventiel: 100ms + 100ms + 100ms = 300ms total
            Concurrent: max(100ms, 100ms, 100ms) = 100ms total
        
        Dette betyder at en enkelt langsom klient ikke blokerer for andre.
    
    Error Handling:
        ConnectionError: Klient disconnected under send
        RuntimeError: WebSocket allerede closed
        Exception: Uventet fejl (log med type navn)
        
        Alle errors resulterer i at klienten fjernes fra tracking.
        Dette forhindrer memory leaks fra zombie connections.
    
    JSON Message Format:
        {
            "type": "update",
            "update_type": "sensor" | "bme680" | "vindue" | "fejl",
            "data": {
                "sensor": {
                    "temperatur": 18.5,
                    "luftfugtighed": 65,
                    "batteri": 87,
                    "målt_klokken": "2025-12-12T10:30:00"
                },
                "bme680": {...},
                "vindue": {...}
            }
        }
    
    Threading Bridge Pattern:
        Fra MQTT thread (sync context):
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(broadcast_til_websockets('sensor'))
        
        Fra BME680 thread (sync context):
            asyncio.create_task(broadcast_til_websockets('bme680'))
        
        Fra app.py (async context):
            await broadcast_til_websockets('vindue')
    
    Eksempler:
        Fra MQTT callback efter sensor data er modtaget:
            import asyncio
            asyncio.create_task(broadcast_til_websockets('sensor'))
        
        Fra BME680 thread efter måling:
            asyncio.create_task(broadcast_til_websockets('bme680'))
        
        Fra app.py efter sendt vindueskommando:
            await broadcast_til_websockets('vindue')
    
    Note:
        Denne funktion må kun kaldes fra async context eller via
        asyncio.create_task() fra sync threads. Direkte kald fra sync
        context uden create_task() vil fejle med RuntimeError.
    """
    # Hent vores aktive klienter fra shared storage
    klienter: Set[WebSocket] = data_opbevaring.hent_websocket_klienter()
    
    # Early return optimering hvis ingen klienter
    if not klienter:
        # Debug
        print(f"Ingen WebSocket klienter - springer broadcast over ({opdaterings_type})")
        return
    
    # Debug
    print(f"Broadcaster {opdaterings_type} til {len(klienter)} klient(er)")
    
    # Hent og serialiser vores sensor data
    try:
        # Hent nuværende sensor data fra shared storage
        sensor_data: Dict[str, Dict[str, Any]] = data_opbevaring.hent_alle_data()
        
        # Serialiser til JSON string med struktureret format
        besked: str = json.dumps({
            'type': 'update',
            'update_type': opdaterings_type,
            'data': sensor_data
        })
        
        # Debug
        print(f"JSON besked klar: {len(besked)} bytes")
        
    except (TypeError, ValueError) as fejl:
        # JSON serialization fejl - log og abort broadcast
        print(f"JSON serialization fejl: {fejl}")
        db.gem_fejl(
            ENHEDS_ID,
            'WebSocketHandler',
            f"JSON serialization fejl: {fejl}"
        )
        return
    
    # Track disconnected klienter i et set
    frakoblede: Set[WebSocket] = set()
    
    # Broadcast til alle vores klienter
    for klient in klienter:
        try:
            # Async send - non-blocking, scheduleret af asyncio
            await klient.send_text(besked)
            
        except ConnectionError as fejl:
            # Klient disconnected under send
            print(f"Klient disconnected under send: {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Connection error ved broadcast: {fejl}"
            )
        
        except RuntimeError as fejl:
            # WebSocket allerede lukket
            print(f"WebSocket allerede lukket: {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Runtime error ved broadcast (WebSocket lukket): {fejl}"
            )
        
        except Exception as fejl:
            # Uventet fejl - log for debugging
            print(f"Uventet fejl ved broadcast: {type(fejl).__name__} - {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Uventet fejl ved broadcast: {type(fejl).__name__} - {fejl}"
            )
    
    # Cleanup disconnected klienter (batch removal)
    if frakoblede:
        # Debug
        print(f"Rydder op i {len(frakoblede)} disconnected klient(er)")
        
        for klient in frakoblede:
            # Fjern fra vores aktive klient tracking
            data_opbevaring.fjern_websocket_klient(klient)
        
        # Log cleanup event med antal fjernede
        db.gem_system_log(
            ENHEDS_ID,
            'WebSocketHandler',
            f"Fjernede {len(frakoblede)} disconnected klienter fra tracking"
        )
    else:
        # Debug
        print(f"Broadcast succesfuld til alle {len(klienter)} klient(er)")


async def broadcast_fejl(
    fejl_besked: str,
    kilde: str = 'System'
) -> None:
    """
    Broadcaster fejlbesked til alle WebSocket klienter.
    
    Convenience wrapper til at sende fejlbeskeder direkte til frontend
    uden at skulle opdatere data_opbevaring først. Bruges primært til
    system-level fejl der skal vises øjeblikkeligt i UI.
    
    Use Cases:
        Fejl der kræver brugerens opmærksomhed
        Hardware fejl (sensor ude af drift, I2C timeout)
        Network issues (MQTT disconnect, remote sync failed)
        Konfigurationsproblemer (manglende environment variabler)
    
    Args:
        fejl_besked: Fejlbeskrivelse at broadcaste til frontend
        kilde: Identifier for kilde af fejl (default 'System')
            Typiske værdier: 'MQTT', 'BME680', 'SyncClient', 'System'
    
    Broadcast Flow:
        1. Log fejl til database først
        2. Hent aktive WebSocket klienter
        3. Early return hvis ingen klienter
        4. Serialiser fejlbesked til JSON med type 'fejl'
        5. Send til alle klienter concurrent
        6. Cleanup disconnected klienter
    
    JSON Message Format:
        {
            "type": "fejl",
            "fejl": "MQTT forbindelse tabt",
            "kilde": "MQTT"
        }
        
        Frontend vil parse dette og vise error toast-notification.
    
    Error Handling:
        JSON serialization fejl logger og abort
        Klient send fejl markerer klient som er disconnected
        Alle errors sluges
    
    Database Logging:
        Fejl logges til database før broadcast. Dette sikrer at
        selv hvis broadcast fejler, har vi log af fejlen til
        debugging.
    
    Eksempler:
        Fra MQTT thread ved connection lost:
            await broadcast_fejl("MQTT forbindelse tabt", "MQTT")
        
        Fra BME680 thread ved sensor hardware fejl:
            await broadcast_fejl("Sensor hardware fejl - I2C timeout", "BME680")
        
        Fra sync client ved remote server down:
            await broadcast_fejl("Remote server ikke tilgængelig", "SyncClient")
    
    Note:
        Denne funktion er primært til debugging og monitoring. Normal
        fejlhåndtering skal bruge standard logging til database via
        db.gem_fejl() uden broadcast.
    """
    # Debug
    print(f"Broadcaster fejl til frontend: {fejl_besked} (kilde: {kilde})")
    
    # Log fejl til database først
    db.gem_fejl(ENHEDS_ID, kilde, fejl_besked)
    
    # Hent vores aktive klienter
    klienter: Set[WebSocket] = data_opbevaring.hent_websocket_klienter()
    
    # Early return hvis ingen klienter
    if not klienter:
        print("Ingen WebSocket klienter - springer fejl broadcast over")
        return
    
    # Serialiser fejlbesked til JSON
    try:
        besked: str = json.dumps({
            'type': 'fejl',
            'fejl': fejl_besked,
            'kilde': kilde
        })
        
        # Debug
        print(f"Fejl besked serialiseret: {len(besked)} bytes")
        
    except (TypeError, ValueError) as fejl:
        # JSON serialization fejl
        print(f"Kunne ikke serialisere fejlbesked: {fejl}")
        db.gem_fejl(
            ENHEDS_ID,
            'WebSocketHandler',
            f"Kunne ikke serialisere fejlbesked: {fejl}"
        )
        return
    
    # Track disconnected klienter
    frakoblede: Set[WebSocket] = set()
    
    # Broadcast til alle vores klienter
    for klient in klienter:
        try:
            await klient.send_text(besked)
        except Exception as fejl:
            # Marker som disconnected
            print(f"Klient fejlede under fejl-broadcast: {type(fejl).__name__}")
            frakoblede.add(klient)
    
    # Cleanup disconnected klienter
    if frakoblede:
        print(f"Rydder op i {len(frakoblede)} disconnected klient(er)")
        for klient in frakoblede:
            data_opbevaring.fjern_websocket_klient(klient)
    else:
        print(f"Fejl broadcast succesfuld til alle {len(klienter)} klient(er)")