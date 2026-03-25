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

function formatDuration(seconds) {
    if (!seconds || seconds <= 0) return '--:--';
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    return m + 'm ' + s + 's';
}

function updatePrinter(printer) {
    var s = printer.status || {};
    var host = printer.host || '--';

    var lines = [];
    lines.push(host);
    lines.push('');

    var state = (s.state || 'unknown').toUpperCase();
    lines.push('State: ' + state);

    if (s.filename) {
        lines.push('File: ' + s.filename);
    }

    lines.push('');
    lines.push('Nozzle: ' + (s.nozzle_temp || '--') + ' / ' + (s.nozzle_target || '--') + ' C');
    lines.push('Bed:    ' + (s.bed_temp || '--') + ' / ' + (s.bed_target || '--') + ' C');
    lines.push('Fan:    ' + (s.fan_speed !== undefined ? s.fan_speed : '--') + '%');

    lines.push('');
    lines.push('X: ' + (s.x_position !== undefined ? s.x_position : '--'));
    lines.push('Y: ' + (s.y_position !== undefined ? s.y_position : '--'));
    lines.push('Z: ' + (s.z_position !== undefined ? s.z_position : '--'));

    if (s.progress !== undefined && s.progress > 0) {
        lines.push('');
        lines.push('Progress: ' + s.progress + '%');
        lines.push('Duration: ' + formatDuration(s.print_duration));
    }

    printerEl.textContent = lines.join('\n');
}

function updatePlugins(plugins) {
    var names = Object.keys(plugins);
    if (names.length === 0) {
        pluginEl.textContent = 'No plugins active';
        return;
    }
    var lines = names.map(function(name) {
        return (plugins[name].enabled ? '[ON]  ' : '[OFF] ') + name;
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
