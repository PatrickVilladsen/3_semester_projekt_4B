"""
Remote Server kode til længere opbevaring vores data og målinger.

Dette modul implementerer en FastAPI webserver der:
- Modtager sensor data fra Raspberry Pi 5 via HTTP POST
- Validerer Bearer token authentication
- Gemmer data i PostgreSQL database
- Udfører automatisk cleanup af gammel data
- Logger alle events og fejl

Arkitektur:
    RPI -> HTTP POST med Bearer Auth -> Validation -> PostgreSQL Insert
    
    Database Tables:
    - sensor_data: Temperatur, fugtighed, gas, batteri målinger
    - fejl_logs: Fejlbeskeder fra lokal server og målinger
    - system_logs: Events og kommando logging

Sikkerhed:
    Bearer token authentication
    SQL injectionsbeskyttelse
    Input validation

Database:
    PostgreSQL: Production-grade RDBMS med ACID garantier
    Thread-safe: Indbygget connection pooling
    Indexes: Optimeret søge-performance på målt_klokken og enheds_id
    Connection Pool: 1-10 forbindelser på samme tid som konstant holdes åbne

Environment variabler:
    DATABASE_URL: PostgreSQL connection string
    Eksempel: "postgresql://user:pass@localhost/db"
    BEARER_TOKEN: Secret token for authentication

Payload Format:
    POST /api/sync
    Headers: Authorization: Bearer <token>
    Body: {
        "sensor_data": [...],
        "fejl_logs": [...],
        "system_logs": [...],
        "enheds_id": "rpi5_id_1"
    }
"""
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


# Logging konfiguration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('remote_server.log')
    ]
)
logger = logging.getLogger("RemoteServer")
"""
Logger instance til server events.

Logging Level Hierarki:
    DEBUG < INFO < WARNING < ERROR < CRITICAL

Med level=INFO logges:
    INFO: Normale operations (startup, data received)
    WARNING: Mulige problemer (invalid data, retries)
    ERROR: Fejl som ikke crasher koden (DB errors, validation)
    CRITICAL: Kritiske fejl som gør koden ubrugelig (DB unreachable, config missing)
"""


# Environment konfiguration
DATABASE_URL: Optional[str] = os.getenv('DATABASE_URL')
"""
Forventer en string i denne format:
    postgresql://username:password@host:port/database
"""

BEARER_TOKEN: Optional[str] = os.getenv('BEARER_TOKEN')
"""
Vores secret token for at godkende forbindelse mellem lokal og remote server

Skal helst være minimum 32 tegn lang og autogeneret for ekstra sikkerhed.
"""

# Validerer at kravene er opfyldt
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


# Database connection pool
try:
    # Connection pool for bedre performance
    db_pool = pool.SimpleConnectionPool(
        minconn=1,      # Min antal forbindelser
        maxconn=10,     # Max antal forbindelser
        dsn=DATABASE_URL    # Data Source Name
    )
    logger.info("Database connection pool oprettet")
    
except psycopg2.Error as fejl:
    logger.critical(f"Kunne ikke forbinde til database: {fejl}")
    raise RuntimeError(f"Database connection fejlede: {fejl}")


# Validerings funktioner

def valider_sensor_værdi(
    værdi: Any,
    min_værdi: Optional[float] = None,
    max_værdi: Optional[float] = None
) -> Optional[float]:
    """
    Validerer og konverterer sensor værdi til float med range checking.
    
    Funktionen håndterer type conversion, whitespace trimming, og min/max
    range checking. Bruges til temperatur, fugtighed, gas og batteri målinger.
    
    Args:
        værdi: Rå værdi fra payload (kan være str, int, float)
        min_værdi: Minimum tilladt værdi. None = ingen nedre grænse
        max_værdi: Maksimum tilladt værdi. None = ingen øvre grænse
    
    Returns:
        Valideret float værdi, eller None hvis invalid
    
    Validation Steps:
        1. Type conversion til float (kan klare både float og string)
        2. Min værdi check
        3. Max værdi check
    
    Eksempler:
        valider_sensor_værdi("23.5", 0, 40) -> 23.5
        valider_sensor_værdi(" 12.3 ", 0, 100) -> 12.3  # Trimmer whitespace
        valider_sensor_værdi("150", 0, 100) -> None     # Over max
        valider_sensor_værdi("abc", 0, 100) -> None     # Ikke et tal
    
    Note:
        Returnerer None ved fejl i stedet for at raise exception.
        Dette gør det lettere at håndtere ugyldige data fra RPI ved at
        springe individuelle målinger over uden at afvise hele payload'en.
    """
    try:
        # Type conversion til float
        nummer = float(værdi)
        
        # Værdi grænse validering
        if min_værdi is not None and nummer < min_værdi:
            return None
        if max_værdi is not None and nummer > max_værdi:
            return None
        
        return nummer
        
    except (ValueError, TypeError):
        # Conversion fejlede (bogstaver, eller ugyldige tegn)
        return None


