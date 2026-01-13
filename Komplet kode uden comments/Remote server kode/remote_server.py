
from dotenv import load_dotenv
import os
load_dotenv()
from fastapi import FastAPI, HTTPException, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import execute_batch
from psycopg2 import pool
from datetime import datetime, timedelta
import logging
import uvicorn


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('remote_server.log')
    ]
)
logger = logging.getLogger("RemoteServer")


DATABASE_URL: Optional[str] = os.getenv('DATABASE_URL')

BEARER_TOKEN: Optional[str] = os.getenv('BEARER_TOKEN')

if not DATABASE_URL:
    logger.critical("DATABASE_URL env-variabel mangler")
    raise RuntimeError("DATABASE_URL skal være sat")

if not BEARER_TOKEN:
    logger.critical("BEARER_TOKEN env-variabel mangler")
    raise RuntimeError("BEARER_TOKEN skal være sat")

if len(BEARER_TOKEN) < 32:
    logger.warning(
        f"BEARER_TOKEN er kun {len(BEARER_TOKEN)} tegn. "
        "Skal af sikkerhedsmæssige oversager være minimum 32 tegn."
    )

try:
    db_pool = pool.SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        dsn=DATABASE_URL
    )
    logger.info("Database connection pool oprettet")
    
except psycopg2.Error as fejl:
    logger.critical(f"Kunne ikke forbinde til database: {fejl}")
    raise RuntimeError(f"Database connection fejlede: {fejl}")


def valider_sensor_værdi(
    værdi: Any,
    min_værdi: Optional[float] = None,
    max_værdi: Optional[float] = None
) -> Optional[float]:
    
    try:

        nummer = float(værdi)
        
        if min_værdi is not None and nummer < min_værdi:
            return None
        if max_værdi is not None and nummer > max_værdi:
            return None
        
        return nummer
        
    except (ValueError, TypeError):
        return None


class SyncPayload(BaseModel):
  
    sensor_data: List[Dict[str, Any]] = Field(
        default=[],
        description="Sensor målinger fra RPi5"
    )
    fejl_logs: List[Dict[str, Any]] = Field(
        default=[],
        description="Fejlbeskeder fra RPi5"
    )
    system_logs: List[Dict[str, Any]] = Field(
        default=[],
        description="System events fra RPi5"
    )
    enheds_id: str = Field(
        ...,
        min_length=1,
        description="Enheds identifier"
    )
    
    @field_validator('enheds_id')
    @classmethod
    def valider_enheds_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("enheds_id må ikke være tom")
        return v.strip()

app = FastAPI(
    title="Automatisk udluftningssystem - Remote Server",
    version="1.0.0",
    description="HTTP receiver til dataopbevaring af data i PostgreSQL database"
)


def hent_db_connection():

    try:
        return db_pool.getconn()
    except pool.PoolError as fejl:
        logger.error(f"Database pool opbrugt: {fejl}")
        raise RuntimeError("Alle database forbindelser er i brug")


def returner_db_connection(conn) -> None:
 
    if conn:
        db_pool.putconn(conn)


