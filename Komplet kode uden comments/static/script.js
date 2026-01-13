'use strict';

let ws = null;

let GRÆNSER = null;

let GrafIndex = 0;

const GRAF_TYPER = ['temperatur', 'luftfugtighed', 'gas'];

const GRAF_ROTATIONSINTERVAL = 15000;

const RECONNECT_DELAY = 3000;

let genforbinder = false;

const LANGT_TRYK_TID = 1000;

const pressState = {
    timer: null,
    startTime: 0,
    isLongPress: false,
    kommando: null
};

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

function updateUI(data) {
    if (data.sensor) {
        opdaterUdendørsData(data.sensor);
    }
    
    if (data.bme680) {
        opdaterIndendørsData(data.bme680); 
    }
    
    if (data.vindue) {
        opdaterVinduesStatus(data.vindue.status);
    }
}

function opdaterUdendørsData(sensor) {
    updateElement('outdoorTemp', sensor.temperatur, '°C');
    updateElement('outdoorFugt', sensor.luftfugtighed, '%');
    updateElement('outdoorBat', sensor.batteri, '%');
}

function opdaterIndendørsData(bme680) {
    opdaterIndendørsVærdi('indoorTemp', bme680.temperatur, 'temperatur');
    opdaterIndendørsVærdi('indoorFugt', bme680.luftfugtighed, 'luftfugtighed');
    opdaterIndendørsVærdi('indoorGas', bme680.gas, 'gas');
}

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

function startTryk(baseCommand, event) {
    event.preventDefault();
    
    pressState.isLongPress = false;
    pressState.kommando = baseCommand;
    pressState.startTime = Date.now();
    
    pressState.timer = setTimeout(() => {
        pressState.isLongPress = true;
        
        sendKommando(baseCommand);
        
        const handling = baseCommand === 'aaben' ? 'åbning' : 'lukning';
        showToast(`Automatisk ${handling} startet`, 'info', 2000);
        
        console.log(`Langt press: ${baseCommand} (automatisk)`);
    }, LANGT_TRYK_TID);
    
    console.log(`Press startet: ${baseCommand}`);
}

function håndterSlipAfKnap(event) {
    event.preventDefault();
    
    if (pressState.timer) {
        clearTimeout(pressState.timer);
        pressState.timer = null;
    }
    
    const duration = Date.now() - pressState.startTime;
    
    if (!pressState.isLongPress && pressState.kommando) {
        const manuelCommand = `manuel_${pressState.kommando}`;
        sendKommando(manuelCommand);
        
        const action = pressState.kommando === 'aaben' ? 'åbning' : 'lukning';
        showToast(`Manuel ${action} (1/5)`, 'info', 2000);
        
        console.log(`Kort tryk opdaget (${duration}ms): ${manuelCommand}`);
    }
    
    pressState.kommando = null;
    pressState.startTime = 0;
}

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

function sendKommando(kommando) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.warn('Kan ikke sende kommando - ingen forbindelse');
        showToast('Ingen forbindelse til server', 'fejl', 3000);
        return;
    }
    
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
    
    console.log('Window controls med press and hold konfigureret');
}

window.sendKommando = sendKommando;


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

function roterGraf() {
    GrafIndex = (GrafIndex + 1) % GRAF_TYPER.length;
    loadGraf();
}

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

window.onunhandledrejection = function(event) {
    console.error('Unhandled promise rejection:', event.reason);
    showToast('En uventet fejl opstod', 'fejl');
    
    event.preventDefault();
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

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