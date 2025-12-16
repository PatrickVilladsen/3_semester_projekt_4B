/*
 * Client-Side JavaScript til Automatisk Udluftningssystem.
 * 
 * Håndterer real-time WebSocket kommunikation, UI opdateringer,
 * graf-rotation og vindues-kontrol med Press'N'Hold funktionalitet.
 * 
 * Bruger 'use strict' kræver at variabler er definerer før de kan bruges
 */

'use strict';


/* Global State - variabler der kan bruges i alle funktioner */

/* global variabel til websocket - værdien ændres senere til at få websocket funktionalitet*/
let ws = null;

/* Opretter global variabel til vores GRÆNSER fra config.py */
let GRÆNSER = null;


/* Graf-roteringssystem */

/* Index for nuværende graf i rotation. */
let GrafIndex = 0;

/* Tilgængelige graf-typer til rotation. */
const GRAF_TYPER = ['temperatur', 'luftfugtighed', 'gas'];

/* Interval mellem graf-rotationer i millisekunder (15 sekunder). */
const GRAF_ROTATIONSINTERVAL = 15000;


/* Reconnection Konfiguration */

/* Forsinkelse før reconnect-forsøg i millisekunder. */
const RECONNECT_DELAY = 3000;

/* Flag der forhindrer flere samtidige reconnect-forsøg. */
let genforbinder = false;


/* Press and Hold System */

/*
 * Varighed for langt tryk i millisekunder.
 * Kort tryk < 1000ms = Manuel kommando (1/5 bevægelse)
 * Langt tryk >= 1000ms = Automatisk kommando (fuld bevægelse)
 */
const LANGT_TRYK_TID = 1000;

/* State-tracking for press & hold funktionalitet. */
const pressState = {
    timer: null,
    startTime: 0,
    isLongPress: false,
    kommando: null
};


/* WebSocket Functions */

/*
 * Opretter WebSocket forbindelse til server.
 * 
 * Håndterer automatisk protokol-valg (ws/wss) baseret på page protokol.
 * Ved forbindelse sendes data og grænser til klienten.
 */
function connect() {
    if (genforbinder) {
        console.log('Reconnect allerede i gang, springer over');
        return;
    }
    
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        console.log(`Forbinder til WebSocket: ${wsUrl}`);
        
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
            console.log('WebSocket forbundet');
            genforbinder = false;
            showToast('Forbundet til server', 'success', 2000);
        };
        
        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                
                console.log('WebSocket besked modtaget:', message.type);
                
                if (message.grænser) {
                    GRÆNSER = message.grænser;
                    console.log('Grænsr indlæst:', GRÆNSER);
                }
                
                if (message.data) {
                    updateUI(message.data);
                }
                
            } catch (fejl) {
                console.error('Fejl ved parsing af WebSocket besked:', fejl);
            }
        };
        
        ws.onerror = (fejl) => {
            console.error('WebSocket fejl:', fejl);
            showToast('WebSocket forbindelsesfejl', 'fejl', 3000);
        };
        
        ws.onclose = (event) => {
            console.log(`WebSocket lukket. Code: ${event.code}, Reason: ${event.reason || 'Ingen grund'}`);
            
            showToast('Forbindelse tabt - genetablerer forbindelsen', 'advarsel', 3000);
            
            planlægGenforbindelse();
        };
        
    } catch (fejl) {
        console.error('Fejl ved oprettelse af WebSocket:', fejl);
        showToast('Kunne ikke oprette forbindelse', 'fejl');
        planlægGenforbindelse();
    }
}

/**
 * Planlægger reconnect-forsøg efter RECONNECT_DELAY.
 * 
 * Forhindrer flere samtidige reconnect-forsøg via genforbinder-flag.
 */
function planlægGenforbindelse() {
    if (genforbinder) {
        return;
    }
    
    genforbinder = true;
    console.log(`Reconnect scheduled om ${RECONNECT_DELAY}ms`);
    
    setTimeout(() => {
        connect();
    }, RECONNECT_DELAY);
}


/* DOM opdateringsfunktion */

/* Opdaterer UI med ny sensor data. */
function updateUI(data) {
    if (data.sensor) {
        opdaterUdendørsData(data.sensor); /* Opdater DHT11 data */
    }
    
    if (data.bme680) {
        opdaterIndendørsData(data.bme680); /* Opdater bme680 data */
    }
    
    if (data.vindue) {
        opdaterVinduesStatus(data.vindue.status); /* Opdater vindue-status */
    }
}

/* Opdaterer udendørs sensor værdier (temperatur, luftfugtighed, batteri).*/
function opdaterUdendørsData(sensor) {
    updateElement('outdoorTemp', sensor.temperatur, '°C');
    updateElement('outdoorFugt', sensor.luftfugtighed, '%');
    updateElement('outdoorBat', sensor.batteri, '%');
}

/* Opdaterer indendørs sensor værdier med farve-kodning. */
function opdaterIndendørsData(bme680) {
    opdaterIndendørsVærdi('indoorTemp', bme680.temperatur, 'temperatur');
    opdaterIndendørsVærdi('indoorFugt', bme680.luftfugtighed, 'luftfugtighed');
    opdaterIndendørsVærdi('indoorGas', bme680.gas, 'gas');
}

/* Opdaterer enkeltvis DOM elementer med ny værdi og enhed. */
function updateElement(elementId, value, unit) {
    const element = document.getElementById(elementId);
    
    if (!element) {
        console.warn(`Element ikke fundet: ${elementId}`);
        return;
    }
    
    if (value !== null && value !== undefined) {
        const displayValue = typeof value === 'number' 
            ? Math.round(value * 10) / 10 
            : value;
        
        element.textContent = `${displayValue} ${unit}`;
    } else {
        element.textContent = `-- ${unit}`;
    }
}

/* Opdaterer indendørs værdi med farve-kodning baseret på grænserne. */
function opdaterIndendørsVærdi(elementId, value, type) {
    const element = document.getElementById(elementId);
    
    if (!element) {
        console.warn(`Element ikke fundet: ${elementId}`);
        return;
    }
    
    if (!GRÆNSER || value === null || value === undefined) {
        element.textContent = '--';
        element.className = 'indoor-value normal';
        return;
    }
    
    let displayValue;
    if (type === 'gas') {
        displayValue = Math.round(value / 1000);
    } else {
        displayValue = Math.round(value);
    }
    
    element.textContent = displayValue;
    
    const farveKlasse = bestemFarveKlasse(value, type);
    element.className = `indoor-value ${farveKlasse}`;
}

/* Bestemmer CSS-klasse baseret på sensor værdi og grænser. */
function bestemFarveKlasse(value, type) {
    const grænser = GRÆNSER[type];
    
    if (!grænser) {
        console.warn(`Grænser ikke fundet for type: ${type}`);
        return 'normal';
    }
    
    switch (type) {
        case 'temperatur':
        case 'luftfugtighed':
            if (value >= grænser.max) {
                return 'max';
            } 
            else if (value >= grænser.limit_high) {
                return 'too_high';
            }
            else if (value <= grænser.limit_low) {
                return 'too_low';
            }
            else {
                return 'normal';
            }
        
        case 'gas':
            if (value <= grænser.min) {
                return 'min';
            } else if (value <= grænser.limit_line) {
                return 'limit';
            } else {
                return 'normal';
            }
        
        default:
            console.warn(`Ukendt sensor type: ${type}`);
            return 'normal';
    }
}

/* Opdaterer vindues-status visning (åben/lukket). */
function opdaterVinduesStatus(status) {
    const statusBox = document.getElementById('vinduesStatus');
    const statusIkon = document.getElementById('statusIkon');
    const statusVærdi = document.getElementById('statusVærdi');
    
    if (!statusBox || !statusIkon || !statusVærdi) {
        console.warn('VinduesStaus elementer ikke fundet');
        return;
    }
    
    if (status === 'aaben') {
        statusBox.className = 'vinduesstatus open';
        statusIkon.src = '/static/images/open.png';
        statusIkon.alt = 'Vindue åbent';
        statusVærdi.textContent = 'ÅBEN';
        statusVærdi.setAttribute('aria-label', 'Vindue er åbent');
    } else {
        statusBox.className = 'vinduesstatus closed';
        statusIkon.src = '/static/images/closed.png';
        statusIkon.alt = 'Vindue lukket';
        statusVærdi.textContent = 'LUKKET';
        statusVærdi.setAttribute('aria-label', 'Vindue er lukket');
    }
}


/* Toast Notification System */

/*Viser toast notification til brugeren. */
function showToast(message, type = 'fejl', duration = 4000) {
    const toast = document.getElementById('toast');
    const toastIkon = document.getElementById('toastIkon');
    const toastBesked = document.getElementById('toastBesked');
    
    if (!toast || !toastIkon || !toastBesked) {
        console.warn('Toast elementer ikke fundet');
        return;
    }
    
    const ikoner = {
        fejl: '❌',
        success: '✅',
        advarsel: '⚠️',
        info: 'ℹ️'
    };
    
    toastIkon.textContent = ikoner[type] || '⚠️';
    toastBesked.textContent = message;
    
    toast.className = `toast show ${type}`;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, duration);
}


/* Ur-Funktioner */

/*
 * Opdaterer ur-display med tid og dato.
 * 
 * Køres hvert sekund via setInterval i init().
 */
function opdaterUr() {
    const klokken = document.getElementById('klokken');
    const dato = document.getElementById('dato');
    
    if (!klokken || !dato) {
        console.warn('Clock elementer ikke fundet');
        return;
    }
    
    const now = new Date();
    
    const klokkenString = now.toLocaleTimeString('da-DK', {
        timeZone: 'Europe/Copenhagen',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });
    
    const datoString = now.toLocaleDateString('da-DK', {
        timeZone: 'Europe/Copenhagen',
        day: '2-digit',
        month: '2-digit'
    });
    
    klokken.textContent = klokkenString;
    klokken.setAttribute('datetime', now.toISOString());
    dato.textContent = datoString;
}


/* Vindueskontrollerings-Funktioner - Press & Hold */

/*
 * Håndterer start af tryk (press).
 * 
 * Starter timer til long press detection. Ved langt tryk (>=1000ms)
 * sendes åbnings / luknings kommando. 
 * Ved kort tryk sendes manuel kommando når trykket slippes.
 */
function startTryk(baseCommand, event) {
    event.preventDefault();
    
    pressState.isLongPress = false;
    pressState.kommando = baseCommand;
    pressState.startTime = Date.now();
    
    pressState.timer = setTimeout(() => {
        pressState.isLongPress = true;
        
        // Langt tryk: Send automatisk-kommando (aaben/luk)
        sendKommando(baseCommand);
        
        const handling = baseCommand === 'aaben' ? 'åbning' : 'lukning';
        showToast(`Automatisk ${handling} startet`, 'info', 2000);
        
        console.log(`Langt press: ${baseCommand} (automatisk)`);
    }, LANGT_TRYK_TID);
    
    console.log(`Press startet: ${baseCommand}`);
}

/*
 * Håndterer afslutning af tryk.
 * 
 * Ved kort tryk sendes manuel kommando (1/5 bevægelse).
 * Ved langt tryk gøres intet da kommando allerede er sendt.
  */
function håndterSlipAfKnap(event) {
    event.preventDefault();
    
    if (pressState.timer) {
        clearTimeout(pressState.timer);
        pressState.timer = null;
    }
    
    const duration = Date.now() - pressState.startTime;
    
    if (!pressState.isLongPress && pressState.kommando) {
        // Kort tryk: Send manuel kommando (manuel_aaben/manuel_luk)
        const manuelCommand = `manuel_${pressState.kommando}`;
        sendKommando(manuelCommand);
        
        const action = pressState.kommando === 'aaben' ? 'åbning' : 'lukning';
        showToast(`Manuel ${action} (1/5)`, 'info', 2000);
        
        console.log(`Kort tryk opdaget (${duration}ms): ${manuelCommand}`);
    }
    
    pressState.kommando = null;
    pressState.startTime = 0;
}

/*
 * Håndterer annullering af tryk 
 * 
 * Nulstiller press state uden at sende kommando.
 */
function håndterTrykAnnullering(event) {
    if (pressState.timer) {
        clearTimeout(pressState.timer);
        pressState.timer = null;
    }
    
    pressState.kommando = null;
    pressState.startTime = 0;
    pressState.isLongPress = false;
    
    console.log('Tryk annulleret');
}

/*
 * Sender vindues-kommando til server via WebSocket.
 * 
 * Validerer kommando og WebSocket forbindelse før afsendelse.
 */
function sendKommando(kommando) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.warn('Kan ikke sende kommando - ingen forbindelse');
        showToast('Ingen forbindelse til server', 'fejl', 3000);
        return;
    }
    
    // Validering med alle tilladte kommandoer
    const tilladteKommandoer = ['aaben', 'luk', 'manuel_aaben', 'manuel_luk', 'kort_aaben'];
    if (!tilladteKommandoer.includes(kommando)) {
        console.error(`Ugyldig kommando: ${kommando}`);
        showToast('Ugyldig kommando', 'fejl');
        return;
    }
    
    try {
        const besked = JSON.stringify({
            type: 'vindue_command',
            kommando: kommando
        });
        
        ws.send(besked);
        console.log(`Kommando sendt: ${kommando}`);
        
    } catch (fejl) {
        console.error('Fejl ved afsendelse af kommando:', fejl);
        showToast('Kunne ikke sende kommando', 'fejl');
    }
}