def initialiser_database() -> None:
  
    conn = None
    try:
        conn = hent_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sensor_data (
                id SERIAL PRIMARY KEY,
                enheds_id TEXT NOT NULL,
                målt_klokken TIMESTAMP NOT NULL,
                kilde TEXT NOT NULL,
                data_type TEXT NOT NULL,
                værdi REAL,
                modtaget_klokken TIMESTAMP DEFAULT NOW()
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fejl_logs (
                id SERIAL PRIMARY KEY,
                enheds_id TEXT NOT NULL,
                målt_klokken TIMESTAMP NOT NULL,
                kilde TEXT NOT NULL,
                fejl_besked TEXT NOT NULL,
                modtaget_klokken TIMESTAMP DEFAULT NOW()
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_logs (
                id SERIAL PRIMARY KEY,
                enheds_id TEXT NOT NULL,
                målt_klokken TIMESTAMP NOT NULL,
                kilde TEXT NOT NULL,
                besked TEXT NOT NULL,
                modtaget_klokken TIMESTAMP DEFAULT NOW()
            )
        ''')
        
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_sensor_målt '
            'ON sensor_data(målt_klokken)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_fejl_målt '
            'ON fejl_logs(målt_klokken)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_logs_målt '
            'ON system_logs(målt_klokken)'
        )
        
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_sensor_enhed '
            'ON sensor_data(enheds_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_fejl_enhed '
            'ON fejl_logs(enheds_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_logs_enhed '
            'ON system_logs(enheds_id)'
        )
        
        conn.commit()
        logger.info("Database skema oprettet og startet korrekt")
        
    except psycopg2.Error as fejl:
        logger.error(f"Database opstart fejlede: {fejl}")
        if conn:
            conn.rollback()
        raise RuntimeError(f"Kunne ikke opstarte eller oprette database: {fejl}")
    
    finally:
        if conn:
            returner_db_connection(conn)

try:
    initialiser_database()
except Exception as fejl:
    logger.critical(f"Kritisk: Database opstart fejlede: {fejl}")
    raise


def verificer_token(authorization: str = Header(None)) -> None:

    if not authorization:
        logger.warning("Unauthorized forsøg på adgang: Ingen authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header mangler"
        )
    
    if not authorization.startswith('Bearer '):
        logger.warning("Unauthorized forsøg på adgang: Forkert formatering i header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header skal være 'Bearer <token>'"
        )
    
    token = authorization[7:]
    
    if token != BEARER_TOKEN:
        logger.warning(f"Unauthorized forsøg på adgang: Ugyldig token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldig authentication token"
        )
    
    logger.debug("Authentication successfuld")


@app.get("/")
async def root() -> Dict[str, str]:
    return {
        "status": "running",
        "service": "Automatisk udluftningssystem - Remote Server",
        "version": "1.0.0"
    }


@app.post("/api/sync")
async def modtag_data(
    payload: SyncPayload,
    authorization: str = Header(None)
) -> JSONResponse:

    verificer_token(authorization)
    
    conn = None
    try:

        conn = hent_db_connection()
        cursor = conn.cursor()
        
 
        if payload.sensor_data:
            sensor_værdier = []
            
            for række in payload.sensor_data:

                værdi = række.get('value')
                data_type = række.get('data_type')
                

                if data_type == 'temperatur':
                    værdi = valider_sensor_værdi(værdi, min_værdi=-40, max_værdi=85)
                elif data_type == 'luftfugtighed':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=100)
                elif data_type == 'batteri':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=100)
                elif data_type == 'gas':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=500000)
                
                if værdi is None:
                    logger.warning(
                        f"Invalid {data_type} værdi skipped: {række.get('value')}"
                    )
                    continue

                sensor_værdier.append((
                    payload.enheds_id,
                    række.get('målt_klokken'),
                    række.get('kilde'),
                    data_type,
                    værdi
                ))

            if sensor_værdier:
                execute_batch(
                    cursor,
                    '''INSERT INTO sensor_data 
                       (enheds_id, målt_klokken, kilde, data_type, værdi) 
                       VALUES (%s, %s, %s, %s, %s)''',
                    sensor_værdier
                )
                logger.debug(f"Indsat {len(sensor_værdier)} sensor rækker")

        if payload.fejl_logs:
            fejl_værdier = [
                (
                    payload.enheds_id,
                    række.get('målt_klokken'),
                    række.get('kilde'),
                    række.get('fejlbesked')
                )
                for række in payload.fejl_logs
            ]
            
            execute_batch(
                cursor,
                '''INSERT INTO fejl_logs 
                   (enheds_id, målt_klokken, kilde, fejl_besked) 
                   VALUES (%s, %s, %s, %s)''',
                fejl_værdier
            )
            logger.debug(f"Indsat {len(fejl_værdier)} rækker fejlbeskeder")

        if payload.system_logs:
            log_værdier = [
                (
                    payload.enheds_id,
                    række.get('målt_klokken'),
                    række.get('kilde'),
                    række.get('besked')
                )
                for række in payload.system_logs
            ]
            
            execute_batch(
                cursor,
                '''INSERT INTO system_logs 
                   (enheds_id, målt_klokken, kilde, besked) 
                   VALUES (%s, %s, %s, %s)''',
                log_værdier
            )
            logger.debug(f"Indsat {len(log_værdier)} system_log rækker")

        conn.commit()

        total_rækker = (
            len(payload.sensor_data) + 
            len(payload.fejl_logs) + 
            len(payload.system_logs)
        )
        
        logger.info(
            f"Data modtaget fra {payload.enheds_id}: "
            f"{total_rækker} rækker gemt"
        )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "success",
                "modtaget_sensor_rækker": len(payload.sensor_data),
                "modtaget_fejlbeskeder": len(payload.fejl_logs),
                "modtaget_system_logs": len(payload.system_logs)
            }
        )
        
    except psycopg2.Error as fejl:

        if conn:
            conn.rollback()
        logger.error(f"Database indsættelse fejlede: {fejl}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database fejl ved opbevaring af data"
        )
    
    except Exception as fejl:

        if conn:
            conn.rollback()
        logger.error(f"Uventet fejl ved data modtagelse: {fejl}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Intern server fejl"
        )
    
    finally:
        if conn:
            returner_db_connection(conn)


@app.post("/api/cleanup")
async def cleanup(
    authorization: str = Header(None)
) -> JSONResponse:

    verificer_token(authorization)
    
    cutoff = datetime.now() - timedelta(days=30)
    
    conn = None
    try:
        conn = hent_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            'DELETE FROM sensor_data WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_sensor_rækker = cursor.rowcount

        cursor.execute(
            'DELETE FROM fejl_logs WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_fejlbeskeder = cursor.rowcount

        cursor.execute(
            'DELETE FROM system_logs WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_system_logs = cursor.rowcount

        conn.commit()
        
        logger.info(
            f"Cleanup udført - Slettet: {antal_slettet_sensor_rækker} sensor-data rækker, "
            f"{antal_slettet_fejlbeskeder} fejlbeskeder, {antal_slettet_system_logs} system-logs"
        )
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "antal_slettet_sensor_rækker": antal_slettet_sensor_rækker,
                "antal_slettet_fejlbeskeder": antal_slettet_fejlbeskeder,
                "antal_slettet_system_logs": antal_slettet_system_logs
            }
        )
        
    except psycopg2.Error as fejl:
        if conn:
            conn.rollback()
        logger.error(f"Cleanup fejlede: {fejl}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database cleanup fejlede"
        )
    
    finally:
        if conn:
            returner_db_connection(conn)

if __name__ == "__main__":

    logger.info("Starter Remote Server på port 8080")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )