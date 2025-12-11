import json
from sensor_data import data_opbevaring

# Her opdaterer vi vores klienter med ny data til hjemmesiden

# Vi sender asynkront ny data til vores klienter
async def broadcast_to_websockets(update_type: str):
    # Vi tjekker hvem der skal modtage data ved at se forbunde klienter
    clients = data_opbevaring.get_websocket_clients()
    
    # Hvis der ikke er nogle så er der ikke nogle at skulle opdatere til
    if not clients:
        return
    
    # Vi samler den nye data
    data = data_opbevaring.get_all_data()
    # Vi pakker den vores json dump ud med den nye data
    message = json.dumps({
        'type': 'update',
        'update_type': update_type,
        'data': data
    })
    
    # Vi opretter en set-liste over de klienter der er disconnectet
    disconnected = set()
    # Vi går igennem alle klienter og forventer en verifikation på at de lytter
    for client in clients:
        try:
            await client.send_text(message)
            # Hvis vi ikke kan sende dem besked tilføjer vi dem på set-listen over disconnectet klienter
        except:
            disconnected.add(client)
    
    # Vi giver så besked på at de skal fjernes som aktive lyttere så vi ikke sender beskeder til dem længere.
    for client in disconnected:
        data_opbevaring.remove_websocket_client(client)