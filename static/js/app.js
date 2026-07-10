class VoltCraftApp {
    /**
     * Main Application State Store & Orchestrator.
     * Manages API fetching, WebSocket sync loops, and state change stacks.
     */
    constructor() {
        this.activeSchematicPath = "voltcraft/storage/schematics/calculator.vcg.json";
        this.graph = {
            schema_version: "1.0.0",
            metadata: { name: "Blank Schematic", created_utc: "2026-05-23T20:38:00Z", author_agent: "Designer" },
            nodes: [],
            edges: [],
            nets: ["n0"]
        };
        this.simResults = null;
        this.probes = new Set(["n1", "n2"]);  // Default diagnostic probes
        this.undoStack = [];
        this.redoStack = [];
        this.websocket = null;
        this.designer = null;       // Reference to designer.js SVG Canvas
        this.simulatorView = null;   // Reference to simulator_view.js wave plotter
        this.agentBridge = null;     // Reference to agent_bridge.js dispatcher
    }

    async init() {
        console.log("[VOLTCRAFT] Initializing state store and active interfaces...");
        
        // Start Reconnecting WebSocket loop
        this.setupWebSocket();

        // Bind global UI controls
        document.getElementById("btn-save").addEventListener("click", () => this.saveSchematic());
        document.getElementById("btn-simulate").addEventListener("click", () => this.runSimulation());
        const csvBtn = document.getElementById("btn-export-csv");
        if (csvBtn) {
            csvBtn.addEventListener("click", () => this.exportCsv());
        }
        const measureBtn = document.getElementById("btn-measure");
        if (measureBtn) {
            measureBtn.addEventListener("click", () => this.runMeasurements());
        }

        // Show the sweep parameter row only in dc_sweep mode
        document.getElementById("sim-mode").addEventListener("change", (e) => {
            const sweepRow = document.getElementById("sweep-controls");
            if (sweepRow) {
                sweepRow.style.display = e.target.value === "dc_sweep" ? "flex" : "none";
            }
        });

        this.bindKeyboardShortcuts();

        // Load Default reference circuit
        await this.loadSchematic(this.activeSchematicPath);
    }

    async loadSchematic(path) {
        this.activeSchematicPath = path;
        document.getElementById("active-file-label").textContent = path.split("/").pop();
        
        try {
            const res = await this.postAction("load_schematic", { path: path });
            if (res.status === "ok") {
                this.graph = res.data;
                this.undoStack = [];
                this.redoStack = [];
                
                this.logJournal("load_schematic", { path: path }, res.journal_id);
                this.refreshUI();
            } else {
                console.error("[VOLTCRAFT] Load failed: ", res.error.message);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Error loading schematic: ", err);
        }
    }

    async saveSchematic() {
        try {
            const res = await this.postAction("save_schematic", {
                path: this.activeSchematicPath,
                graph: this.graph
            });
            if (res.status === "ok") {
                this.logJournal("save_schematic", { path: this.activeSchematicPath }, res.journal_id);
                alert("Schematic saved successfully! draw.io companion XML exported.");
            } else {
                alert(`Error saving schematic: ${res.error.message}`);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Error saving schematic: ", err);
        }
    }

    buildSimParams(mode) {
        let params;
        if (mode === "ac") {
            params = { f_start: 1.0, f_stop: 1e6, points_per_decade: 20 };
        } else if (mode === "digital") {
            params = { t_stop: 0.01 };   // ten cycles of the default 1 kHz clock
        } else if (mode === "monte_carlo") {
            params = { runs: 200, distribution: "uniform" };
        } else if (mode === "dc_sweep") {
            params = {
                component: document.getElementById("sweep-component").value.trim(),
                param: document.getElementById("sweep-param").value.trim(),
                start: parseFloat(document.getElementById("sweep-start").value),
                stop: parseFloat(document.getElementById("sweep-stop").value),
                points: parseInt(document.getElementById("sweep-points").value, 10)
            };
        } else {
            params = {
                t_stop: mode === "dc" ? 0.0 : mode === "mixed" ? 0.001 : 0.05,
                dt: mode === "dc" ? 1.0 : mode === "mixed" ? 1e-5 : 0.001,
                method: "trapezoidal",
                uic: true
            };
            if (mode === "transient") {
                const adaptiveEl = document.getElementById("sim-adaptive");
                params.adaptive = !!(adaptiveEl && adaptiveEl.checked);
            }
        }

        // For mixed mode co-sim, add initial step events
        if (mode === "mixed") {
            params["initial_events"] = [
                { time: 0.0, net: "cmp_out", val: "0" },
                { time: 0.0, net: "inv_out", val: "1" }
            ];
        }
        return params;
    }

    async runSimulation() {
        const mode = document.getElementById("sim-mode").value;
        const btn = document.getElementById("btn-simulate");
        btn.disabled = true;
        btn.textContent = "SOLVING...";

        const params = this.buildSimParams(mode);

        try {
            const res = await this.postAction("run_simulation", {
                path: this.activeSchematicPath,
                mode: mode,
                params: params
            });

            if (res.status === "ok") {
                this.simResults = res.data;
                this.logJournal("run_simulation", { mode: mode }, res.journal_id);
                
                // Update Waveform Display Plotters
                if (this.simulatorView) {
                    this.simulatorView.plot(mode, res.data);
                }
                
                // Update Telemetry Parameters
                this.updateTelemetry(mode, res.data);
            } else {
                alert(`Simulation solver error: ${res.error.message}`);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Simulation execution error: ", err);
        } finally {
            btn.disabled = false;
            btn.textContent = "RUN SOLVER";
        }
    }

    async runMeasurements() {
        const nets = [...this.probes].filter(n => !n.startsWith("branch_"));
        if (nets.length === 0) {
            alert("Select at least one net-voltage probe before measuring.");
            return;
        }
        const btn = document.getElementById("btn-measure");
        btn.disabled = true;
        try {
            const res = await this.postAction("measure", {
                path: this.activeSchematicPath,
                nets: nets,
                params: { t_stop: 0.05, dt: 1e-4, method: "trapezoidal", uic: true }
            });
            if (res.status === "ok") {
                this.renderMeasurements(res.data.measurements);
                this.logJournal("measure", { nets: nets }, res.journal_id);
            } else {
                alert(`Measurement failed: ${res.error.message}`);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Measurement error: ", err);
        } finally {
            btn.disabled = false;
        }
    }

    renderMeasurements(measurements) {
        const container = document.getElementById("measurements-list");
        container.innerHTML = "";

        // Display order and human labels/units for the measurement keys
        const rows = [
            ["peak_to_peak", "Vpp", "V"], ["max", "Max", "V"], ["min", "Min", "V"],
            ["average", "Avg", "V"], ["rms", "RMS", "V"],
            ["rise_time", "Rise", "s"], ["fall_time", "Fall", "s"],
            ["overshoot_pct", "Overshoot", "%"], ["settling_time", "Settle", "s"],
            ["frequency", "Freq", "Hz"], ["period", "Period", "s"], ["duty_cycle", "Duty", ""]
        ];

        const fmt = (v, unit) => {
            if (unit === "%") return `${v.toFixed(2)}%`;
            if (unit === "") return `${(v * 100).toFixed(1)}%`;
            const abs = Math.abs(v);
            if (abs !== 0 && (abs < 1e-3 || abs >= 1e4)) return `${v.toExponential(3)} ${unit}`;
            return `${v.toFixed(4)} ${unit}`;
        };

        Object.keys(measurements).forEach(net => {
            const meas = measurements[net];
            const card = document.createElement("div");
            card.className = "rounded-lg bg-gray-950/60 border border-gray-800 p-2";
            const cells = rows
                .filter(([key]) => meas[key] !== undefined)
                .map(([key, label, unit]) =>
                    `<div class="flex justify-between gap-2"><span class="text-gray-500">${label}</span><span class="text-cyan-300 code-font">${fmt(meas[key], unit)}</span></div>`)
                .join("");
            card.innerHTML = `
                <div class="text-[10px] font-bold text-amber-300 code-font mb-1">${net}</div>
                <div class="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[9px]">${cells}</div>
            `;
            container.appendChild(card);
        });

        if (Object.keys(measurements).length === 0) {
            container.innerHTML = `<div class="text-[11px] text-gray-500 italic p-2 text-center">No probed nets to measure.</div>`;
        }
    }

    async exportCsv() {
        const mode = document.getElementById("sim-mode").value;
        if (mode === "digital" || mode === "mixed") {
            alert("CSV export supports DC, transient, sweep, AC, and Monte Carlo modes.");
            return;
        }
        try {
            const res = await this.postAction("export_csv", {
                path: this.activeSchematicPath,
                mode: mode,
                params: this.buildSimParams(mode)
            });
            if (res.status !== "ok") {
                alert(`CSV export failed: ${res.error.message}`);
                return;
            }
            const blob = new Blob([res.data], { type: "text/csv" });
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            const base = this.activeSchematicPath.split("/").pop().replace(".vcg.json", "");
            link.download = `${base}_${mode}.csv`;
            link.click();
            URL.revokeObjectURL(link.href);
            this.logJournal("export_csv", { mode: mode }, res.journal_id);
        } catch (err) {
            console.error("[VOLTCRAFT] CSV export error: ", err);
        }
    }

    bindKeyboardShortcuts() {
        document.addEventListener("keydown", (e) => {
            const tag = e.target.tagName;
            if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

            const key = e.key.toLowerCase();
            if ((e.ctrlKey || e.metaKey) && key === "z" && !e.shiftKey) {
                e.preventDefault();
                this.undo();
            } else if ((e.ctrlKey || e.metaKey) && (key === "y" || (key === "z" && e.shiftKey))) {
                e.preventDefault();
                this.redo();
            } else if ((e.key === "Delete" || e.key === "Backspace") && this.designer && this.designer.selectedNodeId) {
                e.preventDefault();
                this.deleteSelectedComponent();
            } else if (e.key === "Escape" && this.designer) {
                this.designer.placingType = null;
                this.designer.clearSelection();
                this.designer.render(this.graph);
            }
        });
    }

    async deleteSelectedComponent() {
        const nodeId = this.designer.selectedNodeId;
        try {
            const res = await this.postAction("delete_node", {
                path: this.activeSchematicPath,
                id: nodeId
            });
            if (res.status === "ok") {
                this.designer.clearSelection();
                this.logJournal("delete_node", { id: nodeId }, res.journal_id);
                // WebSocket broadcast refreshes the canvas
            } else {
                alert(`Delete failed: ${res.error.message}`);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Error deleting component: ", err);
        }
    }

    async updateComponentParams(nodeId, params) {
        try {
            const res = await this.postAction("update_params", {
                path: this.activeSchematicPath,
                id: nodeId,
                params: params
            });
            if (res.status === "ok") {
                this.logJournal("update_params", { id: nodeId }, res.journal_id);
                // WebSocket broadcast refreshes the canvas
            } else {
                alert(`Parameter update failed: ${res.error.message}`);
            }
        } catch (err) {
            console.error("[VOLTCRAFT] Error updating parameters: ", err);
        }
    }

    async postAction(action, params) {
        const response = await fetch("/api/agent/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action: action,
                agent_id: "Designer",
                params: params
            })
        });
        return await response.json();
    }

    setupWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/agent`;
        
        console.log(`[VOLTCRAFT] Connecting WS Broker: ${wsUrl}`);
        this.websocket = new WebSocket(wsUrl);

        this.websocket.onopen = () => {
            console.log("[VOLTCRAFT] WebSocket active.");
            document.getElementById("ws-status-dot").className = "w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse";
        };

        this.websocket.onmessage = (event) => {
            const packet = JSON.parse(event.data);

            if (packet.type === "schematic_mutated") {
                if (packet.path === this.activeSchematicPath) {
                    this.graph = packet.graph;
                    this.refreshUI();
                }
            } else if (packet.type === "agent_action_triggered") {
                this.logJournalWS(packet.agent_id, packet.action, packet.timestamp);
            }

            // Forward to the agent bridge overlay adapter. Delegating here
            // (instead of the bridge patching onmessage) keeps the bridge
            // attached across WebSocket reconnects.
            if (this.agentBridge) {
                this.agentBridge.onPacket(packet);
            }
        };

        this.websocket.onclose = () => {
            console.warn("[VOLTCRAFT] WebSocket disconnected. Retrying in 3 seconds...");
            document.getElementById("ws-status-dot").className = "w-2.5 h-2.5 rounded-full bg-rose-500 animate-ping";
            setTimeout(() => this.setupWebSocket(), 3000);
        };
    }

    pushState() {
        // deep copy graph schema to stack
        this.undoStack.push(JSON.parse(JSON.stringify(this.graph)));
        this.redoStack = [];  // Reset redo
    }

    undo() {
        if (this.undoStack.length > 0) {
            this.redoStack.push(JSON.parse(JSON.stringify(this.graph)));
            this.graph = this.undoStack.pop();
            this.refreshUI();
            this.syncWsEdit("replace_graph");
        }
    }

    redo() {
        if (this.redoStack.length > 0) {
            this.undoStack.push(JSON.parse(JSON.stringify(this.graph)));
            this.graph = this.redoStack.pop();
            this.refreshUI();
            this.syncWsEdit("replace_graph");
        }
    }

    syncWsEdit(actionName, mutations = []) {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            const packet = {
                type: "schematic_edit",
                path: this.activeSchematicPath,
                action: actionName,
                agent_id: "Designer",
                mutations: mutations
            };
            // Whole-graph sync: without it the server's broadcast echoes the
            // old state back and visually reverts local edits
            if (actionName === "replace_graph") {
                packet.graph = this.graph;
            }
            this.websocket.send(JSON.stringify(packet));
        }
    }

    refreshUI() {
        // Redraw SVG Symbol Canvas Editor
        if (this.designer) {
            this.designer.render(this.graph);
        }
        // Update diagnostics list of probes
        this.updateProbesList();
    }

    addProbeBadge(container, key, label) {
        const isChecked = this.probes.has(key);
        const badge = document.createElement("div");
        badge.className = "flex items-center justify-between px-3 py-1.5 rounded-lg bg-gray-950/60 border border-gray-800 hover:border-cyan-500/40 transition";
        badge.innerHTML = `
            <div class="flex items-center gap-2">
                <span class="w-2 h-2 rounded-full ${isChecked ? 'bg-cyan-400' : 'bg-gray-700'}"></span>
                <span class="text-xs font-semibold code-font text-gray-300">${label}</span>
            </div>
            <input type="checkbox" ${isChecked ? 'checked' : ''} class="w-3.5 h-3.5 rounded bg-gray-950 border-gray-800 text-cyan-500 focus:ring-cyan-500 focus:ring-offset-gray-900 cursor-pointer" data-net="${key}">
        `;

        badge.querySelector("input").addEventListener("change", (e) => {
            if (e.target.checked) {
                this.probes.add(key);
            } else {
                this.probes.delete(key);
            }
            this.refreshUI();
            // Retrigger simulation waveform plot updating
            if (this.simulatorView && this.simResults) {
                const mode = document.getElementById("sim-mode").value;
                this.simulatorView.plot(mode, this.simResults);
            }
        });

        container.appendChild(badge);
    }

    updateProbesList() {
        const container = document.getElementById("probes-list");
        container.innerHTML = "";

        if (this.graph.nets.length <= 1) {
            container.innerHTML = `<div class="text-[11px] text-gray-500 italic p-3 text-center">Place components and connect nodes to assign waveform diagnostic probes.</div>`;
            return;
        }

        // Net voltage probes
        this.graph.nets.forEach(net => {
            if (net === "n0") return; // Skip GND
            this.addProbeBadge(container, net, `Net: ${net}`);
        });

        // Branch current probes (voltage-defining components carry their
        // current in the MNA solution vector)
        this.graph.nodes
            .filter(n => ["voltage_source", "opamp", "digital_interface_out"].includes(n.type))
            .forEach(node => {
                this.addProbeBadge(container, `branch_${node.id}`, `I(${node.id}) [A]`);
            });
    }

    updateTelemetry(mode, data) {
        // Populate solver diagnostics from the engine's reported stats
        const condEl = document.getElementById("stat-matrix-cond");
        const resEl = document.getElementById("stat-newton-res");
        const stepsEl = document.getElementById("stat-timesteps");
        const dimEl = document.getElementById("stat-matrix-dim");

        const stats = data.stats;
        if (stats) {
            dimEl.textContent = `${stats.matrix_size} x ${stats.matrix_size}`;
            condEl.textContent = stats.condition_estimate !== undefined
                ? stats.condition_estimate.toExponential(2) : "—";
            resEl.textContent = stats.residual !== undefined
                ? stats.residual.toExponential(2) : "—";

            if (stats.analysis === "dc") {
                stepsEl.textContent = `${stats.newton_iterations} NR${stats.converged ? "" : " (DIVERGED)"}`;
            } else if (stats.analysis === "transient") {
                stepsEl.textContent = `${stats.timesteps} steps / ${stats.newton_iterations} NR`;
            } else if (stats.analysis === "ac") {
                stepsEl.textContent = `${stats.points} freq pts`;
            } else if (stats.analysis === "dc_sweep") {
                stepsEl.textContent = `${stats.points} sweep pts${stats.converged ? "" : " (DIVERGED)"}`;
            } else if (stats.analysis === "transient_adaptive") {
                stepsEl.textContent = `${stats.timesteps} adaptive (+${stats.rejected_steps} rej)`;
            } else if (stats.analysis === "monte_carlo") {
                stepsEl.textContent = `${stats.runs} MC runs / ${stats.toleranced_components} tol`;
            }
            return;
        }

        // Modes without engine stats (digital / mixed co-sim)
        const numNets = this.graph.nets.length - 1;
        const numVolts = this.graph.nodes.filter(n => n.type === "voltage_source" || n.type === "opamp").length;
        dimEl.textContent = `${numNets + numVolts} x ${numNets + numVolts}`;
        condEl.textContent = "—";
        resEl.textContent = "—";
        if (mode === "mixed" && data.analog_times) {
            stepsEl.textContent = `${data.analog_times.length} CO-Sim`;
        } else {
            stepsEl.textContent = "—";
        }
    }

    logJournal(action, data, sha256) {
        const log = document.getElementById("agent-journal-log");
        const dateStr = new Date().toISOString().split("T")[1].replace("Z", "");
        
        const entry = document.createElement("div");
        entry.innerHTML = `<span class="text-indigo-400 font-semibold">[${dateStr}]</span> <span class="text-amber-300 uppercase">${action}</span> <span class="text-gray-400">${JSON.stringify(data)}</span> <span class="text-emerald-400 font-semibold code-font text-[9px] ml-2">SHA: ${sha256.substring(0,8)}</span>`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    logJournalWS(agent, action, timestamp) {
        const log = document.getElementById("agent-journal-log");
        const timeStr = new Date(timestamp * 1000).toISOString().split("T")[1].replace("Z", "");
        
        const entry = document.createElement("div");
        entry.innerHTML = `<span class="text-rose-400 font-semibold">[${timeStr}]</span> <span class="text-cyan-400 font-semibold">${agent}</span> <span class="text-indigo-300">triggered websocket actions:</span> <span class="text-gray-300">${action}</span>`;
        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }
}

// Instantiate App globally
const appStore = new VoltCraftApp();
window.addEventListener("DOMContentLoaded", () => {
    appStore.init();
});