/*
 * Opsætter event listeners for vindues-kontrol knapper.
 * 
 * Tilføjer både mouse og touch events for udvidet support.
 */
function setupVinduesKontrol() {
    const åbenKnap = document.getElementById('åbenKnap');
    if (åbenKnap) {
        åbenKnap.addEventListener('mousedown', (e) => startTryk('aaben', e));
        åbenKnap.addEventListener('mouseup', håndterSlipAfKnap);
        åbenKnap.addEventListener('mouseleave', håndterTrykAnnullering);
        
        åbenKnap.addEventListener('touchstart', (e) => startTryk('aaben', e));
        åbenKnap.addEventListener('touchend', håndterSlipAfKnap);
        åbenKnap.addEventListener('touchcancel', håndterTrykAnnullering);
    }
    
    const lukKnap = document.getElementById('lukKnap');
    if (lukKnap) {
        lukKnap.addEventListener('mousedown', (e) => startTryk('luk', e));
        lukKnap.addEventListener('mouseup', håndterSlipAfKnap);
        lukKnap.addEventListener('mouseleave', håndterTrykAnnullering);
        
        lukKnap.addEventListener('touchstart', (e) => startTryk('luk', e));
        lukKnap.addEventListener('touchend', håndterSlipAfKnap);
        lukKnap.addEventListener('touchcancel', håndterTrykAnnullering);
    }
    
    console.log('✓ Window controls med press & hold konfigureret');
}

// Eksporter til global scope for console debug
window.sendKommando = sendKommando;


/* Graf-System */

/*
 * Indlæser nuværende graf baseret på GrafIndex.
 * 
 * Tilføjer timestamp query-parameter for at undgå browser caching.

 */
function loadGraf() {
    const grafPNG = document.getElementById('grafPNG');
    
    if (!grafPNG) {
        console.warn('Graf png-element ikke fundet');
        return;
    }
    
    const grafType = GRAF_TYPER[GrafIndex];
    const timestamp = new Date().getTime();
    
    grafPNG.src = `/api/graf/${grafType}?days=7&t=${timestamp}`;
    grafPNG.alt = `${grafType} graf - sidste 7 dage`;
    
    console.log(`Graf loaded: ${grafType}`);
}

/*
 * Roterer til næste graf i GRAF_TYPER array.
 * 
 * Kaldes automatisk hvert GRAF_ROTATIONSINTERVAL (15 sekunder).
 */
function roterGraf() {
    GrafIndex = (GrafIndex + 1) % GRAF_TYPER.length;
    loadGraf();
}


/* Opstarts-fase */

/*
 * initialiserer applikationen.
 * 
 * Starter:
 * - WebSocket forbindelse
 * - Ur-opdatering (hvert sekund)
 * - Graf-rotation (hvert 15. sekund)
 * - Vindues-kontrol event listeners
 */
function init() {
    console.log('Initialiserer applikationen');
    
    connect();
    
    opdaterUr();
    setInterval(opdaterUr, 1000);
    
    loadGraf();
    setInterval(roterGraf, GRAF_ROTATIONSINTERVAL);
    
    setupVinduesKontrol();
    
    console.log('Applikation initialiseret');
}


/* Error Handling */

/* Global error handler for uncaught exceptions. */
window.onerror = function(message, source, lineno, colno, error) {
    console.error('Global fejl:', {
        message,
        source,
        lineno,
        colno,
        error
    });
    
    showToast('En uventet fejl opstod', 'fejl');
    
    return false;
};

/*Handler for unhandled-promise rejections. */
window.onunhandledrejection = function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    showToast('En uventet fejl opstod', 'fejl');
    
    event.preventDefault();
};


/* Applikationsstart */

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}


/* Exports */

/*
 * Public API eksponeret på window objekt.
 * 
 * Tillader konsol debug og testing:
 * - window.app.connect()
 * - window.app.sendKommando('aaben')
 * - window.app.isConnected()
 */
window.app = {
    connect,
    sendCommand: sendKommando,
    showToast,
    updateUI,
    updateClock: opdaterUr,
    loadGraph: loadGraf,
    rotateGraph: roterGraf,
    getThresholds: () => GRÆNSER,
    isConnected: () => ws && ws.readyState === WebSocket.OPEN
};

console.log('Script loaded uden fejl');