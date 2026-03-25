const statusEl = document.getElementById('connection-status');
const printerEl = document.getElementById('printer-status');
const pluginEl = document.getElementById('plugin-list');

let ws = null;

function connect() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws');

    ws.onopen = function() {
        statusEl.textContent = 'connected';
        statusEl.classList.add('connected');
    };

    ws.onclose = function() {
        statusEl.textContent = 'disconnected';
        statusEl.classList.remove('connected');
        setTimeout(connect, 2000);
    };

    ws.onmessage = function(event) {
        const data = JSON.parse(event.data);
        if (data.printer) { updatePrinter(data.printer); }
        if (data.plugins) { updatePlugins(data.plugins); }
    };
}

function updatePrinter(printer) {
    const s = printer.status || {};
    const bed = s.bed_temp !== undefined ? s.bed_temp : '--';
    const nozzle = s.nozzle_temp !== undefined ? s.nozzle_temp : '--';
    const fan = s.fan_speed !== undefined ? s.fan_speed : '--';
    const z = s.z_position !== undefined ? s.z_position : '--';
    const prog = s.progress !== undefined ? s.progress : 0;
    printerEl.textContent = 'Bed: ' + bed + 'C | Nozzle: ' + nozzle + 'C | Fan: ' + fan + '% | Z: ' + z + ' | Progress: ' + prog + '%';
}

function updatePlugins(plugins) {
    const names = Object.keys(plugins);
    const lines = names.map(function(name) {
        return (plugins[name].enabled ? '[ON] ' : '[OFF] ') + name;
    });
    pluginEl.textContent = lines.join('\n');
}

function killAll() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({action: 'kill_all'}));
    }
}

document.getElementById('btn-kill').addEventListener('click', killAll);
connect();