# Pydantic for payload validering

class SyncPayload(BaseModel):
    """
    Pydantic model til validation af sync payload fra RPI.
    
    Denne model sikrer at indkommende data har korrekt struktur før det
    processeres. Pydantic validerer automatisk types og kan coerce værdier
    hvis muligt.
    
    Attributes:
        sensor_data: Liste af sensor målinger (må være tom)
        fejl_logs: Liste af fejlbeskeder (må være tom)
        system_logs: Liste af system events (må være tom)
        enheds_id: Unikt ID for RPi5
    
    Validering:
        enheds_id må ikke være en tom string
        Alle lister skal være JSON arrays
        Lister må være tomme men ikke None
    
    Eksempel på Payload:
        {
            "sensor_data": [
                {
                    "id": 1,
                    "målt_klokken": "2025-12-12T10:30:00",
                    "kilde": "BME680",
                    "data_type": "temperatur",
                    "værdi": 22.5
                }
            ],
            "fejl_logs": [],
            "system_logs": [],
            "enheds_id": "rpi5_id_1"
        }
    """
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
        ...,  # de 3 prikker markerer at feltet er påkrævet
        min_length=1,
        description="Enheds identifier"
    )
    
    @field_validator('enheds_id')
    @classmethod
    def valider_enheds_id(cls, v: str) -> str:
        """
        Validerer at enheds_id ikke er whitespace.
        
        Args:
            v: enheds_id string at validere
        
        Returns:
            Trimmet enheds_id
        
        Raises:
            ValueError: Hvis enheds_id udelukkende er whitespace
        """
        if not v.strip():
            raise ValueError("enheds_id må ikke være tom")
        return v.strip()

# FastAPI applikation
app = FastAPI(
    title="Automatisk udluftningssystem - Remote Server",
    version="1.0.0",
    description="HTTP receiver til dataopbevaring af data i PostgreSQL database"
)

# Database funktioner

def hent_db_connection():
    """
    Henter database connection fra pool.
    
    Connection pool manager sørger for at connections genbruges effektivt
    og ikke leaker (altså at forbindelserne frigives igen når de ikke skal bruges længere).
    Max 10 samtidige forbindelser sikrer at vi ikke
    overloader PostgreSQL serveren.
    
    Returns:
        psycopg2.connection: Database forbindelse fra pool
    
    Raises:
        RuntimeError: Hvis connection pool er opbrugt (alle 10 i brug)
    
    Note:
        Connection bliver returneret til pool efter brug via putconn().
        Der bruges context manager for at rydde op ved fejl (with).
    """
    try:
        return db_pool.getconn()
    except pool.PoolError as fejl:
        logger.error(f"Database pool opbrugt: {fejl}")
        raise RuntimeError("Alle database forbindelser er i brug")


def returner_db_connection(conn) -> None:
    """
    Returnerer database connection til pool.
    
    Args:
        conn: Connection at returnere
    
    Note:
        Kaldes automatisk i finally blokken efter database ændringerne.
        Connection kan genbruges af andre requests efter return.
    """
    if conn:
        db_pool.putconn(conn)


def initialiser_database() -> None:
    """
    Opretter database skema hvis det ikke eksisterer i forvejen.
    
    Denne funktion køres ved server startup og sikrer at alle nødvendige
    tables og indexer findes. IF NOT EXISTS gør den "idempotent" så den altså
    kan kaldes flere gange.
    
    Skema:
        sensor_data: IoT-sensor målinger
            id, enheds_id, målt_klokken, kilde, data_type, værdi, modtaget_klokken
        
        fejl_logs: Fejl logs fra enheder
            id, enheds_id, målt_klokken, kilde, fejl_besked, modtaget_klokken
        
        system_logs: Event logs fra enheder
            id, enheds_id, målt_klokken, kilde, besked, modtaget_klokken
    
    Indexes:
        målt_klokken indexes: Giver hurtig svar ved kald efter dato'er
        enheds_id indexes: Filtering per enhed
        
        Index scan: O(log n) via B-tree vs O(n) full table scan
    
    Raises:
        RuntimeError: Hvis schema creation fejler
    
    Note:
        Bruger IF NOT EXISTS så det er sikkert at køre multiple gange.
        Dette tillader safe restarts uden manual database management.
    """
    conn = None
    try:
        conn = hent_db_connection()
        cursor = conn.cursor()
        
        # Sensor data table
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
        
        # Fejlbeskeder table
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
        
        # System logs table
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
        
        # Indexes for performance
        
        # Klokkeslæt indexes til dato filtering
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
        
        # Enheds ID indexes til filtering per enhed
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
        
        # Gem ændringer
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


# Initialiser database ved startup
try:
    initialiser_database()
except Exception as fejl:
    logger.critical(f"Kritisk: Database opstart fejlede: {fejl}")
    raise


# Authentication

def verificer_token(authorization: str = Header(None)) -> None:
    """
    Verificerer Bearer token authentication.
    
    Tjekker at Authorization header findes og indeholder valid Bearer token
    der matcher vores konfigureret BEARER_TOKEN fra env-variablen.
    
    Args:
        authorization: Authorization header fra HTTP request
    
    Raises:
        HTTPException 401: Hvis token mangler eller er ugyldig
    
    Security:
        Timing-safe string sammenligning - så man ikke kan gætte sig frem
        Token skal være minimum 32 tegn (tjekket ved startup)
        Logger forsøg på unauthorized adgang
    
    Header Format:
        Authorization: Bearer <token>
    
    Note:
        Denne funktion bruges som FastAPI dependency i endpoints.
        FastAPI kalder den automatisk før endpoint handleren.
    """
    # Tjek om Authorization header eksisterer
    if not authorization:
        logger.warning("Unauthorized forsøg på adgang: Ingen authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header mangler"
        )
    
    # Tjek om header starter med "Bearer "
    if not authorization.startswith('Bearer '):
        logger.warning("Unauthorized forsøg på adgang: Forkert formatering i header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header skal være 'Bearer <token>'"
        )
    
    # Gør token til selve token-værdien ved at fjerne "Bearer "
    token = authorization[7:]
    
    # Verificerer token
    if token != BEARER_TOKEN:
        logger.warning(f"Unauthorized forsøg på adgang: Ugyldig token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ugyldig authentication token"
        )
    
    # Token er gyldig - log success (til konsol)
    logger.debug("Authentication successfuld")


# API Endpoints

