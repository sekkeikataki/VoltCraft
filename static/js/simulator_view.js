class VoltCraftSimulatorView {
    /**
     * Pure SVG Dynamic Waveform Plotter.
     * Renders analog signals and digital logic state timelines without external libraries.
     */
    constructor(analogPlotId, digitalPlotId) {
        this.analogSvg = document.getElementById(analogPlotId);
        this.digitalSvg = document.getElementById(digitalPlotId);
        
        this.colors = ["#06b6d4", "#fbbf24", "#f43f5e", "#10b981", "#a855f7", "#3b82f6"];
        this.activeMode = "dc";
        this.simData = null;

        this.initEvents();
    }

    initEvents() {
        // Hover coordinate tracking on analog waveform plotter
        this.analogSvg.addEventListener("mousemove", (e) => {
            if (!this.simData) return;
            const rect = this.analogSvg.getBoundingClientRect();
            const mouseX = e.clientX - rect.left;
            
            // Map mouse X to simulation time
            const width = rect.width || 400;
            const height = rect.height || 200;
            
            let times = [];
            if (this.activeMode === "transient" && this.simData.times) {
                times = this.simData.times;
            } else if (this.activeMode === "mixed" && this.simData.analog_times) {
                times = this.simData.analog_times;
            } else {
                return;
            }

            const tStart = times[0];
            const tEnd = times[times.length - 1];
            const tSpan = tEnd - tStart;

            const ratio = (mouseX - 40) / (width - 60);
            if (ratio >= 0 && ratio <= 1.0) {
                const targetTime = tStart + ratio * tSpan;
                
                // Display telemetry coordinates
                const telEl = document.getElementById("telemetry-display");
                telEl.innerHTML = `<span>TIME: ${targetTime.toFixed(4)}s</span>`;
            }
        });
    }

    plot(mode, data) {
        this.activeMode = mode;
        this.simData = data;
        
        // Clear both visualizers
        this.analogSvg.innerHTML = "";
        this.digitalSvg.innerHTML = "";

        if (mode === "dc") {
            this.plotDC(data);
        } else if (mode === "transient") {
            this.plotTransient(data);
        } else if (mode === "ac") {
            this.plotAC(data);
        } else if (mode === "mixed") {
            this.plotMixed(data);
        }
    }

    probeLabel(key) {
        // Branch keys hold component currents; nets hold voltages
        return key.startsWith("branch_") ? `I(${key.slice(7)})` : key;
    }

    formatHz(f) {
        if (f >= 1e9) return `${(f / 1e9).toFixed(0)}GHz`;
        if (f >= 1e6) return `${(f / 1e6).toFixed(0)}MHz`;
        if (f >= 1e3) return `${(f / 1e3).toFixed(0)}kHz`;
        return `${f.toFixed(0)}Hz`;
    }

    drawLogFrequencyGrid(targetSvg, w, h, logMin, logMax, yMin, yMax, yUnit) {
        // Decade vertical gridlines with Hz labels, linear Y gridlines
        const grid = document.createElementNS("http://www.w3.org/2000/svg", "g");

        for (let d = Math.ceil(logMin); d <= Math.floor(logMax); d++) {
            const gx = 40 + ((d - logMin) / (logMax - logMin)) * (w - 60);
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", gx);
            line.setAttribute("y1", "15");
            line.setAttribute("x2", gx);
            line.setAttribute("y2", h - 30);
            line.setAttribute("stroke", "rgba(255,255,255,0.08)");
            line.setAttribute("stroke-width", "1");
            grid.appendChild(line);

            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.textContent = this.formatHz(Math.pow(10, d));
            label.setAttribute("x", gx - 12);
            label.setAttribute("y", h - 15);
            label.setAttribute("fill", "#64748b");
            label.setAttribute("font-size", "8px");
            grid.appendChild(label);
        }

        const yDivs = 4;
        for (let i = 0; i <= yDivs; i++) {
            const ratio = i / yDivs;
            const gy = h - 30 - ratio * (h - 45);
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", "40");
            line.setAttribute("y1", gy);
            line.setAttribute("x2", w - 20);
            line.setAttribute("y2", gy);
            line.setAttribute("stroke", "rgba(255,255,255,0.05)");
            line.setAttribute("stroke-width", "1");
            grid.appendChild(line);

            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.textContent = `${(yMin + ratio * (yMax - yMin)).toFixed(0)}${yUnit}`;
            label.setAttribute("x", "2");
            label.setAttribute("y", gy + 3);
            label.setAttribute("fill", "#64748b");
            label.setAttribute("font-size", "8px");
            grid.appendChild(label);
        }

        targetSvg.appendChild(grid);
    }

    plotAC(data) {
        // Bode plot: magnitude (dB) in the analog panel, phase (deg) below
        const freqs = data.freqs;
        const cmap = data.cmap;
        if (!freqs || freqs.length < 2) return;

        const logMin = Math.log10(freqs[0]);
        const logMax = Math.log10(freqs[freqs.length - 1]);

        const probedNets = Object.keys(cmap).filter(net =>
            net !== "n0" && appStore.probes.has(net));

        const drawCurves = (svg, series, yMin, yMax, yUnit) => {
            const w = svg.clientWidth || 380;
            const h = svg.clientHeight || 180;
            this.drawLogFrequencyGrid(svg, w, h, logMin, logMax, yMin, yMax, yUnit);

            probedNets.forEach((net, idx) => {
                const wave = series[cmap[net]];
                const color = this.colors[idx % this.colors.length];

                let points = "";
                for (let i = 0; i < freqs.length; i++) {
                    const px = 40 + ((Math.log10(freqs[i]) - logMin) / (logMax - logMin)) * (w - 60);
                    const clamped = Math.max(yMin, Math.min(yMax, wave[i]));
                    const py = h - 30 - ((clamped - yMin) / (yMax - yMin)) * (h - 45);
                    points += `${px.toFixed(1)},${py.toFixed(1)} `;
                }

                const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
                polyline.setAttribute("points", points.trim());
                polyline.setAttribute("fill", "none");
                polyline.setAttribute("stroke", color);
                polyline.setAttribute("stroke-width", "2");

                const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
                label.textContent = this.probeLabel(net);
                label.setAttribute("x", w - 50);
                label.setAttribute("y", 15 + idx * 12);
                label.setAttribute("fill", color);
                label.setAttribute("font-size", "9px");
                label.setAttribute("font-weight", "600");

                svg.appendChild(polyline);
                svg.appendChild(label);
            });
        };

        // Auto-scale magnitude to the probed curves (floor at -100 dB)
        let magMin = -20.0;
        let magMax = 10.0;
        probedNets.forEach(net => {
            data.magnitude_db[cmap[net]].forEach(v => {
                if (v > -100 && v < magMin) magMin = v;
                if (v > magMax) magMax = v;
            });
        });
        magMin = Math.max(-100, Math.floor(magMin / 10) * 10);
        magMax = Math.ceil(magMax / 10) * 10;

        drawCurves(this.analogSvg, data.magnitude_db, magMin, magMax, "dB");
        drawCurves(this.digitalSvg, data.phase_deg, -180, 180, "°");
    }

    plotDC(data) {
        // DC operating point displays flat voltages as horizontal bars
        const w = this.analogSvg.clientWidth || 380;
        const h = this.analogSvg.clientHeight || 200;

        // Draw grid lines
        this.drawGrid(this.analogSvg, w, h, 0, 1, 0, 10, "Time (DC)", "Voltage (V)");

        const x = data.x;
        const cmap = data.cmap;
        
        let idx = 0;
        Object.keys(cmap).forEach(net => {
            if (net.startsWith("branch_")) return; // Skip current branches
            if (net === "n0") return;

            const val = x[cmap[net]];
            const color = this.colors[idx % this.colors.length];
            
            // Map voltage to Y
            const yPos = h - 30 - ((val / 10.0) * (h - 50));
            
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", "40");
            line.setAttribute("y1", yPos);
            line.setAttribute("x2", w - 20);
            line.setAttribute("y2", yPos);
            line.setAttribute("stroke", color);
            line.setAttribute("stroke-width", "2.5");
            
            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.textContent = `${net}: ${val.toFixed(3)}V`;
            label.setAttribute("x", w - 80);
            label.setAttribute("y", yPos - 5);
            label.setAttribute("fill", color);
            label.setAttribute("font-size", "9px");
            label.setAttribute("font-weight", "600");
            
            this.analogSvg.appendChild(line);
            this.analogSvg.appendChild(label);
            idx++;
        });
    }

    plotTransient(data) {
        const w = this.analogSvg.clientWidth || 380;
        const h = this.analogSvg.clientHeight || 200;

        const times = data.times;
        const waveforms = data.waveforms;
        const cmap = data.cmap;

        if (!times || times.length === 0) return;

        const tMin = times[0];
        const tMax = times[times.length - 1];
        
        // Find min/max values across all waveforms to auto-scale plot
        let vMin = 99999.0;
        let vMax = -99999.0;
        
        Object.keys(cmap).forEach(net => {
            if (net === "n0") return;
            if (!appStore.probes.has(net)) return;  // Plot only checked probes

            const netIdx = cmap[net];
            waveforms[netIdx].forEach(val => {
                if (val < vMin) vMin = val;
                if (val > vMax) vMax = val;
            });
        });

        if (vMin === 99999.0) vMin = 0.0;
        if (vMax === -99999.0) vMax = 5.0;
        if (vMin === vMax) { vMin -= 1.0; vMax += 1.0; }

        this.drawGrid(this.analogSvg, w, h, tMin, tMax, vMin, vMax, "Time (s)", "Voltage (V)");

        // Plot each active probe curve (nets and branch currents)
        let colorIdx = 0;
        Object.keys(cmap).forEach(net => {
            if (net === "n0") return;
            if (!appStore.probes.has(net)) return;

            const netIdx = cmap[net];
            const netWave = waveforms[netIdx];
            const color = this.colors[colorIdx % this.colors.length];

            let points = "";
            for (let i = 0; i < times.length; i++) {
                const tx = times[i];
                const vy = netWave[i];
                
                // Map to SVG coordinates
                const px = 40 + ((tx - tMin) / (tMax - tMin)) * (w - 60);
                const py = h - 30 - ((vy - vMin) / (vMax - vMin)) * (h - 50);
                
                points += `${px.toFixed(1)},${py.toFixed(1)} `;
            }

            const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
            polyline.setAttribute("points", points.trim());
            polyline.setAttribute("fill", "none");
            polyline.setAttribute("stroke", color);
            polyline.setAttribute("stroke-width", "2");
            polyline.setAttribute("filter", "drop-shadow(0 0 3px " + color + "80)");
            
            // Add label
            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.textContent = this.probeLabel(net);
            label.setAttribute("x", w - 50);
            label.setAttribute("y", 15 + colorIdx * 12);
            label.setAttribute("fill", color);
            label.setAttribute("font-size", "9px");
            label.setAttribute("font-weight", "600");

            this.analogSvg.appendChild(polyline);
            this.analogSvg.appendChild(label);
            colorIdx++;
        });
    }

    plotMixed(data) {
        // Plots Analog Waveforms in Top and stacked Digital timelines in Bottom
        const w_an = this.analogSvg.clientWidth || 380;
        const h_an = this.analogSvg.clientHeight || 200;

        const a_times = data.analog_times;
        const a_waves = data.analog_waveforms;
        const a_map = data.analog_map;

        if (a_times && a_times.length > 0) {
            // Plot analog portion
            const tMin = a_times[0];
            const tMax = a_times[a_times.length - 1];
            
            let vMin = 0.0;
            let vMax = 5.0;
            
            this.drawGrid(this.analogSvg, w_an, h_an, tMin, tMax, vMin, vMax, "Time (s)", "Voltage (V)");

            let colorIdx = 0;
            Object.keys(a_map).forEach(net => {
                if (net === "n0") return;
                if (!appStore.probes.has(net)) return;

                const netIdx = a_map[net];
                const netWave = a_waves[netIdx];
                const color = this.colors[colorIdx % this.colors.length];

                let points = "";
                for (let i = 0; i < a_times.length; i++) {
                    const tx = a_times[i];
                    const vy = netWave[i];
                    
                    const px = 40 + ((tx - tMin) / (tMax - tMin)) * (w_an - 60);
                    const py = h_an - 30 - ((vy - vMin) / (vMax - vMin)) * (h_an - 50);
                    
                    points += `${px.toFixed(1)},${py.toFixed(1)} `;
                }

                const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
                polyline.setAttribute("points", points.trim());
                polyline.setAttribute("fill", "none");
                polyline.setAttribute("stroke", color);
                polyline.setAttribute("stroke-width", "2");

                this.analogSvg.appendChild(polyline);
                colorIdx++;
            });
        }

        // Plot digital timelines
        const w_di = this.digitalSvg.clientWidth || 380;
        const h_di = this.digitalSvg.clientHeight || 140;

        const d_waves = data.digital_waveforms;
        if (!d_waves || Object.keys(d_waves).length === 0) {
            return;
        }

        const d_nets = Object.keys(d_waves);
        const rowHeight = (h_di - 40) / d_nets.length;
        const tMin = a_times ? a_times[0] : 0.0;
        const tMax = a_times ? a_times[a_times.length - 1] : 0.001;

        // Draw horizontal logic separators
        d_nets.forEach((net, idx) => {
            const rowY = 15 + idx * rowHeight;
            const logList = d_waves[net]; // list of (t, val)

            // Draw line divider
            const divider = document.createElementNS("http://www.w3.org/2000/svg", "line");
            divider.setAttribute("x1", "40");
            divider.setAttribute("y1", rowY + rowHeight - 5);
            divider.setAttribute("x2", w_di - 20);
            divider.setAttribute("y2", rowY + rowHeight - 5);
            divider.setAttribute("stroke", "rgba(255, 255, 255, 0.05)");
            divider.setAttribute("stroke-width", "1");
            this.digitalSvg.appendChild(divider);

            // Draw label
            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.textContent = net;
            label.setAttribute("x", "5");
            label.setAttribute("y", rowY + rowHeight / 2);
            label.setAttribute("fill", "#fbbf24"); // Amber digital text
            label.setAttribute("font-size", "8px");
            label.setAttribute("font-weight", "600");
            this.digitalSvg.appendChild(label);

            // Reconstruct logic state steps
            if (logList.length === 0) return;

            let points = "";
            let currVal = logList[0][1];
            
            // Start point
            let px = 40;
            let py = rowY + (currVal === "1" ? 5 : rowHeight - 10);
            points += `${px},${py} `;

            for (let j = 0; j < logList.length; j++) {
                const tx = logList[j][0];
                const val = logList[j][1];

                const nextX = 40 + ((tx - tMin) / (tMax - tMin)) * (w_di - 60);
                
                // Draw horizontal step to transition time
                points += `${nextX.toFixed(1)},${py.toFixed(1)} `;
                
                // Draw vertical step
                py = rowY + (val === "1" ? 5 : rowHeight - 10);
                points += `${nextX.toFixed(1)},${py.toFixed(1)} `;
                
                currVal = val;
            }

            // Cap end point at tMax
            const endX = w_di - 20;
            points += `${endX},${py} `;

            const stepsPath = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
            stepsPath.setAttribute("points", points.trim());
            stepsPath.setAttribute("fill", "none");
            stepsPath.setAttribute("stroke", "#f59e0b");
            stepsPath.setAttribute("stroke-width", "2");
            stepsPath.setAttribute("filter", "drop-shadow(0 0 2px rgba(245,158,11,0.5))");
            this.digitalSvg.appendChild(stepsPath);
        });
    }

    drawGrid(targetSvg, w, h, xMin, xMax, yMin, yMax, xLabel, yLabel) {
        // Draw coordinate grids
        const gridGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
        
        // Draw main axes lines
        const axisX = document.createElementNS("http://www.w3.org/2000/svg", "line");
        axisX.setAttribute("x1", "40");
        axisX.setAttribute("y1", h - 30);
        axisX.setAttribute("x2", w - 20);
        axisX.setAttribute("y2", h - 30);
        axisX.setAttribute("stroke", "rgba(255,255,255,0.2)");
        axisX.setAttribute("stroke-width", "1");
        
        const axisY = document.createElementNS("http://www.w3.org/2000/svg", "line");
        axisY.setAttribute("x1", "40");
        axisY.setAttribute("y1", "20");
        axisY.setAttribute("x2", "40");
        axisY.setAttribute("y2", h - 30);
        axisY.setAttribute("stroke", "rgba(255,255,255,0.2)");
        axisY.setAttribute("stroke-width", "1");

        gridGroup.appendChild(axisX);
        gridGroup.appendChild(axisY);

        // Subdivisions grid lines
        const divs = 4;
        for (let i = 1; i <= divs; i++) {
            const ratio = i / divs;
            const gx = 40 + ratio * (w - 60);
            const gy = h - 30 - ratio * (h - 50);

            // X-Grid
            const xGrid = document.createElementNS("http://www.w3.org/2000/svg", "line");
            xGrid.setAttribute("x1", gx);
            xGrid.setAttribute("y1", "20");
            xGrid.setAttribute("x2", gx);
            xGrid.setAttribute("y2", h - 30);
            xGrid.setAttribute("stroke", "rgba(255,255,255,0.05)");
            xGrid.setAttribute("stroke-width", "1");

            // Y-Grid
            const yGrid = document.createElementNS("http://www.w3.org/2000/svg", "line");
            yGrid.setAttribute("x1", "40");
            yGrid.setAttribute("y1", gy);
            yGrid.setAttribute("x2", w - 20);
            yGrid.setAttribute("y2", gy);
            yGrid.setAttribute("stroke", "rgba(255,255,255,0.05)");
            yGrid.setAttribute("stroke-width", "1");

            gridGroup.appendChild(xGrid);
            gridGroup.appendChild(yGrid);

            // Labels for axes scale bounds
            const labelX = document.createElementNS("http://www.w3.org/2000/svg", "text");
            const valX = xMin + ratio * (xMax - xMin);
            labelX.textContent = valX.toFixed(3);
            labelX.setAttribute("x", gx - 15);
            labelX.setAttribute("y", h - 15);
            labelX.setAttribute("fill", "#64748b");
            labelX.setAttribute("font-size", "8px");
            gridGroup.appendChild(labelX);

            const labelY = document.createElementNS("http://www.w3.org/2000/svg", "text");
            const valY = yMin + ratio * (yMax - yMin);
            labelY.textContent = valY.toFixed(1);
            labelY.setAttribute("x", "10");
            labelY.setAttribute("y", gy + 3);
            labelY.setAttribute("fill", "#64748b");
            labelY.setAttribute("font-size", "8px");
            gridGroup.appendChild(labelY);
        }

        // Labels for min values
        const minLabelX = document.createElementNS("http://www.w3.org/2000/svg", "text");
        minLabelX.textContent = xMin.toFixed(3);
        minLabelX.setAttribute("x", "30");
        minLabelX.setAttribute("y", h - 15);
        minLabelX.setAttribute("fill", "#64748b");
        minLabelX.setAttribute("font-size", "8px");
        gridGroup.appendChild(minLabelX);

        const minLabelY = document.createElementNS("http://www.w3.org/2000/svg", "text");
        minLabelY.textContent = yMin.toFixed(1);
        minLabelY.setAttribute("x", "10");
        minLabelY.setAttribute("y", h - 27);
        minLabelY.setAttribute("fill", "#64748b");
        minLabelY.setAttribute("font-size", "8px");
        gridGroup.appendChild(minLabelY);

        targetSvg.appendChild(gridGroup);
    }
}

// Bind to App instance
window.addEventListener("DOMContentLoaded", () => {
    appStore.simulatorView = new VoltCraftSimulatorView("analog-plot", "digital-plot");
});
