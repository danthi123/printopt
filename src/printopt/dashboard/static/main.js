var statusEl = document.getElementById('connection-status');
var printerEl = document.getElementById('printer-status');
var pluginEl = document.getElementById('plugin-list');
var panels = document.querySelectorAll('.plugin-panel');

var ws = null;
var activePlugin = null;
var latestData = {};

function connect() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
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
        latestData = JSON.parse(event.data);
        if (latestData.printer) updatePrinter(latestData.printer);
        if (latestData.plugins) updatePlugins(latestData.plugins);
        updateActivePanel();
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
    var lines = [];
    lines.push(printer.host || '--');
    lines.push('');
    lines.push('State: ' + (s.state || 'unknown').toUpperCase());
    if (s.filename) lines.push('File:  ' + s.filename);
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
    // Clear and rebuild plugin list
    while (pluginEl.firstChild) pluginEl.removeChild(pluginEl.firstChild);

    var names = Object.keys(plugins);
    if (names.length === 0) {
        pluginEl.textContent = 'No plugins active';
        return;
    }

    names.forEach(function(name) {
        var div = document.createElement('div');
        div.className = 'plugin-item' + (activePlugin === name ? ' active' : '');
        var indicator = plugins[name].enabled ? '[ON]  ' : '[OFF] ';
        div.textContent = indicator + name;
        div.addEventListener('click', function() { selectPlugin(name); });
        pluginEl.appendChild(div);
    });
}

function selectPlugin(name) {
    activePlugin = name;
    panels.forEach(function(p) { p.style.display = 'none'; });
    var panel = document.getElementById('panel-' + name);
    if (panel) {
        panel.style.display = 'block';
    } else {
        document.getElementById('panel-none').style.display = 'block';
    }
    // Re-render plugin list to show active state
    if (latestData.plugins) updatePlugins(latestData.plugins);
    updateActivePanel();
}

function updateActivePanel() {
    if (!activePlugin || !latestData.plugins) return;
    var data = latestData.plugins[activePlugin];
    if (!data) return;

    if (activePlugin === 'vibration') updateVibrationPanel(data);
    if (activePlugin === 'flow') updateFlowPanel(data);
    if (activePlugin === 'thermal') updateThermalPanel(data);
}

function updateVibrationPanel(data) {
    var el = document.getElementById('vib-status');
    var results = data.results || {};

    if (Object.keys(results).length === 0) {
        el.textContent = 'No analysis data. Run: printopt vibration analyze';
        return;
    }

    var lines = [];
    ['x', 'y'].forEach(function(axis) {
        var r = results[axis];
        if (!r) return;
        var best = r.best;
        lines.push(axis.toUpperCase() + ' Axis:');
        if (best) {
            lines.push('  Recommended: ' + best.shaper_type.toUpperCase() + ' @ ' + best.frequency + ' Hz');
        }
        if (r.peaks && r.peaks.length > 0) {
            lines.push('  Peaks: ' + r.peaks.map(function(p) {
                return p.frequency.toFixed(1) + ' Hz';
            }).join(', '));
        }
        if (r.shapers && r.shapers.length > 0) {
            lines.push('  Alternatives:');
            r.shapers.slice(0, 3).forEach(function(s) {
                lines.push('    ' + s.shaper_type + ' @ ' + s.frequency + ' Hz (vib: ' + s.remaining_vibration.toFixed(4) + ')');
            });
        }
        lines.push('');
    });
    el.textContent = lines.join('\n');

    // Draw FFT plots
    drawFFTPlots(results);
}

function drawFFTPlots(results) {
    var canvas = document.getElementById('vib-canvas');
    var ctx = canvas.getContext('2d');
    var w = canvas.width;
    var h = canvas.height;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#0a0a1a';
    ctx.fillRect(0, 0, w, h);

    var axes = ['x', 'y'];
    var colors = { x: '#3498db', y: '#e74c3c' };
    var peakColors = { x: '#2ecc71', y: '#f39c12' };

    // Find global max for scaling
    var allValues = [];
    axes.forEach(function(axis) {
        var r = results[axis];
        if (r && r.psd_values) {
            r.psd_values.forEach(function(v) { if (v > 0) allValues.push(v); });
        }
    });

    if (allValues.length === 0) return;

    var maxVal = Math.max.apply(null, allValues);
    var minVal = Math.min.apply(null, allValues.filter(function(v) { return v > 0; }));
    if (minVal <= 0) minVal = 1e-6;

    var margin = { top: 30, right: 20, bottom: 40, left: 60 };
    var plotW = w - margin.left - margin.right;
    var plotH = h - margin.top - margin.bottom;

    // Draw axes
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(margin.left, margin.top);
    ctx.lineTo(margin.left, h - margin.bottom);
    ctx.lineTo(w - margin.right, h - margin.bottom);
    ctx.stroke();

    // Axis labels
    ctx.fillStyle = '#888';
    ctx.font = '11px Consolas, Monaco, monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Frequency (Hz)', w / 2, h - 5);

    ctx.save();
    ctx.translate(12, h / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText('PSD (log)', 0, 0);
    ctx.restore();

    // Title
    ctx.fillStyle = '#ccc';
    ctx.font = '13px Consolas, Monaco, monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Vibration Analysis — Frequency Response', w / 2, 18);

    // Frequency ticks
    ctx.fillStyle = '#666';
    ctx.font = '10px Consolas, Monaco, monospace';
    var maxFreq = 200;
    for (var f = 0; f <= maxFreq; f += 25) {
        var fx = margin.left + (f / maxFreq) * plotW;
        ctx.fillText(f.toString(), fx, h - margin.bottom + 15);
        ctx.strokeStyle = '#222';
        ctx.beginPath();
        ctx.moveTo(fx, margin.top);
        ctx.lineTo(fx, h - margin.bottom);
        ctx.stroke();
    }

    // Draw PSD curves
    axes.forEach(function(axis) {
        var r = results[axis];
        if (!r || !r.psd_freqs || !r.psd_values) return;

        var freqs = r.psd_freqs;
        var values = r.psd_values;

        ctx.strokeStyle = colors[axis];
        ctx.lineWidth = 2;
        ctx.beginPath();

        var logMin = Math.log10(minVal);
        var logMax = Math.log10(maxVal);
        var logRange = logMax - logMin;
        if (logRange <= 0) logRange = 1;

        for (var i = 0; i < freqs.length; i++) {
            var fx = margin.left + (freqs[i] / maxFreq) * plotW;
            var val = Math.max(values[i], minVal);
            var fy = margin.top + plotH - ((Math.log10(val) - logMin) / logRange) * plotH;

            if (i === 0) ctx.moveTo(fx, fy);
            else ctx.lineTo(fx, fy);
        }
        ctx.stroke();

        // Draw peak markers
        if (r.peaks) {
            r.peaks.forEach(function(peak) {
                var px = margin.left + (peak.frequency / maxFreq) * plotW;
                ctx.strokeStyle = peakColors[axis];
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(px, margin.top);
                ctx.lineTo(px, h - margin.bottom);
                ctx.stroke();
                ctx.setLineDash([]);

                // Label
                ctx.fillStyle = peakColors[axis];
                ctx.font = '10px Consolas, Monaco, monospace';
                ctx.textAlign = 'center';
                ctx.fillText(peak.frequency.toFixed(0) + 'Hz', px, margin.top - 5);
            });
        }
    });

    // Legend
    var legendX = w - margin.right - 100;
    var legendY = margin.top + 10;
    axes.forEach(function(axis, i) {
        if (!results[axis]) return;
        ctx.fillStyle = colors[axis];
        ctx.fillRect(legendX, legendY + i * 18, 12, 12);
        ctx.fillStyle = '#ccc';
        ctx.font = '11px Consolas, Monaco, monospace';
        ctx.textAlign = 'left';
        ctx.fillText(axis.toUpperCase() + ' axis', legendX + 18, legendY + i * 18 + 10);
    });
}

function updateFlowPanel(data) {
    var statsEl = document.getElementById('flow-stats');
    var lines = [];
    lines.push('Enabled: ' + (data.enabled ? 'YES' : 'NO'));
    lines.push('Total adjustments: ' + (data.total_adjustments || 0));
    lines.push('Features ahead: ' + (data.features_ahead || 0));
    statsEl.textContent = lines.join('\n');

    var timelineEl = document.getElementById('flow-timeline');
    var comps = data.active_compensations || [];
    if (comps.length === 0) {
        timelineEl.textContent = 'No active compensations';
    } else {
        var compLines = comps.map(function(c) {
            return c.type + ': ' + c.value;
        });
        timelineEl.textContent = compLines.join('\n');
    }
}

function updateThermalPanel(data) {
    var statsEl = document.getElementById('thermal-stats');
    var lines = [];
    lines.push('Material: ' + (data.material || '--'));
    lines.push('Layer: ' + (data.layer || 0));
    if (data.max_temp !== undefined) lines.push('Max temp: ' + data.max_temp.toFixed(1) + ' C');
    if (data.max_gradient !== undefined) lines.push('Max gradient: ' + data.max_gradient.toFixed(2) + ' C/mm');
    if (data.hotspot_count !== undefined) lines.push('Hotspots: ' + data.hotspot_count);
    statsEl.textContent = lines.join('\n');

    // Draw thermal heatmap if we have grid data
    if (data.heatmap) {
        drawHeatmap(data.heatmap);
    }

    var warningsEl = document.getElementById('thermal-warnings');
    var warnings = data.warnings || [];
    if (warnings.length === 0) {
        warningsEl.textContent = '';
    } else {
        var wLines = warnings.map(function(w) {
            return 'Layer ' + w.layer + ': ' + w.type + (w.value ? ' (' + w.value.toFixed(1) + ')' : '');
        });
        warningsEl.textContent = 'Warnings:\n' + wLines.join('\n');
    }
}

function drawHeatmap(heatmapData) {
    var canvas = document.getElementById('thermal-canvas');
    var ctx = canvas.getContext('2d');
    var rows = heatmapData.length;
    var cols = heatmapData[0] ? heatmapData[0].length : 0;
    if (rows === 0 || cols === 0) return;

    var cellW = canvas.width / cols;
    var cellH = canvas.height / rows;

    for (var y = 0; y < rows; y++) {
        for (var x = 0; x < cols; x++) {
            var temp = heatmapData[y][x];
            ctx.fillStyle = tempToColor(temp);
            ctx.fillRect(x * cellW, y * cellH, cellW + 1, cellH + 1);
        }
    }
}

function tempToColor(temp) {
    // Map temperature to color: blue (cold/ambient 25C) -> red (hot/200C)
    var min = 25, max = 150;
    var ratio = Math.max(0, Math.min(1, (temp - min) / (max - min)));
    var r = Math.floor(255 * ratio);
    var b = Math.floor(255 * (1 - ratio));
    var g = Math.floor(100 * (1 - Math.abs(ratio - 0.5) * 2));
    return 'rgb(' + r + ',' + g + ',' + b + ')';
}

function killAll() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({action: 'kill_all'}));
    }
}

function resetAll() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({action: 'reset'}));
    }
}

function sendAction(action) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({action: action}));
    }
}

document.getElementById('btn-kill').addEventListener('click', killAll);
document.getElementById('btn-reset').addEventListener('click', resetAll);
document.getElementById('btn-vib-analyze').addEventListener('click', function() {
    this.disabled = true;
    this.textContent = 'Running...';
    sendAction('run_vibration');
    var btn = this;
    setTimeout(function() { btn.disabled = false; btn.textContent = 'Run Analysis'; }, 90000);
});
document.getElementById('btn-flow-enable').addEventListener('click', function() { sendAction('enable_flow'); });
document.getElementById('btn-flow-disable').addEventListener('click', function() { sendAction('disable_flow'); });
document.getElementById('btn-thermal-enable').addEventListener('click', function() { sendAction('enable_thermal'); });
document.getElementById('btn-thermal-disable').addEventListener('click', function() { sendAction('disable_thermal'); });
connect();
