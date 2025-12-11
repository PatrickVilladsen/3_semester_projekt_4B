from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import json
import re
import asyncio
from database import db
from sensor_data import data_opbevaring
#from mqtt import mqtt_client
from config import WEB_HOST, WEB_PORT, THRESHOLDS
import uvicorn

# Regex til validerer kommandoerne
COMMAND_PATTERN = re.compile(r'^[a-z_]+$')

# Tilladte kommandoer vi bruger til mqtt
TILLADTE_KOMMANDOER = ['aaben', 'luk', 'manuel_aaben', 'manuel_luk', 'kort_aaben']

# Vi dannar en global reference til sensoren så vi kan få den fra main.py
_bme680_sensor_instance = None

# Her er til når vi skal definere dens nye værdi
def set_bme680_sensor(sensor):
    global _bme680_sensor_instance
    _bme680_sensor_instance = sensor

# funktion til at bringe den nye data til vores klienter
def notify_websocket_clients(update_type: str):

    #Tjekker hvem der skal have besked
    clients = data_opbevaring.get_websocket_clients()
    
    # Stopper hvis der ikke er nogle til at modtage data
    if not clients:
        return
    
    # Her klargører vi ny data til vores klienter
    # update_type fortæller hvad det er der skal opdateres
    data_opbevaring._last_update_type = update_type
    # Vi fortæller data_opbevaring hvornår vi sendte den sidste opdatering
    data_opbevaring._last_update_time = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0

# Vi definerer Lifespan som en asynkron contextmanager som vi binder på FastAPI
# Det er baseret på ASGI og sammenkobler vores threading med vores asynkrone del af koden
@asynccontextmanager
async def lifespan(app: FastAPI):
    
    # Vi opsætter en kommunikationsbro mellem vores bme680 sensor og vores websocket, det er fordi at vi ønsker at opdateringer
    # fra bme680 sensoren sendes ud til vores klienter
    if _bme680_sensor_instance:
        _bme680_sensor_instance.set_websocket_callback(notify_websocket_clients)
    else:
        pass
    '''
    Nu er opstartsfasen over og der er dannet en bro mellem vores bme680 tråd og websockets
    Nu er det FastAPI med websockets der håndterer resten og vi er ikke bundet på threads herfra
    Vi modtager stadig threads-opdateringer, men vi kører asynkront, så webserveren fryser ikke når
    vi venter på svar/data fra en thread'''
    yield

# Her oprettes selve webserveren
app = FastAPI(
    title="Automatisk udluftnings System", 
    version="1.0.0",
    lifespan=lifespan
)

# Her ændrer vi på vores middleware så vi kan tilgå vores FastAPI uden besvær
# Der står mere om det inde på vores dokumentations fil
app.add_middleware(
    CORSMiddleware,
    #Tillader forspørgelser fra alle domæner/IP'er
    allow_origins=["*"],
    # Tillader sende credentials oplysninger som f.eks. tokens
    allow_credentials=True,
    # Tillader alle HTTP-metoder som f.eks. GET, POST osv.
    allow_methods=["*"],
    # Tillader alle HTTP headers
    allow_headers=["*"],
)

# Hvor vores static filer findes (html, css, javascript, billeder osv.)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Vores Endpoint - fortæller hvilken "side" der er vores root
@app.get("/")
async def root():
    return FileResponse('static/index.html')

# Her henter FastAPI vores data
@app.get("/api/data")
async def get_data():
    data = data_opbevaring.get_all_data()
    return data

# Her hentes vores threshold som javascript skal bruge til at håndtere farver på displayer
@app.get("/api/thresholds")
async def get_thresholds():
    return THRESHOLDS

# Her sendes der besked fra klienten en kommando som skal bruges til mqtt omkring styring af vinduet
@app.post("/api/vindue/{command}")
async def control_vindue(command: str):
    # Her verificerer vi med regex at det er en gyldig kommando
    if not COMMAND_PATTERN.match(command):
        raise HTTPException(status_code=400, detail="Ugyldig kommando format")
    
    if command not in TILLADTE_KOMMANDOER:
        raise HTTPException(status_code=400, detail="Ukendt kommando")
    
    # MQTT publish af kommandoen
    mqtt_client.publish_command(command)
    return {"status": "success", "command": command}

