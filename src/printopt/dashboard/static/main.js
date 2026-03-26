var statusEl = document.getElementById('connection-status');
var printerEl = document.getElementById('printer-status');
var pluginEl = document.getElementById('plugin-list');
var ws = null;
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
        updateAllPanels();
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
    while (pluginEl.firstChild) pluginEl.removeChild(pluginEl.firstChild);
    var names = Object.keys(plugins);
    if (names.length === 0) {
        pluginEl.textContent = 'No plugins active';
        return;
    }
    names.forEach(function(name) {
        var div = document.createElement('div');
        div.className = 'plugin-item';
        var indicator = plugins[name].enabled ? '[ON]  ' : '[OFF] ';
        div.textContent = indicator + name;
        pluginEl.appendChild(div);
    });
}

function updateAllPanels() {
    if (!latestData.plugins) return;
    if (latestData.plugins.vibration) updateVibrationPanel(latestData.plugins.vibration);
    if (latestData.plugins.flow) updateFlowPanel(latestData.plugins.flow);
    if (latestData.plugins.thermal) updateThermalPanel(latestData.plugins.thermal);
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
    lines.push('State: ' + (data.state || '--'));
    if (data.filename) lines.push('File: ' + data.filename);
    if (data.progress > 0) lines.push('Progress: ' + data.progress.toFixed(1) + '%');
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

    var logEl = document.getElementById('flow-log');
    var log = data.log || [];
    if (log.length === 0) {
        logEl.textContent = 'No adjustments yet';
    } else {
        var logLines = log.map(function(entry) {
            var time = new Date(entry.time * 1000);
            var ts = time.toLocaleTimeString();
            return ts + ' ' + entry.feature + ' L' + entry.line + ': ' + entry.value;
        });
        logEl.textContent = logLines.join('\n');
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
    if (data.speed_adjusted) lines.push('Speed: REDUCED');
    if (data.fan_adjusted) lines.push('Fan: BOOSTED');
    if (data.print_active) lines.push('Status: ACTIVE');
    statsEl.textContent = lines.join('\n');

    // Draw toolpath if available, otherwise fall back to heatmap grid
    if (data.toolpath && data.toolpath.length > 0) {
        drawToolpath(data.toolpath, data.bed_x || 245, data.bed_y || 245, data.nozzle_pos);
    } else if (data.heatmap) {
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
    // Blue (25C) -> Cyan (50C) -> Green (75C) -> Yellow (100C) -> Red (150C)
    var min = 25, max = 150;
    var ratio = Math.max(0, Math.min(1, (temp - min) / (max - min)));

    var r, g, b;
    if (ratio < 0.25) {
        // Blue to Cyan
        var t = ratio / 0.25;
        r = 0; g = Math.floor(255 * t); b = 255;
    } else if (ratio < 0.5) {
        // Cyan to Green
        var t = (ratio - 0.25) / 0.25;
        r = 0; g = 255; b = Math.floor(255 * (1 - t));
    } else if (ratio < 0.75) {
        // Green to Yellow
        var t = (ratio - 0.5) / 0.25;
        r = Math.floor(255 * t); g = 255; b = 0;
    } else {
        // Yellow to Red
        var t = (ratio - 0.75) / 0.25;
        r = 255; g = Math.floor(255 * (1 - t)); b = 0;
    }
    return 'rgb(' + r + ',' + g + ',' + b + ')';
}

function drawToolpath(segments, bedX, bedY, nozzlePos) {
    var canvas = document.getElementById('thermal-canvas');
    var ctx = canvas.getContext('2d');
    var w = canvas.width;
    var h = canvas.height;

    // Margins for axes
    var margin = { top: 30, right: 20, bottom: 40, left: 50 };
    var plotW = w - margin.left - margin.right;
    var plotH = h - margin.top - margin.bottom;

    // Scale factors: bed coordinates to canvas pixels
    var scaleX = plotW / bedX;
    var scaleY = plotH / bedY;

    // Clear canvas
    ctx.fillStyle = '#0a0a1a';
    ctx.fillRect(0, 0, w, h);

    // Draw bed outline
    ctx.strokeStyle = '#333';
    ctx.lineWidth = 1;
    ctx.strokeRect(margin.left, margin.top, plotW, plotH);

    // Draw grid lines every 50mm
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 0.5;
    for (var gx = 50; gx < bedX; gx += 50) {
        var px = margin.left + gx * scaleX;
        ctx.beginPath();
        ctx.moveTo(px, margin.top);
        ctx.lineTo(px, margin.top + plotH);
        ctx.stroke();
    }
    for (var gy = 50; gy < bedY; gy += 50) {
        var py = margin.top + gy * scaleY;
        ctx.beginPath();
        ctx.moveTo(margin.left, py);
        ctx.lineTo(margin.left + plotW, py);
        ctx.stroke();
    }

    // Axis labels
    ctx.fillStyle = '#666';
    ctx.font = '10px Consolas, Monaco, monospace';
    ctx.textAlign = 'center';
    for (var lx = 0; lx <= bedX; lx += 50) {
        ctx.fillText(lx.toString(), margin.left + lx * scaleX, h - margin.bottom + 15);
    }
    ctx.textAlign = 'right';
    for (var ly = 0; ly <= bedY; ly += 50) {
        ctx.fillText(ly.toString(), margin.left - 5, margin.top + plotH - ly * scaleY + 4);
    }

    // Title
    ctx.fillStyle = '#ccc';
    ctx.font = '13px Consolas, Monaco, monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Thermal Map — Toolpath Temperature', w / 2, 18);

    // Draw toolpath segments colored by temperature
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';

    for (var i = 0; i < segments.length; i++) {
        var seg = segments[i];
        var sx = margin.left + seg.x1 * scaleX;
        var sy = margin.top + plotH - seg.y1 * scaleY;
        var ex = margin.left + seg.x2 * scaleX;
        var ey = margin.top + plotH - seg.y2 * scaleY;

        ctx.strokeStyle = tempToColor(seg.temp);
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(ex, ey);
        ctx.stroke();
    }

    // Draw current nozzle position
    if (nozzlePos) {
        var nx = margin.left + nozzlePos.x * scaleX;
        var ny = margin.top + plotH - nozzlePos.y * scaleY;
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(nx, ny, 4, 0, 2 * Math.PI);
        ctx.fill();
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 1;
        ctx.stroke();
    }

    // Draw temperature legend
    var legendX = w - margin.right - 30;
    var legendTop = margin.top + 10;
    var legendH = 150;
    var legendW = 15;

    for (var li = 0; li < legendH; li++) {
        var tempVal = 25 + (150 - 25) * (1 - li / legendH);
        ctx.fillStyle = tempToColor(tempVal);
        ctx.fillRect(legendX, legendTop + li, legendW, 2);
    }
    ctx.fillStyle = '#888';
    ctx.font = '9px Consolas, Monaco, monospace';
    ctx.textAlign = 'left';
    ctx.fillText('150C', legendX + legendW + 3, legendTop + 5);
    ctx.fillText('88C', legendX + legendW + 3, legendTop + legendH / 2);
    ctx.fillText('25C', legendX + legendW + 3, legendTop + legendH);

    // Border around legend
    ctx.strokeStyle = '#444';
    ctx.lineWidth = 1;
    ctx.strokeRect(legendX, legendTop, legendW, legendH);
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

function doVibAnalyze(btn) {
    btn.disabled = true;
    btn.textContent = 'Running...';
    sendAction('run_vibration');
    setTimeout(function() { btn.disabled = false; btn.textContent = 'Run Analysis'; }, 90000);
}

document.getElementById('btn-kill').addEventListener('click', killAll);
document.getElementById('btn-reset').addEventListener('click', resetAll);
// Plugin buttons use onclick handlers in HTML to avoid duplicate triggers
connect();
