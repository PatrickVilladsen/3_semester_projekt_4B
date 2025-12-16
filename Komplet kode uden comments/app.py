

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


_bme680_sensor_instans: Optional[Any] = None



def sæt_bme680_sensor(sensor: Any) -> None:

    global _bme680_sensor_instans
    _bme680_sensor_instans = sensor


async def notificer_websocket_klienter(opdaterings_type: str) -> None:

    await broadcast_til_websockets(opdaterings_type)


@asynccontextmanager
async def lifespan(app: FastAPI):

    if _bme680_sensor_instans:
        _bme680_sensor_instans.sæt_websocket_callback(
            notificer_websocket_klienter
        )
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'BME680 WebSocket callback konfigureret')
    else:
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', 'BME680 sensor instance ikke tilgængelig')
    
    yield
    
    db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'Webserver shutdown påbegyndt')


app = FastAPI(
    title="Automatisk Udluftningssystem",
    version="1.0.0",
    description="IoT-baseret automatisk vindues-styring med fokus på indeklima optimering",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")



@app.get("/")
async def root() -> FileResponse:

    return FileResponse('static/index.html')


@app.get("/api/data")
async def hent_data() -> Dict[str, Any]:

    data = data_opbevaring.hent_alle_data()
    return data


@app.get("/api/thresholds")
async def hent_grænseværdier() -> Dict[str, Dict[str, Any]]:

    return GRÆNSER


@app.get("/api/historical/{data_type}")
async def hent_historik(
    data_type: str,
    dage: int = 7
) -> Dict[str, Any]:

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

    try:
        temp_data = db.hent_datahistorik('temperatur', dage=7)
        
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

    gyldige_typer = ['temperatur', 'luftfugtighed', 'gas']
    
    if graf_type not in gyldige_typer:
        raise HTTPException(
            status_code=400,
            detail=f"Ugyldig graf type. Gyldige: {', '.join(gyldige_typer)}"
        )
    
    if dage < 1 or dage > 30:
        raise HTTPException(
            status_code=400,
            detail="dage skal være mellem 1 og 30"
        )
    
    try:
        billede_buffer = graf_generator.generer_graf(graf_type, dage)
        
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


KOMMANDO_SYNTAX = re.compile(r'^[a-z_]+$')


TILLADTE_KOMMANDOER = [
    'aaben',
    'luk',
    'manuel_aaben',
    'manuel_luk',
    'kort_aaben'
]



@app.post("/api/vindue/{kommando}")
async def kontroller_vindue(kommando: str) -> Dict[str, str]:

    if not KOMMANDO_SYNTAX.match(kommando):
        raise HTTPException(
            status_code=400,
            detail="Ugyldig kommando format (kun lowercase og underscore)"
        )
    
    if kommando not in TILLADTE_KOMMANDOER:
        raise HTTPException(
            status_code=400,
            detail=f"Ukendt kommando. Gyldige: {', '.join(TILLADTE_KOMMANDOER)}"
        )
    if kommando in ['manuel_aaben', 'manuel_luk']:
        from climate_controller import klima_controller
        
        if kommando == 'manuel_aaben':
            klima_controller.annuller_manuel_override_hvis_manuel_åben(kommando)

        klima_controller.aktiver_manuel_override(kommando)
        db.gem_system_log(
                ENHEDS_ID,
                'WEB_SERVER',
                f'Manuel override aktiveret (REST): {kommando}'
            )

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



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:

    await websocket.accept()
    
    data_opbevaring.tilføj_websocket_klient(websocket)
    
    try:
        nuværende_data = data_opbevaring.hent_alle_data()
        
        await websocket.send_text(json.dumps({
            'type': 'initial',
            'data': nuværende_data,
            'grænser': GRÆNSER
        }))
        
        sidste_sendt_tid = 0.0
        
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=1.0
                )
                
                besked = json.loads(data)
                besked_type = besked.get('type')
                
                if besked_type == 'vindue_command':
                    kommando = besked.get('kommando', '')
                    
                    if (KOMMANDO_SYNTAX.match(kommando) and
                        kommando in TILLADTE_KOMMANDOER):
                        
                        if kommando in ['manuel_aaben', 'manuel_luk']:
                            from climate_controller import klima_controller

                            if kommando == 'manuel_aaben':
                                klima_controller.annuller_manuel_override_hvis_manuel_åben(kommando)
                            
                            klima_controller.aktiver_manuel_override(kommando)

                            db.gem_system_log(
                                ENHEDS_ID,
                                'WEB_SERVER',
                                f'Manuel override aktiveret: {kommando}'
                            )
                        from mqtt import mqtt_klient
                        mqtt_klient.publicer_kommando(kommando)
                        
                        await websocket.send_text(json.dumps({
                            'type': 'command_sent',
                            'kommando': kommando
                        }))
                
                elif besked_type == 'get_data':
                    nuværende_data = data_opbevaring.hent_alle_data()
                    
                    await websocket.send_text(json.dumps({
                        'type': 'data',
                        'data': nuværende_data
                    }))
            
            except asyncio.TimeoutError:
                nuværende_tid = asyncio.get_event_loop().time()
                
                if nuværende_tid - sidste_sendt_tid >= 1.0:
                    nuværende_data = data_opbevaring.hent_alle_data()
                    
                    await websocket.send_text(json.dumps({
                        'type': 'update',
                        'update_type': 'periodic',
                        'data': nuværende_data
                    }))
                    
                    sidste_sendt_tid = nuværende_tid
            
            except json.JSONDecodeError as fejl:
                db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"WebSocket JSON fejl: {fejl}")
            
            except Exception as fejl:
                db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"WebSocket fejl: {fejl}")
                break
    
    except WebSocketDisconnect:
        pass
    
    finally:
        data_opbevaring.fjern_websocket_klient(websocket)
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'WebSocket klient disconnected')



def start_webserver() -> None:

    try:
        db.gem_system_log(
            ENHEDS_ID,
            'WEB_SERVER',
            f'Starter Uvicorn på {WEB_HOST}:{WEB_PORT}'
        )
        
        uvicorn.run(
            app,
            host=WEB_HOST,
            port=WEB_PORT,
            log_level="info"
        )
        
    except OSError as fejl:
        fejl_besked = f"Kunne ikke starte webserver: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', fejl_besked)
        raise RuntimeError(fejl_besked)
    
    except Exception as fejl:
        fejl_besked = f"Webserver fejl: {fejl}"
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', fejl_besked)
        raise RuntimeError(fejl_besked)



def standalone_main() -> None:

    
    db.gem_system_log(
        ENHEDS_ID,
        'WEB_SERVER',
        'STANDALONE MODE: Webserver startet uden sensor integration'
    )
    
    try:
        start_webserver()
        
    except KeyboardInterrupt:
        print("\nShutdown ved Ctrl+C")
        db.gem_system_log(ENHEDS_ID, 'WEB_SERVER', 'Standalone mode shutdown ved SIGINT')
    
    except Exception as fejl:
        print(f"\nFATAL ERROR: {fejl}")
        db.gem_fejl(ENHEDS_ID, 'WEB_SERVER', f"Standalone mode fejl: {fejl}")
        raise


if __name__ == "__main__":

    standalone_main()