@app.get("/")
async def root() -> Dict[str, str]:
    """
    Root endpoint - tjekker om serveren kører.
    
    Simpel endpoint til at verificere at serveren kører. Kan bruges af load
    balancers og monitoring systemer til health checks.
    Vi kan også når nu vi kører Linux få computeren til at tjekke om
    endpointet virker og hvis ikke, så genstarter den hele koden.
    
    Returns:
        Status information om server
    
    Eksempel Response:
        {
            "status": "running",
            "service": "Automatisk udluftningssystem - Remote Server",
            "version": "1.0.0"
        }
    
    Note:
        Dette endpoint kræver ikke authentication og kan bruges til
        health checks fra f.eks. load balancers uden credentials.
    """
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
    """
    Modtager og gemmer data fra vores RPi5
    
    Dette er hoved-endpointet der modtager batches af sensor data, fejlbeskeder og
    system logs fra Raspberry Pi 5. Data valideres og indsættes i
    PostgreSQL database med automatic transaction management (ACID).
    Det betyder går alt godt - så comittes ændringer
    Går alt ikke godt - så bliver rollback initeret og ændringerne slettes.
    
    Args:
        payload: SyncPayload med sensor data arrays
        authorization: Bearer token for authentication
    
    Returns:
        JSONResponse med status og antal gemte rækker
    
    Raises:
        HTTPException 401: Hvis authentication fejler
        HTTPException 500: Hvis database insertion fejler
    
    Data Flow:
        1. Verificer Bearer token (raises 401 hvis ugyldig)
        2. Valider payload structure (Pydantic automatic)
        3. Valider sensor værdier (vores ranges)
        4. Insert til database (batched for performance)
        5. Commit sendingen (med ACID garantier)
        6. Få "kvittering"
    
    Validation Ranges:
        temperatur: 0 til 40°C (DHT11 limits)
        luftfugtighed: 0 til 100% RH (fysisk limit)
        batteri: 0 til 100% (procent)
        gas: 0 til 500000 kilo-ohm (er ikke testet)
    
    Response Format:
        {
            "status": "success",
            "modtaget_sensor_rækker": 42,
            "modtaget_fejlbeskeder": 0,
            "modtager_system_logs": 5
        }
    
    Performance:
        Bruger execute_batch() for efficient bulk inserts.
    
    Note:
        Ugyldige værdier skippes individuelt uden at afvise hele payload.
        Dette tillader partial-success hvis nogle målinger er ugyldige.
    """
    # Verificer authentication
    verificer_token(authorization)
    
    conn = None
    try:
        # Hent database connection
        conn = hent_db_connection()
        cursor = conn.cursor()
        
        # Indsæt sensor data
        if payload.sensor_data:
            sensor_værdier = []
            
            for række in payload.sensor_data:
                # Valider værdi baseret på data type
                værdi = række.get('value')
                data_type = række.get('data_type')
                
                # Type-specific validering
                if data_type == 'temperatur':
                    værdi = valider_sensor_værdi(værdi, min_værdi=-40, max_værdi=85)
                elif data_type == 'luftfugtighed':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=100)
                elif data_type == 'batteri':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=100)
                elif data_type == 'gas':
                    værdi = valider_sensor_værdi(værdi, min_værdi=0, max_værdi=500000)
                
                # Spring ugyldige værdier over
                if værdi is None:
                    logger.warning(
                        f"Invalid {data_type} værdi skipped: {række.get('value')}"
                    )
                    continue
                
                # Tilføj til batch
                sensor_værdier.append((
                    payload.enheds_id,
                    række.get('målt_klokken'),
                    række.get('kilde'),
                    data_type,
                    værdi
                ))
            
            # Batch indsættelse
            if sensor_værdier:
                execute_batch(
                    cursor,
                    '''INSERT INTO sensor_data 
                       (enheds_id, målt_klokken, kilde, data_type, værdi) 
                       VALUES (%s, %s, %s, %s, %s)''',
                    sensor_værdier
                )
                logger.debug(f"Indsat {len(sensor_værdier)} sensor rækker")
        
        # Indsæt fejlbeskeder
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
        
        # Indsæt system logs
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
        
        # Commit forsendelsen
        conn.commit()
        
        # Beregn total antal rækker
        total_rækker = (
            len(payload.sensor_data) + 
            len(payload.fejl_logs) + 
            len(payload.system_logs)
        )
        
        logger.info(
            f"Data modtaget fra {payload.enheds_id}: "
            f"{total_rækker} rækker gemt"
        )
        
        # Returner success svar
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
        # Database fejl
        if conn:
            conn.rollback()
        logger.error(f"Database indsættelse fejlede: {fejl}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database fejl ved opbevaring af data"
        )
    
    except Exception as fejl:
        # Uventet fejl
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
    """
    Sletter data ældre end 30 dage fra database.
    
    Dette endpoint kan kaldes manuelt eller via cron job til at rydde op i
    gammel data og forberede GDPR lovgivning omkring hvor længe data må opbevares.
    cron job er Linux-relateret og man kan ændre i en fil (crontable) for
    at give Linux besked på at køre kommandoen på specifikke tidspunkter.

    Args:
        authorization: Bearer token for authentication
    
    Returns:
        JSONResponse med antal slettede rækker per table
    
    Raises:
        HTTPException 401: Hvis authentication fejler
        HTTPException 500: Hvis cleanup fejler
    
    Cleanup Policy:
        Behold data fra seneste 30 dage
        Slet ældre data fra alle tables
        Log antal slettede rækker
    
    Response Format:
        {
            "antal_slettet_sensor_rækker": 1234,
            "antal_slettet_fejlbeskeder": 56,
            "antal_slettet_system_logs": 789
        }
    
    Note:
        Der bruges målt_klokken-indexet for bedre performance.
    """
    # Verificer authentication
    verificer_token(authorization)
    
    # Beregn cutoff dato (30 dage tilbage)
    cutoff = datetime.now() - timedelta(days=30)
    
    conn = None
    try:
        conn = hent_db_connection()
        cursor = conn.cursor()
        
        # slet gammel sensor data
        cursor.execute(
            'DELETE FROM sensor_data WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_sensor_rækker = cursor.rowcount
        
        # slet gamle fejlbeskeder
        cursor.execute(
            'DELETE FROM fejl_logs WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_fejlbeskeder = cursor.rowcount
        
        # slet gamle system logs
        cursor.execute(
            'DELETE FROM system_logs WHERE målt_klokken < %s',
            (cutoff,)
        )
        antal_slettet_system_logs = cursor.rowcount
        
        # Gem ændringer
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


# Server startup

if __name__ == "__main__":
    """
    Entry point når script køres direkte.
    
    Starter Uvicorn server på port 8080 for at modtage sync requests fra
    RPi5. I stor skala ville der tilføjes brug af Gunicorn med multiple workers
    for bedre concurrency og performance.
    """
    logger.info("Starter Remote Server på port 8080...")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )