class VoltCraftDesigner {
    /**
     * SVG CAD Schematic Canvas Editor.
     * Manages interactive drag-and-drop component placing, grid snapping, and orthogonal wire routing.
     */
    constructor(canvasId) {
        this.svg = document.getElementById(canvasId);
        this.activeGraph = null;
        this.selectedPin = null;
        this.draggedNode = null;
        this.dragOffset = { x: 0, y: 0 };
        this.scale = 1.0;
        this.gridSize = 20;

        // SVG symbol path descriptions
        this.symbols = {
            resistor: "M 0 20 L 20 20 L 25 10 L 35 30 L 45 10 L 55 30 L 65 10 L 75 30 L 80 20 L 100 20",
            capacitor: "M 0 20 L 45 20 M 45 5 L 45 35 M 55 5 L 55 35 M 55 20 L 100 20",
            inductor: "M 0 20 L 20 20 C 25 30, 35 30, 40 20 C 45 30, 55 30, 60 20 C 65 30, 75 30, 80 20 L 100 20",
            diode: "M 0 20 L 40 20 M 40 5 L 40 35 L 60 20 Z M 60 5 L 60 35 M 60 20 L 100 20",
            voltage_source: "M 0 20 L 30 20 M 70 20 L 100 20 M 50 5 C 41.7 5, 35 11.7, 35 20 C 35 28.3, 41.7 35, 50 35 C 58.3 35, 65 28.3, 65 20 C 65 11.7, 58.3 5, 50 5 M 40 20 L 48 20 M 44 16 L 44 24 M 52 20 L 60 20",
            current_source: "M 0 20 L 30 20 M 70 20 L 100 20 M 50 5 C 41.7 5, 35 11.7, 35 20 C 35 28.3, 41.7 35, 50 35 C 58.3 35, 65 28.3, 65 20 C 65 11.7, 58.3 5, 50 5 M 38 20 L 62 20 M 54 14 L 62 20 L 54 26",
            opamp: "M 0 10 L 30 10 M 0 30 L 30 30 M 30 0 L 30 40 L 70 20 Z M 70 20 L 100 20",
            analog_comparator: "M 0 20 L 30 20 M 30 0 L 30 40 L 70 20 Z M 70 20 L 100 20",
            digital_and: "M 0 10 L 30 10 M 0 30 L 30 30 M 30 5 L 50 5 A 15 15 0 0 1 50 35 L 30 35 Z M 65 20 L 100 20",
            digital_or: "M 0 10 L 25 10 M 0 30 L 25 30 M 20 5 C 32 5, 48 10, 65 20 C 48 30, 32 35, 20 35 C 26 26, 26 14, 20 5 Z M 65 20 L 100 20",
            digital_xor: "M 0 10 L 20 10 M 0 30 L 20 30 M 12 5 C 18 14, 18 26, 12 35 M 20 5 C 26 14, 26 26, 20 35 C 32 5, 48 10, 65 20 C 48 30, 32 35, 20 35 M 65 20 L 100 20",
            digital_interface_out: "M 0 20 L 30 20 M 70 20 L 100 20 M 30 5 L 70 5 L 70 35 L 30 35 Z"
        };

        this.initEvents();
    }

    initEvents() {
        // Register zoom actions
        document.getElementById("btn-zoom-in").addEventListener("click", () => this.zoom(0.1));
        document.getElementById("btn-zoom-out").addEventListener("click", () => this.zoom(-0.1));
        
        // Tool button placing mode selection
        document.querySelectorAll(".tool-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                document.querySelectorAll(".tool-btn").forEach(b => {
                    b.classList.remove("bg-cyan-950", "border-cyan-500/50", "text-cyan-400", "shadow-[0_0_10px_rgba(6,182,212,0.2)]");
                    b.classList.add("bg-gray-950/40", "border-gray-800", "text-gray-400");
                });
                btn.classList.remove("bg-gray-950/40", "border-gray-800", "text-gray-400");
                btn.classList.add("bg-cyan-950", "border-cyan-500/50", "text-cyan-400", "shadow-[0_0_10px_rgba(6,182,212,0.2)]");
                this.placingType = btn.getAttribute("data-type");
            });
        });

        document.getElementById("btn-clear").addEventListener("click", () => {
            appStore.pushState();
            appStore.graph.nodes = [];
            appStore.graph.edges = [];
            appStore.graph.nets = ["n0"];
            appStore.refreshUI();
            appStore.syncWsEdit("clear");
        });

        // Click on SVG canvas to place
        this.svg.addEventListener("click", (e) => {
            if (this.placingType && !e.target.closest("g") && e.target.tagName !== "path") {
                const rect = this.svg.getBoundingClientRect();
                const rawX = (e.clientX - rect.left) / this.scale;
                const rawY = (e.clientY - rect.top) / this.scale;
                
                // Snap coordinates
                const snapX = Math.round(rawX / this.gridSize) * this.gridSize;
                const snapY = Math.round(rawY / this.gridSize) * this.gridSize;

                appStore.pushState();
                this.placeComponent(this.placingType, snapX, snapY);
                
                // Reset tool selection
                this.placingType = null;
                document.querySelectorAll(".tool-btn").forEach(b => {
                    b.classList.remove("bg-cyan-950", "border-cyan-500/50", "text-cyan-400", "shadow-[0_0_10px_rgba(6,182,212,0.2)]");
                    b.classList.add("bg-gray-950/40", "border-gray-800", "text-gray-400");
                });
            }
        });

        // Drag elements
        this.svg.addEventListener("mousemove", (e) => {
            if (this.draggedNode) {
                const rect = this.svg.getBoundingClientRect();
                const rawX = (e.clientX - rect.left) / this.scale;
                const rawY = (e.clientY - rect.top) / this.scale;
                
                const snapX = Math.round((rawX - this.dragOffset.x) / this.gridSize) * this.gridSize;
                const snapY = Math.round((rawY - this.dragOffset.y) / this.gridSize) * this.gridSize;
                
                this.draggedNode.pos.x = snapX;
                this.draggedNode.pos.y = snapY;
                
                this.render(appStore.graph);
            }
        });

        this.svg.addEventListener("mouseup", () => {
            if (this.draggedNode) {
                appStore.syncWsEdit("update_node_positions");
                this.draggedNode = null;
            }
        });
    }

    zoom(delta) {
        this.scale = Math.max(0.5, min(2.5, this.scale + delta));
        this.render(appStore.graph);
    }

    async placeComponent(type, x, y) {
        // Send request to POST action API
        const res = await appStore.postAction("place_component", {
            path: appStore.activeSchematicPath,
            type: type,
            pos: { x: x, y: y },
            rot: 0.0,
            params: {}
        });

        if (res.status === "ok") {
            appStore.logJournal("place_component", { type: type, x: x, y: y }, res.journal_id);
            // WebSocket triggers update
        }
    }

    drawOrthogonalPath(x1, y1, x2, y2) {
        // Compute premium orthogonal paths (Manhattan-bend routes)
        const midX = (x1 + x2) / 2;
        return `M ${x1} ${y1} L ${midX} ${y1} L ${midX} ${y2} L ${x2} ${y2}`;
    }

    render(graph) {
        this.activeGraph = graph;
        this.svg.innerHTML = "";

        // 1. Draw snap grid dots
        const w = this.svg.clientWidth || 800;
        const h = this.svg.clientHeight || 500;
        for (let gx = 0; gx < w; gx += this.gridSize) {
            for (let gy = 0; gy < h; gy += this.gridSize) {
                const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                dot.setAttribute("cx", gx * this.scale);
                dot.setAttribute("cy", gy * this.scale);
                dot.setAttribute("r", "1");
                dot.setAttribute("fill", "rgba(255, 255, 255, 0.15)");
                this.svg.appendChild(dot);
            }
        }

        // 2. Render Wires (Edges)
        graph.edges.forEach(edge => {
            const pathData = edge.path;
            if (pathData.length < 2) return;
            
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            let d = `M ${pathData[0][0] * this.scale} ${pathData[0][1] * this.scale}`;
            for (let i = 1; i < pathData.length; i++) {
                // Draw orthogonal line segments
                d += ` L ${pathData[i][0] * this.scale} ${pathData[i][1] * this.scale}`;
            }
            
            // Check if this net has an active diagnostic probe check in global state
            const hasProbe = appStore.probes.has(edge.net);

            path.setAttribute("d", d);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke", hasProbe ? "#06b6d4" : "#10b981");
            path.setAttribute("stroke-width", hasProbe ? "3.5" : "2.5");
            path.setAttribute("filter", hasProbe ? "drop-shadow(0 0 4px rgba(6,182,212,0.6))" : "");
            
            // Tooltip or net label
            path.innerHTML = `<title>Net: ${edge.net}</title>`;
            this.svg.appendChild(path);
        });

        // 3. Render Components (Nodes)
        graph.nodes.forEach(node => {
            const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
            const px = node.pos.x * this.scale;
            const py = node.pos.y * this.scale;
            
            group.setAttribute("transform", `translate(${px}, ${py}) rotate(${node.rot}, ${40 * this.scale}, ${20 * this.scale})`);
            
            // Symbol path rendering
            const symbolPath = this.symbols[node.type] || this.symbols["resistor"];
            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            
            // Scale path coordinates
            const scaledPath = symbolPath.split(" ").map(token => {
                if (token.includes(",")) {
                    return token.split(",").map(val => {
                        const parsed = parseFloat(val);
                        return isNaN(parsed) ? val : parsed * this.scale;
                    }).join(",");
                }
                const floatVal = parseFloat(token);
                return isNaN(floatVal) ? token : floatVal * this.scale;
            }).join(" ");

            path.setAttribute("d", scaledPath);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke", "#f43f5e"); // Hot neon pink
            path.setAttribute("stroke-width", "2");
            path.setAttribute("filter", "drop-shadow(0 0 5px rgba(244,63,94,0.3))");
            group.appendChild(path);

            // Renders component label text
            const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
            text.textContent = node.id;
            text.setAttribute("x", 40 * this.scale);
            text.setAttribute("y", -5 * this.scale);
            text.setAttribute("text-anchor", "middle");
            text.setAttribute("fill", "#94a3b8");
            text.setAttribute("font-size", `${10 * this.scale}px`);
            text.setAttribute("font-weight", "600");
            group.appendChild(text);

            // Bind component dragging
            group.addEventListener("mousedown", (e) => {
                if (e.target.tagName !== "circle") {  // Skip pin clicks
                    appStore.pushState();
                    this.draggedNode = node;
                    const rect = this.svg.getBoundingClientRect();
                    const rawMouseX = (e.clientX - rect.left) / this.scale;
                    const rawMouseY = (e.clientY - rect.top) / this.scale;
                    this.dragOffset = {
                        x: rawMouseX - node.pos.x,
                        y: rawMouseY - node.pos.y
                    };
                    e.stopPropagation();
                }
            });

            // Double click component to rotate it 90 degrees
            group.addEventListener("dblclick", (e) => {
                appStore.pushState();
                node.rot = (node.rot + 90) % 360;
                this.render(graph);
                appStore.syncWsEdit("rotate_node");
                e.stopPropagation();
            });

            // 4. Render Terminal pins (Clickable circles)
            Object.keys(node.pins).forEach(pinName => {
                // Determine pin coordinates based on component type and name
                let pinX = 0;
                let pinY = 20;
                
                if (node.type === "opamp") {
                    if (pinName === "non_inverting") { pinX = 0; pinY = 10; }
                    else if (pinName === "inverting") { pinX = 0; pinY = 30; }
                    else if (pinName === "out") { pinX = 100; pinY = 20; }
                } else if (node.type.startsWith("digital_") && node.type !== "digital_interface_out") {
                    if (pinName === "a") { pinX = 0; pinY = 10; }
                    else if (pinName === "b") { pinX = 0; pinY = 30; }
                    else if (pinName === "out" || pinName === "q" || pinName === "q_bar") { pinX = 100; pinY = 20; }
                } else if (node.type === "analog_comparator") {
                    if (pinName === "analog_in") { pinX = 0; pinY = 20; }
                    else if (pinName === "digital_out") { pinX = 100; pinY = 20; }
                } else if (node.type === "digital_interface_out") {
                    if (pinName === "digital_in") { pinX = 0; pinY = 20; }
                    else if (pinName === "analog_out") { pinX = 100; pinY = 20; }
                } else {
                    if (pinName === "b" || pinName === "cathode") {
                        pinX = 100;
                    }
                }

                const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                circle.setAttribute("cx", pinX * this.scale);
                circle.setAttribute("cy", pinY * this.scale);
                circle.setAttribute("r", "5");
                circle.setAttribute("fill", "#f59e0b"); // Neon amber
                circle.setAttribute("stroke", "#d97706");
                circle.setAttribute("stroke-width", "1");
                circle.setAttribute("class", "cursor-pointer hover:scale-125 transition-transform duration-100");

                // Tooltip
                circle.innerHTML = `<title>${node.id}:${pinName} (Net: ${node.pins[pinName]})</title>`;

                // Connection wiring logic
                circle.addEventListener("click", (e) => {
                    e.stopPropagation();
                    if (!this.selectedPin) {
                        this.selectedPin = { node_id: node.id, pin: pinName };
                        circle.setAttribute("fill", "#06b6d4"); // cyan highlight
                        circle.setAttribute("r", "7");
                    } else {
                        const fromPin = this.selectedPin;
                        const toPin = { node_id: node.id, pin: pinName };
                        
                        if (fromPin.node_id !== toPin.node_id) {
                            appStore.pushState();
                            this.connectPins(fromPin, toPin);
                        }
                        
                        this.selectedPin = null;
                        this.render(graph);
                    }
                });

                group.appendChild(circle);
            });

            this.svg.appendChild(group);
        });
    }

    async connectPins(fromPin, toPin) {
        const res = await appStore.postAction("wire_pins", {
            path: appStore.activeSchematicPath,
            from: fromPin,
            to: toPin
        });

        if (res.status === "ok") {
            appStore.logJournal("wire_pins", { from: fromPin, to: toPin }, res.journal_id);
            // WebSocket automatically refreshes
        }
    }
}

// Bind to App instance
window.addEventListener("DOMContentLoaded", () => {
    appStore.designer = new VoltCraftDesigner("cad-canvas");
});

// Helper for min logic
function min(a, b) {
    return a < b ? a : b;
}
