

import json
from typing import Set, Dict, Any
from fastapi import WebSocket
from sensor_data import data_opbevaring
from database import db
from config import ENHEDS_ID


async def broadcast_til_websockets(opdaterings_type: str) -> None:

    klienter: Set[WebSocket] = data_opbevaring.hent_websocket_klienter()
    
    if not klienter:
        print(f"Ingen WebSocket klienter - springer broadcast over ({opdaterings_type})")
        return
    
    print(f"Broadcaster {opdaterings_type} til {len(klienter)} klient(er)")
    
    try:
        sensor_data: Dict[str, Dict[str, Any]] = data_opbevaring.hent_alle_data()
        
        besked: str = json.dumps({
            'type': 'update',
            'update_type': opdaterings_type,
            'data': sensor_data
        })
        
        print(f"JSON besked klar: {len(besked)} bytes")
        
    except (TypeError, ValueError) as fejl:
        print(f"JSON serialization fejl: {fejl}")
        db.gem_fejl(
            ENHEDS_ID,
            'WebSocketHandler',
            f"JSON serialization fejl: {fejl}"
        )
        return
    
    frakoblede: Set[WebSocket] = set()
    
    for klient in klienter:
        try:
            await klient.send_text(besked)
            
        except ConnectionError as fejl:
            print(f"Klient disconnected under send: {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Connection error ved broadcast: {fejl}"
            )
        
        except RuntimeError as fejl:
            print(f"WebSocket allerede lukket: {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Runtime error ved broadcast (WebSocket lukket): {fejl}"
            )
        
        except Exception as fejl:
            print(f"Uventet fejl ved broadcast: {type(fejl).__name__} - {fejl}")
            frakoblede.add(klient)
            db.gem_fejl(
                ENHEDS_ID,
                'WebSocketHandler',
                f"Uventet fejl ved broadcast: {type(fejl).__name__} - {fejl}"
            )
    
    if frakoblede:
        print(f"Rydder op i {len(frakoblede)} disconnected klient(er)")
        
        for klient in frakoblede:
            data_opbevaring.fjern_websocket_klient(klient)
        
        db.gem_system_log(
            ENHEDS_ID,
            'WebSocketHandler',
            f"Fjernede {len(frakoblede)} disconnected klienter fra tracking"
        )
    else:
        print(f"Broadcast succesfuld til alle {len(klienter)} klient(er)")


async def broadcast_fejl(
    fejl_besked: str,
    kilde: str = 'System'
) -> None:

    print(f"Broadcaster fejl til frontend: {fejl_besked} (kilde: {kilde})")
    
    db.gem_fejl(ENHEDS_ID, kilde, fejl_besked)
    
    klienter: Set[WebSocket] = data_opbevaring.hent_websocket_klienter()
    
    if not klienter:
        print("Ingen WebSocket klienter - springer fejl broadcast over")
        return
    
    try:
        besked: str = json.dumps({
            'type': 'fejl',
            'fejl': fejl_besked,
            'kilde': kilde
        })
        
        print(f"Fejl besked serialiseret: {len(besked)} bytes")
        
    except (TypeError, ValueError) as fejl:
        print(f"Kunne ikke serialisere fejlbesked: {fejl}")
        db.gem_fejl(
            ENHEDS_ID,
            'WebSocketHandler',
            f"Kunne ikke serialisere fejlbesked: {fejl}"
        )
        return
    
    frakoblede: Set[WebSocket] = set()
    
    for klient in klienter:
        try:
            await klient.send_text(besked)
        except Exception as fejl:
            print(f"Klient fejlede under fejl-broadcast: {type(fejl).__name__}")
            frakoblede.add(klient)
    
    if frakoblede:
        print(f"Rydder op i {len(frakoblede)} disconnected klient(er)")
        for klient in frakoblede:
            data_opbevaring.fjern_websocket_klient(klient)
    else:
        print(f"Fejl broadcast succesfuld til alle {len(klienter)} klient(er)")