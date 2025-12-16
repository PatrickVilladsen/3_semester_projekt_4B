"""
FastAPI Webserver til automatisk vinduesstyringssystem.

Dette modul implementerer hele web interfacet der:
- Serverer HTML frontend til touchscreen
- Håndterer REST API endpoints til data hentning
- Broadcaster real-time opdateringer via WebSocket
- Genererer matplotlib grafer til visualisering
- Konfigurerer callback bridges mellem threading og asyncio

Arkitektur:
    FastAPI (ASGI) -> Uvicorn -> AsyncIO Event Loop
    - Static Files: HTML, CSS, JavaScript
    - REST Endpoints: JSON data til frontend
    - WebSocket: Real-time bidirectional kommunikation
    - Callbacks: BME680 thread -> WebSocket notifications

WebSocket Flow:
    Sensor Thread -> notificer_websocket_klienter() -> websocket_handler.broadcast
    -> Alle aktive klienter modtager opdatering -> Frontend opdateres

Threading Bridge:
    BME680/MQTT threads er sync, WebSocket er async. Vi bridger mellem
    de to verdener via asyncio.create_task() som scheduler callbacks i
    event loop uden at blokere sensor threads.
    
Brug:
    from app import start_webserver
    start_webserver()  # Blokerer indtil shutdown signal
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any
import json
import re
import asyncio

from database import db
from sensor_data import data_opbevaring
from graph_generator import graf_generator
from websocket_handler import broadcast_til_websockets
from config import WEB_HOST, WEB_PORT, GRÆNSER, ENHEDS_ID
import uvicorn


# Global sensor reference til callback setup
_bme680_sensor_instans: Optional[Any] = None
"""
Global reference til BME680 sensor instance.

Sættes af main.py via sæt_bme680_sensor() efter sensor thread starter.
Bruges i lifespan context manager til at opsætte callback bridge mellem
sensor thread og WebSocket event loop.
"""


def sæt_bme680_sensor(sensor: Any) -> None:
    """
    Registrerer BME680 sensor instans globalt for callback-setup.
    
    Kaldes fra main.py efter BME680 thread er startet. Dette tillader
    lifespan context manager at konfigurere WebSocket callback på sensor.
    
    Args:
        sensor: BME680Sensor instans fra indoor_sensor.py
    
    Funktion:
        Opdaterer global _bme680_sensor_instans variabel
    """
    global _bme680_sensor_instans
    _bme680_sensor_instans = sensor


async def notificer_websocket_klienter(opdaterings_type: str) -> None:
    """
    Callback wrapper til WebSocket broadcasts fra sensor threads.
    
    Denne funktion er callback bridge mellem sync threads (MQTT, BME680)
    og async WebSocket system. Den delegerer til websocket_handler modulet
    som håndterer alt broadcast logikken.
    
    Args:
        opdaterings_type: Type af opdatering for frontend routing
            'sensor': ESP32 outdoor data (temp, fugt, bat)
            'bme680': BME680 indoor data (temp, fugt, gas)
            'vindue': Vindues-status ændring
            'fejl': Fejlbesked fra ESP32
    
    Threading Bridge:
        Sensor threads kalder denne funktion via asyncio.create_task()
        for at bridge mellem sync og async contexts.
    
    Delegation:
        Alt broadcast-logik håndteres af websocket_handler modulet.
        Dette undgår duplikering og holder ansvarsområder separeret.
    
    Eksempler:
        Fra MQTT thread:
        import asyncio
        asyncio.create_task(notificer_websocket_klienter('sensor'))
        
        Fra BME680 thread:
        asyncio.create_task(notificer_websocket_klienter('bme680'))
    
    Note:
        Denne funktion må ikke kaldes direkte fra sync context.
        Brug altid asyncio.create_task() eller await fra async context.
    """
    await broadcast_til_websockets(opdaterings_type)


# Lifespan context manager til startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager til applikations lifecycle management.
    
    Håndterer startup og shutdown events for FastAPI applikation.
    Ved startup opsættes callback bridges mellem sensor threads og
    WebSocket event loop. Ved shutdown kunne der indføres cleanup i fremtiden
    
    Startup Flow:
        1. Tjek om BME680 sensor instans er tilgængelig
        2. Hvis ja: Opsæt WebSocket callback på sensor
        3. Log startup event til database
        4. Hvis nej: Log error (sensor kan stadig køre uden callbacks)
    
    Callback Bridge:
        BME680 sensor thread kalder notificer_websocket_klienter() via
        callback. Dette bridger sync threading context til async WebSocket
        event loop for real-time frontend opdateringer.
    
    Args:
        app: FastAPI application instans
    
    Yields:
        Kontrollen returneres til FastAPI runtime når opstartsfasen er forbi
        og webserveren kan køre.
    
    Funktion:
        Opsætter BME680 sensor WebSocket callback hvis tilgængelig
        Logger startup/shutdown events til database
    
    Note:
        Lifespan erstatter @app.on_event decorators og er
        brugt i flere moderne FastAPI eksempler.
    """
    # Startup fase
    if _bme680_sensor_instans:
        # Opsæt callback bridge til WebSocket
        _bme680_sensor_instans.sæt_websocket_callback(
            notificer_websocket_klienter
        )
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'BME680 WebSocket callback konfigureret')
    else:
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', 'BME680 sensor instance ikke tilgængelig')
    
    # Yield til FastAPI runtime
    yield
    
    # Shutdown fase
    db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'Webserver shutdown påbegyndt')


# Opret FastAPI application
app = FastAPI(
    title="Automatisk Udluftningssystem",
    version="1.0.0",
    description="IoT-baseret automatisk vindues-styring med fokus på indeklima optimering",
    lifespan=lifespan
)

# CORS middleware til cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Tillad alle origins (development)
    allow_credentials=True,     # Tillad credentials (cookies, tokens)
    allow_methods=["*"],        # Tillad alle HTTP metoder
    allow_headers=["*"],        # Tillad alle headers
)

# Mount static files (HTML, CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")


# REST API Endpoints

@app.get("/")
async def rod() -> FileResponse:
    """
    Serverer HTML frontend fra static directory.
    
    Root endpoint der loader hele SPA frontend. Frontend er single-page
    application med JavaScript der håndterer UI og WebSocket kommunikation.
    
    Returns:
        FileResponse med index.html
    """
    return FileResponse('static/index.html')


@app.get("/api/data")
async def hent_data() -> Dict[str, Any]:
    """
    Henter nuværende sensor data fra alle kilder.
    
    Returnerer snapshot af al tilgængelig sensor data. Frontend bruger
    dette til initial data load og fallback hvis WebSocket fejler.
    
    Returns:
        Dictionary med tre keys:
        sensor: ESP32 outdoor data (temperatur, luftfugtighed, batteri)
        bme680: BME680 indoor data (temperatur, luftfugtighed, gas)
        vindue: Vindues status (status, position, max_position)
    
    Response Format:
        {
            "sensor": {
                "temperatur": 18.5,
                "luftfugtighed": 65,
                "batteri": 87,
                "målt_klokken": "2025-12-12T10:30:00"
            },
            "bme680": {
                "temperatur": 22.3,
                "luftfugtighed": 55,
                "gas": 48500,
                "målt_klokken": "2025-12-12T10:30:05"
            },
            "vindue": {
                "status": "lukket",
                "position": 0,
                "max_position": 1000,
                "målt_klokken": "2025-12-12T10:25:00"
            }
        }
    """
    data = data_opbevaring.hent_alle_data()
    return data


@app.get("/api/thresholds")
async def hent_grænseværdier() -> Dict[str, Dict[str, Any]]:
    """
    Henter klima grænseværdier til frontend styling.
    
    Frontend bruger disse værdier til at bestemme display farver:
    Normal (grøn): Inden for komfort zone
    Grænse (gul): Tæt på grænse
    Kritisk (rød): Over/under kritisk grænse
    
    Returns:
        GRÆNSER dictionary fra config.py med temp, luftfugtighed og gas grænser
    
    Response Format:
        {
            "temp": {"limit_low": 19, "limit_high": 22, "max": 25},
            "luftfugtighed": {"limit_low": 40, "limit_high": 60, "max": 75},
            "gas": {"limit_line": 45000, "min": 25000}
        }
    """
    return GRÆNSER


@app.get("/api/historical/{data_type}")
async def hent_historik(
    data_type: str,
    dage: int = 7
) -> Dict[str, Any]:
    """
    Henter historik af sensor data til graf plotting.
    
    Queries database for sensor målinger inden for specificeret tidsperiode.
    Data returneres sorteret kronologisk for nem graf plotting i frontend.
    
    Args:
        data_type: Type af måling ('temperatur', 'luftfugtighed', 'gas')
        dage: Antal dage historik at hente (default 7, max 30)
    
    Returns:
        Dictionary med data_type og liste af målinger
    
    Raises:
        HTTPException 400: Hvis data_type ugyldig
    
    Response Format:
        {
            "data_type": "temperatur",
            "data": [
                {
                    "målt_klokken": "2025-12-12T00:00:00",
                    "værdi": 22.5,
                    "kilde": "BME680"
                },
                ...
            ]
        }
    
    Note:
        Data kan indeholde målinger fra flere kilder (indendørs og udendørs).
        Frontend farve-koder baseret på kilden.
    """
    gyldige_typer = ['temperatur', 'luftfugtighed', 'gas']
    
    if data_type not in gyldige_typer:
        raise HTTPException(
            status_code=400,
            detail=f"Ugyldig data type. Gyldige: {', '.join(gyldige_typer)}"
        )
    
    data = db.hent_datahistorik(data_type, dage)
    return {"data_type": data_type, "data": data}

@app.get("/api/debug/kilder")
async def debug_sensor_kilder() -> Dict[str, Any]:
    """Endpoint til debug for at se alle sensor kilder i databasen."""
    try:
        # Hent temperatur data fra sidste 7 dage
        temp_data = db.hent_datahistorik('temperatur', dage=7)
        
        # Saml alle unikke kilder
        kilder = set()
        for måling in temp_data:
            kilder.add(måling.get('kilde', 'UKENDT'))
        
        return {
            "antal_målinger": len(temp_data),
            "unikke_kilder": sorted(list(kilder)),
            "eksempel_målinger": temp_data[:5] if temp_data else []
        }
    
    except Exception as fejl:
        return {
            "fejl": str(fejl),
            "antal_målinger": 0,
            "unikke_kilder": [],
            "eksempel_målinger": []
        }

@app.get("/api/graf/{graf_type}")
async def hent_graf(
    graf_type: str,
    dage: int = 7
) -> StreamingResponse:
    """
    Genererer og returnerer matplotlib graf som PNG billede.
    
    Server-side graf rendering der genererer matplotlib visualisering og
    streamer de genereret PNG-billeder direkte til browser. Frontend roterer mellem
    graf typer automatisk hver 15. sekund.
    
    Args:
        graf_type: Type af graf ('temperatur', 'luftfugtighed', 'gas')
        dage: Antal dage historik at vise (1-30, default 7)
    
    Returns:
        StreamingResponse med PNG billede og no-cache headers
    
    Raises:
        HTTPException 400: Hvis graf_type er ugyldig eller dage er "out of range"
        HTTPException 500: Hvis graf generering fejler
    
    Headers:
        Cache-Control: no-cache (tvinger browser reload)
        Content-Type: image/png
        
    Eksempler:
        GET /api/graph/temperatur?dage=7
        GET /api/graph/luftfugtighed?dage=7
    """
    gyldige_typer = ['temperatur', 'luftfugtighed', 'gas']
    
    # Valider graf type
    if graf_type not in gyldige_typer:
        raise HTTPException(
            status_code=400,
            detail=f"Ugyldig graf type. Gyldige: {', '.join(gyldige_typer)}"
        )
    
    # Valider dage range
    if dage < 1 or dage > 30:
        raise HTTPException(
            status_code=400,
            detail="dage skal være mellem 1 og 30"
        )
    
    try:
        # Generer matplotlib graf til BytesIO buffer
        billede_buffer = graf_generator.generer_graf(graf_type, dage)
        
        # Stream PNG til browser med no-cache headers
        return StreamingResponse(
            billede_buffer,
            media_type="image/png",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    
    except Exception as fejl:
        db.gem_fejl(
            ENHEDS_ID,
            'GRAPH_GENERATOR',
            f"Fejl ved generering af {graf_type} graf: {fejl}"
        )
        raise HTTPException(
            status_code=500,
            detail="Graf kunne ikke genereres"
        )


# Validation patterns til kommando sikkerhed
KOMMANDO_SYNTAX = re.compile(r'^[a-z_]+$')
"""Regex pattern til validering af vindueskommandoer (kun lowercase og underscore)."""

TILLADTE_KOMMANDOER = [
    'aaben',
    'luk',
    'manuel_aaben',
    'manuel_luk',
    'kort_aaben'
]
"""Whitelist af gyldige vindues kommandoer."""


@app.post("/api/vindue/{kommando}")
async def kontroller_vindue(kommando: str) -> Dict[str, str]:
    """
    Sender vindueskommando via MQTT til ESP32.
    
    Validerer kommando mod regex og whitelist før forsendelse til MQTT.
    Dette forhindrer injection attacks og arbitrary kommandoer (ikke godkendt kommandoer).
    
    Args:
        kommando: Vindues kommando ('aaben', 'luk', 'manuel_aaben',
                 'manuel_luk', 'kort_aaben')
    
    Returns:
        Dictionary med status og echo af kommando
    
    Raises:
        HTTPException 400: Hvis kommando format ugyldig eller ikke i whitelist
        HTTPException 503: Hvis MQTT forbindelse ikke tilgængelig
    
    Validation:
        1. Regex check: Kun lowercase letters og underscore
        2. Whitelist check: Kommando skal være i TILLADTE_KOMMANDOER
    
    Security:
        Ingen authentication (lokalt netværk trusted)
        Regex validering forhindrer injection
        Whitelist forhindrer arbitrary kommandoer
    
    Response Format:
        {"status": "success", "kommando": "aaben"}
    
    Note:
        Kommando sendes asynkront via MQTT. Confirmation kommer via
        WebSocket når ESP32 acknowledger med vindue/status update.
    """
    # Regex validering
    if not KOMMANDO_SYNTAX.match(kommando):
        raise HTTPException(
            status_code=400,
            detail="Ugyldig kommando format (kun lowercase og underscore)"
        )
    
    # Whitelist validering
    if kommando not in TILLADTE_KOMMANDOER:
        raise HTTPException(
            status_code=400,
            detail=f"Ukendt kommando. Gyldige: {', '.join(TILLADTE_KOMMANDOER)}"
        )
    # Override ved manuel åben
    if kommando in ['manuel_aaben', 'manuel_luk']:
        from climate_controller import klima_controller # Import lokalt for at undgå circular dependency - her vinder funktionalitet over best practice
        
        if kommando == 'manuel_aaben':
            klima_controller.annuller_manuel_override_hvis_manuel_åben(kommando)

        klima_controller.aktiver_manuel_override(kommando)
        db.gem_system_log(
                ENHEDS_ID,
                'WEB_SERVER',
                f'Manuel override aktiveret (REST): {kommando}'
            )

    # Import lokalt for at undgå circular dependency - her vinder funktionalitet over best practice
    from mqtt import mqtt_klient
    
    try:
        mqtt_klient.publicer_kommando(kommando)
        return {"status": "success", "kommando": kommando}
    
    except Exception as fejl:
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"MQTT publish fejl: {fejl}")
        raise HTTPException(
            status_code=503,
            detail="MQTT forbindelse ikke tilgængelig"
        )


# WebSocket Endpoint

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint til real-time bidirectional kommunikation.
    
    Håndterer persistent WebSocket forbindelse til frontend for real-time
    sensor-opdateringer og vindueskommandoer. Implementerer polling loop
    med 1 sekund timeout for periodiske opdateringer.
    
    Connection Lifecycle:
        1. Accept: Accepter incoming WebSocket connection
        2. Register: Tilføj til aktive klienter i data_opbevaring
        3. Initial Send: Send nuværende state til klient
        4. Loop: Lyt efter klient beskeder og send periodiske opdateringer
        5. Disconnect: Cleanup klient fra aktive liste
    
    Client -> Server Messages:
        {'type': 'vindue_command', 'kommando': 'aaben'}
        -> Valideres og sendes til MQTT
        
        {'type': 'get_data'}
        -> Returnerer nuværende sensor data
    
    Server -> Client Messages:
        {'type': 'initial', 'data': {...}, '': {...}}
        -> Initial state ved forbindelse
        
        {'type': 'update', 'update_type': 'sensor', 'data': {...}}
        -> Sensor opdatering fra MQTT/BME680 thread
        
        {'type': 'update', 'update_type': 'periodic', 'data': {...}}
        -> Periodisk polling opdatering (1 sekund interval)
        
        {'type': 'command_sent', 'kommando': 'aaben'}
        -> Bekræftelse på sendt kommando
    
    Polling Loop:
        WebSocket lytter med 1 sekund timeout. Ved timeout sendes
        periodisk opdatering hvis 1+ sekund siden sidste send.
        Dette sikrer frontend altid har recent data.
    
    Error Handling:
        JSON decode fejl logges men bryder ikke forbindelse
        WebSocket fejl logger og lukker forbindelse gracefully
        WebSocketDisconnect håndteres specifikt for cleanup
    
    Security:
        Ingen authentication
        Command-validation via KOMMANDO_SYNTAX regex
        JSON decode errors fanges for robusthed
    
    Args:
        websocket: FastAPI WebSocket connection object
    
    Note:
        Selvom kun én klient (touchscreen) forventes, supporterer koden
        flere klienter via Set tracking i data_opbevaring.
    """
    # Accept incoming connection
    await websocket.accept()
    
    # Register klient i aktive klient liste
    data_opbevaring.tilføj_websocket_klient(websocket)
    
    try:
        # Send initial state til ny klient
        nuværende_data = data_opbevaring.hent_alle_data()
        
        await websocket.send_text(json.dumps({
            'type': 'initial',
            'data': nuværende_data,
            'grænser': GRÆNSER
        }))
        
        # Polling loop state tracking
        sidste_sendt_tid = 0.0
        
        # Main WebSocket loop
        while True:
            try:
                # Lyt efter klient beskeder med 1 sekund timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=1.0
                )
                
                # Parse JSON besked
                besked = json.loads(data)
                besked_type = besked.get('type')
                
                # Vindueskommando-håndtering
                if besked_type == 'vindue_command':
                    kommando = besked.get('kommando', '')
                    
                    # Valider kommando
                    if (KOMMANDO_SYNTAX.match(kommando) and
                        kommando in TILLADTE_KOMMANDOER):
                        
                        if kommando in ['manuel_aaben', 'manuel_luk']:
                            # Importeres lokalt for at undgå circular dependency
                            from climate_controller import klima_controller

                            if kommando == 'manuel_aaben':
                                klima_controller.annuller_manuel_override_hvis_manuel_åben(kommando)
                            
                            klima_controller.aktiver_manuel_override(kommando)

                           # Log til database
                            db.gem_system_log(
                                ENHEDS_ID,
                                'WEB_SERVER',
                                f'Manuel override aktiveret: {kommando}'
                            )
                        # Send via MQTT (import lokalt)
                        from mqtt import mqtt_klient
                        mqtt_klient.publicer_kommando(kommando)
                        
                        # Bekræft til klient
                        await websocket.send_text(json.dumps({
                            'type': 'command_sent',
                            'kommando': kommando
                        }))
                
                # Dataforespørgsels-håndtering
                elif besked_type == 'get_data':
                    nuværende_data = data_opbevaring.hent_alle_data()
                    
                    await websocket.send_text(json.dumps({
                        'type': 'data',
                        'data': nuværende_data
                    }))
            
            # Timeout -> Periodisk opdatering
            except asyncio.TimeoutError:
                # Hent nuværende event loop tid
                nuværende_tid = asyncio.get_event_loop().time()
                
                # Tjek om 1+ sekund siden sidste send
                if nuværende_tid - sidste_sendt_tid >= 1.0:
                    # Send periodisk opdatering
                    nuværende_data = data_opbevaring.hent_alle_data()
                    
                    await websocket.send_text(json.dumps({
                        'type': 'update',
                        'update_type': 'periodic',
                        'data': nuværende_data
                    }))
                    
                    # Opdater sidste sendt tid
                    sidste_sendt_tid = nuværende_tid
            
            # JSON decode fejl
            except json.JSONDecodeError as fejl:
                db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"WebSocket JSON fejl: {fejl}")
                # Fortsæt loop (breaker ikke forbindelse)
            
            # Andre WebSocket fejl
            except Exception as fejl:
                db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"WebSocket fejl: {fejl}")
                break  # Luk forbindelse ved ukendte fejl
    
    # Disconnect cleanup
    except WebSocketDisconnect:
        # Normal disconnect fra klient
        pass
    
    finally:
        # Cleanup: Fjern fra aktive klienter
        data_opbevaring.fjern_websocket_klient(websocket)
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'WebSocket klient disconnected')


# Server Start Funktion

def start_webserver() -> None:
    """
    Starter Uvicorn ASGI server med konfigurerede settings.
    
    Production entry point (main.py). Starter Uvicorn server
    i blocking mode og kører indtil shutdown signal modtages.
    
    Uvicorn Configuration:
        Host: WEB_HOST fra config ('127.0.0.1')
        Port: WEB_PORT fra config (8000)
        Log Level: 'info' for logging
        Workers: 1 (single process, multiple threads via asyncio)
    
    Raises:
        RuntimeError: Hvis server start fejler
    
    Funktion:
        Starter HTTP server på konfigureret port
        Blokerer caller thread indtil shutdown
        Logger startup event til database
    
    Note:
        Denne funktion kaldes normalt fra main.py efter alle threads
        er startet. Den blokerer main thread indtil SIGINT/SIGTERM.
    """
    try:
        # Log webserver startup
        db.gem_system_log(
            ENHEDS_ID,
            'WEB_SERVER',
            f'Starter Uvicorn på {WEB_HOST}:{WEB_PORT}'
        )
        
        # Start Uvicorn ASGI server (blocking)
        uvicorn.run(
            app,
            host=WEB_HOST,
            port=WEB_PORT,
            log_level="info"
        )
        
    except OSError as fejl:
        # Port bruges allerede eller permission denied
        fejl_besked = f"Kunne ikke starte webserver: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', fejl_besked)
        raise RuntimeError(fejl_besked)
    
    except Exception as fejl:
        # Uventet fejl
        fejl_besked = f"Webserver fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', fejl_besked)
        raise RuntimeError(fejl_besked)


# Standalone Execution for Testing

def standalone_main() -> None:
    """
    Entry point for standalone testing uden main.py.
    
    Tillader at køre webserveren isoleret for at teste API endpoints
    uden at starte hele systemet (MQTT, BME680, etc.).
    
    Limitations:
        Ingen BME680 sensor data 
        Ingen MQTT sensor data
        Ingen remote sync
        WebSocket opdateringer virker ikke
    
    Use Cases:
        Test API endpoint responses
        Test frontend static serving
        Debug WebSocket forbindelseslogikken
        Develop frontend uden hardware
    
    Usage:
        python3 app.py
        Webserver starter på http://127.0.0.1:8000
        Naviger til http://localhost:8000 i browser
    
    Warning:
        Denne mode er KUN til udvikling og testing. Brug altid main.py
        til fuld funktionalitet da det starter alle nødvendige
        komponenter.
    """
    
    db.gem_system_log(
        ENHEDS_ID,
        'WEB_SERVER',
        'STANDALONE MODE: Webserver startet uden sensor integration'
    )
    
    try:
        # Start webserver i standalone mode
        start_webserver()
        
    except KeyboardInterrupt:
        # Graceful shutdown ved Ctrl+C
        print("\nShutdown ved Ctrl+C")
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'Standalone mode shutdown ved SIGINT')
    
    except Exception as fejl:
        # Log uventet fejl
        print(f"\nFATAL ERROR: {fejl}")
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"Standalone mode fejl: {fejl}")
        raise


if __name__ == "__main__":
    """
    Module execution guard for standalone testing.
    
    Denne blok køres kun når app.py køres direkte som script:
        python3 app.py
    
    Den køres ikke når app.py importeres som modul:
        from app import start_webserver  # __name__ = "app"
    
    Funktionalitet:
        Tillader standalone execution til udvikling og testing uden at
        starte hele systemet via main.py.
    """
    standalone_main()