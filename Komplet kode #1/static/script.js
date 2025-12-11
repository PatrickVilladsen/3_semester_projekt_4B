let ws;
let THRESHOLDS = null;



function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(protocol + '//' + window.location.host + '/ws');
    
    ws.onopen = () => {
        console.log('WebSocket forbundet');
        showToast('Forbundet til server', 'success', 2000);
    };
    
    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        
        if (message.thresholds) {
            THRESHOLDS = message.thresholds;
            console.log('Thresholds modtaget:', THRESHOLDS);
        }
        
        if (message.data) {
            updateUI(message.data);
        }
    };
    
    ws.onerror = (error) => {
        console.error('WebSocket fejl:', error);
        showToast('WebSocket fejl', 'error');
    };
    
    ws.onclose = () => {
        console.log('WebSocket forbindelse lukket');
        showToast('Forbindelse tabt', 'warning');
        setTimeout(connect, 3000);
    };
}

function updateUI(data) {
    if (data.sensor) {
        updateElement('outdoorTemp', data.sensor.temperature, '°C');
        updateElement('outdoorHum', data.sensor.humidity, '%');
        updateElement('outdoorBat', data.sensor.battery, '%');
    }
    
    if (data.bme680) {
        updateIndoorValue('indoorTemp', data.bme680.temperature, 'temp');
        updateIndoorValue('indoorHum', data.bme680.humidity, 'humidity');
        updateIndoorValue('indoorGas', data.bme680.gas, 'gas');
    }
    
    if (data.vindue) {
        updateWindowStatus(data.vindue.status);
    }
}

function updateElement(elementId, value, unit) {
    const element = document.getElementById(elementId);
    if (!element) return;
    
    if (value !== null && value !== undefined) {
        const displayValue = typeof value === 'number' ? Math.round(value * 10) / 10 : value;
        element.textContent = displayValue + ' ' + unit;
    } else {
        element.textContent = '-- ' + unit;
    }
}

function updateIndoorValue(elementId, value, type) {
    const element = document.getElementById(elementId);
    if (!element) return;
    
    if (!THRESHOLDS) {
        element.textContent = '--';
        element.className = 'indoor-value normal';
        return;
    }
    
    if (value === null || value === undefined) {
        element.textContent = '--';
        element.className = 'indoor-value normal';
        return;
    }
    
    if (type === 'gas') {
        element.textContent = Math.round(value / 1000);
    } else {
        element.textContent = Math.round(value);
    }
    
    let className = 'indoor-value ';
    
    if (type === 'temp') {
        if (value >= THRESHOLDS.temp.max) {
            className += 'max';
        } else if (value >= THRESHOLDS.temp.limit_high) {
            className += 'too_high';
        } else {
            className += 'normal';
        }
    } else if (type === 'humidity') {
        if (value >= THRESHOLDS.humidity.max) {
            className += 'max';
        } else if (value >= THRESHOLDS.humidity.limit_high) {
            className += 'too_high';
        } else {
            className += 'normal';
        }
    } else if (type === 'gas') {
        if (value <= THRESHOLDS.gas.min) {
            className += 'min';
        } else if (value <= THRESHOLDS.gas.limit_line) {
            className += 'limit';
        } else {
            className += 'normal';
        }
    }
    
    element.className = className;
}

function updateWindowStatus(status) {
    const statusBox = document.getElementById('windowStatus');
    const image = document.getElementById('windowImage');
    const text = document.getElementById('windowText');
    
    if (status === 'aaben') {
        statusBox.className = 'window-status-box open';
        image.src = '/static/images/open.png';
        image.alt = 'Åbent vindue';
        text.textContent = 'Åben';
    } else {
        statusBox.className = 'window-status-box closed';
        image.src = '/static/images/closed.png';
        image.alt = 'Lukket vindue';
        text.textContent = 'Lukket';
    }
}

function showToast(message, type = 'error', duration = 4000) {
    const toast = document.getElementById('toast');
    const toastIcon = document.getElementById('toastIcon');
    const toastMessage = document.getElementById('toastMessage');
    
    const icons = {
        error: '❌',
        success: '✅',
        warning: '⚠️',
        info: 'ℹ️'
    };
    
    toastIcon.textContent = icons[type] || '⚠️';
    toastMessage.textContent = message;
    toast.className = 'toast show ' + type;
    
    setTimeout(() => {
        toast.classList.remove('show');
    }, duration);
}

function updateClock() {
    const now = new Date();
    
    const timeString = now.toLocaleTimeString('da-DK', {
        timeZone: 'Europe/Copenhagen',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });
    
    const dateString = now.toLocaleDateString('da-DK', {
        timeZone: 'Europe/Copenhagen',
        day: '2-digit',
        month: '2-digit'
    });
    
    document.getElementById('clockTime').textContent = timeString;
    document.getElementById('clockDate').textContent = dateString;
}

function sendCommand(command) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.error('WebSocket ikke forbundet');
        showToast('Ingen forbindelse til server', 'error');
        return;
    }
    
    const message = {
        type: 'vindue_command',
        command: command
    };
    
    console.log('Sender kommando:', message);
    ws.send(JSON.stringify(message));
    
    const commandText = command === 'aaben' ? 'Åbner vindue' : 'Lukker vindue';
    showToast(commandText, 'info', 2000);
}

function init() {
    connect();
    updateClock();
    setInterval(updateClock, 1000);
}

window.sendCommand = sendCommand;

init();