# Her er vores Endpoint til websocket, det fortæller hvad der skal ske med nye klienter der opretter forbindelse
# Hånderes asynkront
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Vi accepterer klienten
    await websocket.accept()
    #Vi lagre information omkring klienten i vores data opbevaring
    data_opbevaring.add_websocket_client(websocket)
    
    try:
        # Vi henter alt nuværende data så klienten kan se det
        vores_data = data_opbevaring.get_all_data()
        
        await websocket.send_text(json.dumps({
            'type': 'initial',
            'data': vores_data,
            'thresholds': THRESHOLDS
        }))
        
        # Her laver vi et "Polling" loop der sikrer at vi refresher browseren hvert 2. sekund
        last_sent_time = 0
        
        while True:
            try:
                # Vi tjekker efter ny data hvert 2. sekund
                data = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                # det data vi ønsker at modtage - enten data fra sensor eller besked fra klient
                message = json.loads(data)
                msg_type = message.get('type')
                # Hvis vi modtager en kommando fra klienten tjekker vi den igennem og sender den ud på mqtt
                if msg_type == 'vindue_command':
                    command = message.get('command', '')
                    #skal selvfølgelig verificeret med regex
                    if COMMAND_PATTERN.match(command) and command in TILLADTE_KOMMANDOER:
                        
                        # MQTT publish
                        mqtt_client.publish_command(command)
                        # Vi bekræfter klienten at beskeden er sent
                        await websocket.send_text(json.dumps({
                            'type': 'command_sent',
                            'command': command
                        }))
                # Her håndterer vi når der er tale om ny data fra sensorene
                elif msg_type == 'get_data':
                    vores_data = data_opbevaring.get_all_data()
                    # Vi sender det videre til klienten
                    await websocket.send_text(json.dumps({
                        'type': 'data',
                        'data': vores_data
                    }))
                    
                    # Her opdaterer vi efter ikke at have modtaget noget fra hverken sensorene eller klienten i 2 sekunder
            except asyncio.TimeoutError:
                # Her sker der lidt - Vi henter det aktuelle tidspunkt fra asyncio, vi tjekker vores event loop der holder hele FastAPI i gang
                # Vi tjekker dens time() hvilket altid bevæger sig frem
                current_time = asyncio.get_event_loop().time()
                
                # vi verificerer så at tiden mellem current_time og last_sent_time er 2 sekunder
                if current_time - last_sent_time >= 2.0:
                    # Vi opdaterer data til websocket(klienten) med ny data
                    vores_data = data_opbevaring.get_all_data()
                    
                    # Vi sender den til javascript
                    await websocket.send_text(json.dumps({
                        'type': 'update',
                        'update_type': 'periodic',
                        'data': vores_data
                    }))
                    
                    # Vi indstiller last_sent_time til current_time, så vi om 2 sekunder igen kan verificerer om der er gået 2 sekunder igen
                    last_sent_time = current_time
                    
                    #error log
            except json.JSONDecodeError:
                db.log_error('WEB_SERVER', f"WebSocket JSON fejl: {e}")
            except Exception as e:
                db.log_error('WEB_SERVER', f"WebSocket fejl: {e}")
                break
                
                # Ved websocket disconnect af klient, giver vi besked på at fjerne klienten fra vores data_opbevaring
    except WebSocketDisconnect:
        data_opbevaring.remove_websocket_client(websocket)

# Her starter vi vores webserver
def start_web_server():
    uvicorn.run(
        app, 
        host=WEB_HOST, 
        port=WEB_PORT,
        log_level="info"
    )

# Så vi kan køre den uden at køre main.py - er ligegyldigt i den endelige løsning
if __name__ == "__main__":
    start_web_server()