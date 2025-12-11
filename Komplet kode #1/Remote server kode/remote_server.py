from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from typing import List, Dict, Any
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime, timedelta
import os
import logging
import uvicorn



'''
Dette er vores kode til vores remote server
Den modtager data fra vores raspberry pi og sætter det så ind i vores postgresql database
Det smarte er at vi opretter vores tables til databasen hvis de ikke allerede findes
Vi benytter logging i stedet for print da vi får lang flere detaljer om hændelserne der sker'''

# Her defi
def valider_sensor_værdi(value, min_val=None, max_val=None):
    try:
        num = float(value)
        if min_val is not None and num < min_val:
            return None
        if max_val is not None and num > max_val:
            return None
        return num
    except (ValueError, TypeError):
        return None

# Her opsætter vi vores logging-system, vi ønsker tidspunkt, levelname, som kan være f.eks. info eller error.
#Og til sidst selve beskeden
# Da vi har level sat til INFO betyder det at vi modtager logging fra INFO og alt der er rangeret over INFO - ranglisten kan ses på dokumentation filen.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("RemoteServer")

# Opsætning af vores database lokation
DATABASE_URL = os.getenv('DATABASE_URL')
BEARER_TOKEN = os.getenv('BEARER_TOKEN')

# Logging til hvis det ikke er sat korrekt op
if not DATABASE_URL:
    logger.warning("DATABASE_URL mangler.")
if not BEARER_TOKEN:
    logger.warning("BEARER_TOKEN mangler.")

# Da vores data sendes over HTTP skal vi kunne modtage den og her bruger vi FastAPI som vores "tolk"
app = FastAPI(title="HTTP Receiver Remote Server")

# Nu skal vi opsætte vores database

# Her defineres get_db til at forbinde til vores database
def get_db():
    return psycopg2.connect(DATABASE_URL)

# Her defineres vores initiering af databasen
def init_db():
    try:
        '''
        Da PostgreSQL ikke fungerer på samme måde som SQLite og af natur er indbygget med mere thread safety
        skal der ikke bruges noget ligesom contextmanager - da det simpelthen er indbygget
        Det er også derfor at vi skal commit og close manuelt i koden'''
        # Først forbiner vi til databasen
        conn = get_db()
        # Så giver vi arbejdsredskabet
        cursor = conn.cursor()
        
        # Vi opretter tables til hvis de ikke allerede findes. - Her til sensor data
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sensor_data (
                id SERIAL PRIMARY KEY,
                device_id TEXT,
                timestamp TIMESTAMP,
                source TEXT,
                data_type TEXT,
                value REAL,
                received_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        # Og så til error logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS errors (
                id SERIAL PRIMARY KEY,
                device_id TEXT,
                timestamp TIMESTAMP,
                source TEXT,
                error_message TEXT,
                received_at TIMESTAMP DEFAULT NOW()
            )
        ''')

        # Og til sidst til system logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_logs (
                id SERIAL PRIMARY KEY,
                device_id TEXT,
                timestamp TIMESTAMP,
                source TEXT,
                message TEXT,
                received_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        # Vi opretter index til databasen så den har sorteret det i sin hukommelse efter dato, hvilket gør at den ikke behøver at kigge hele databsen igennem
        # hvis vi nu ønsker at se data fra de sidste 24 timer
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_sensor_timestamp ON sensor_data(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_error_timestamp ON errors(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON system_logs(timestamp)')
        # Vi gemmer sorteringen
        conn.commit()
        # Vi lukker forbindelsen
        conn.close()

        logger.info("Databasen's tables er oprettet")
        
    except Exception as e:
        logger.error(f"Database kunne ikke oprette tables: {e}")

# Her starter vi databasen
if DATABASE_URL:
    init_db()

# Vi bruger SyncPayload til at verificere at dataen vi modtager passer ind i den model som vi forventer
class SyncPayload(BaseModel):
    #Vi forventer en liste med dictionaries - listen må gerne være tom
    sensor_data: List[Dict[str, Any]] = []
    #Vi forventer en liste med dictionaries - listen må gerne være tom
    errors: List[Dict[str, Any]] = []
    #Vi forventer en liste med dictionaries - listen må gerne være tom
    system_logs: List[Dict[str, Any]] = []
    # Vi forventer en string
    device_id: str


# Her sker vores verificering af vores bearer token
# Vi forventer at argumentet er authorization med en string, hvis der mangler en header sætter vi dens værdi til None
def verify_token(authorization: str = Header(None)):
    # Hvis der ikke er en header med authorization eller den ikke starer med "Bearer"
    if not authorization or not authorization.startswith('Bearer '):
        #Så sender vi en log med at der var nogle uvedkommende som forsøgte at få forbindelse
        logger.warning("Uvedkommende kilde forsøgte at skabe forbindelse: (No/Invalid Header)")
        # Og vi giver dem en fejlbesked
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    # Vi tjekker den token der kom og slicer "bearer " fra så vi altså kun læser selve token-værdien
    token = authorization[7:]
    #Hvis den token ikke passer med den token vi forventer giver vi besked i log
    if token != BEARER_TOKEN:
        logger.warning("Uvedkommende kilde forsøgte at skabe forbindelse: (Wrong Token)")
        raise HTTPException(status_code=401, detail="Invalid token")

# Vores "Endpoint" til FastAPI

# Vores root - det er beskeden som sendes i det vi modtager en GET-anmodning
@app.get("/")
async def root():
    return {"status": "running", "service": "HTTP Receiver Remote Server"}

#Her det det vi modtager
#Lytter efter POST-anmodninger
@app.post("/api/sync")
# Modtager POST fra kilde
# Vi beskriver hvilket noget data vi forventer at modtage
async def receive_data(
    payload: SyncPayload,
    authorization: str = Header(None)
):
    # Vi verificerer token
    verify_token(authorization)
    # Vi vil derefter indsætte den nye data i databasen
    try:
        # Forbinder til db
        conn = get_db()
        # For redskabet til at ændre i databasen
        cursor = conn.cursor()
        
        # Vi indsætter payload'en vi har modtaget - her er det til sensor værdierne
        if payload.sensor_data:
            sensor_values = []
            for row in payload.sensor_data:
                # Valider værdierne baseret på data_type
                value = row['value']
                data_type = row['data_type']
                
                if data_type == 'temperature':
                    value = valider_sensor_værdi(value, min_val=-40, max_val=85)
                elif data_type == 'humidity':
                    value = valider_sensor_værdi(value, min_val=0, max_val=100)
                
                if value is not None:
                    sensor_values.append(
                        (payload.device_id, row['timestamp'], row['source'], 
                         row['data_type'], value)
                    )
            # Send batch afsted
            # Her beskytter vi også mod sql injection %s fungerer ligesom ? da vi arbejdede med den lokale sqlite databse
            # sensor_values er så datapakken der sendes med og har de værdier som erstatter
            execute_batch(
                cursor,
                'INSERT INTO sensor_data (device_id, timestamp, source, data_type, value) VALUES (%s, %s, %s, %s, %s)',
                sensor_values
            )
        
        # Samme koncept bare med error logs
        if payload.errors:
            error_values = [
                (payload.device_id, row['timestamp'], row['source'], row['error_message'])
                for row in payload.errors
            ]
            execute_batch(
                cursor,
                'INSERT INTO errors (device_id, timestamp, source, error_message) VALUES (%s, %s, %s, %s)',
                error_values
            )

        # Samme koncept bare med system logs
        if payload.system_logs:
            log_values = [
                (payload.device_id, row['timestamp'], row['source'], row['message'])
                for row in payload.system_logs
            ]
            execute_batch(
                cursor,
                'INSERT INTO system_logs (device_id, timestamp, source, message) VALUES (%s, %s, %s, %s)',
                log_values
            )
        # Vi comitter til databasen
        conn.commit()
        # Vi afslutter forbindelsen
        conn.close()
        
        # Her fortæller vi hvor mange nye rækker der blev tilføjet til databasen i den sidste sending
        new_rows = len(payload.sensor_data) + len(payload.errors) + len(payload.system_logs)
        logger.info(f"Modtog data fra {payload.device_id}: {new_rows} nye rækker gemt.")
        
        # Her er så vores "kvittering" til vores lokal server - vi bekræfter at vi har gemt den nye data så den kan synkronisere det
        return {
            "status": "success",
            "received_sensor_rows": len(payload.sensor_data),
            "received_errors": len(payload.errors),
            "received_logs": len(payload.system_logs)
        }
        
    except Exception as e:
        logger.error(f"Fejl da nye rækker skulle gemmes: {e}")
        raise HTTPException(status_code=500, detail="Database Fejl")

# Besked på at data skal ryddes fra raspberry pi
@app.post("/api/cleanup")
async def cleanup(authorization: str = Header(None)):
    # Vi verificerer token
    verify_token(authorization)
    # Vores cutoff her er 30 dage, så vi tjekker efter rækker der er ældre end det
    cutoff = datetime.now() - timedelta(days=30)
    # Vi forbinder til databasen
    conn = get_db()
    # Vi får redigeringsredskabet
    cursor = conn.cursor()
    #Vi sletter rækker som er ældre end 30 dage
    cursor.execute('DELETE FROM sensor_data WHERE timestamp < %s', (cutoff,))
    # Vi tæller hvor mange rækker der blev slettet
    deleted_sensor = cursor.rowcount
    
    # Samme koncept
    cursor.execute('DELETE FROM errors WHERE timestamp < %s', (cutoff,))
    deleted_errors = cursor.rowcount

    # Samme koncept
    cursor.execute('DELETE FROM system_logs WHERE timestamp < %s', (cutoff,))
    deleted_logs = cursor.rowcount
    
    # Vi gememr vores ændringer
    conn.commit()
    # Vi afslutter forbindelsen
    conn.close()
    
    logger.info(f"Database rydning. Der blev slettet: {deleted_sensor} sensor-rækker, {deleted_errors} error-rækker og {deleted_logs} system-logs-rækker")
    
    # Giv besked på at rækkerne blev slettet
    return {
        "deleted_sensor_rows": deleted_sensor,
        "deleted_errors": deleted_errors,
        "deleted_logs": deleted_logs
    }

# Vores loop der holder koden kørende
if __name__ == "__main__":
    logger.info("Remote Server startes på port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)