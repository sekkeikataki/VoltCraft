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
        this._gridLayer = null;
        this._gridKey = null;

        // SVG symbol path descriptions
        this.symbols = {
            resistor: "M 0 20 L 30 20 L 30 10 L 70 10 L 70 30 L 30 30 L 30 20 M 70 20 L 100 20",
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
            digital_interface_out: "M 0 20 L 30 20 M 70 20 L 100 20 M 30 5 L 70 5 L 70 35 L 30 35 Z",
            nmos: "M 0 20 L 45 20 M 45 10 L 45 30 M 52 8 L 52 32 M 52 12 L 70 12 L 70 5 L 100 5 M 52 28 L 70 28 L 70 35 L 100 35 M 58 28 L 52 25 L 58 22",
            pmos: "M 0 20 L 40 20 M 43 20 A 3 3 0 1 1 49 20 A 3 3 0 1 1 43 20 M 52 10 L 52 30 M 58 8 L 58 32 M 58 12 L 72 12 L 72 5 L 100 5 M 58 28 L 72 28 L 72 35 L 100 35",
            bjt_npn: "M 0 20 L 45 20 M 45 8 L 45 32 M 45 15 L 70 6 L 70 5 L 100 5 M 45 25 L 70 34 L 70 35 L 100 35 M 62 33 L 70 34 L 64 27",
            bjt_pnp: "M 0 20 L 45 20 M 45 8 L 45 32 M 45 15 L 70 6 L 70 5 L 100 5 M 45 25 L 70 34 L 70 35 L 100 35 M 53 25 L 45 25 L 51 31",
            subcircuit: "M 0 20 L 25 20 M 75 20 L 100 20 M 25 5 L 75 5 L 75 35 L 25 35 Z M 33 13 L 42 22 M 42 13 L 33 22 M 55 25 L 67 25 M 55 29 L 67 29",
            vcvs: "M 0 10 L 30 10 M 0 30 L 30 30 M 50 5 L 65 20 L 50 35 L 35 20 Z M 46 16 L 54 16 M 50 12 L 50 20 M 46 26 L 54 26 M 65 20 L 100 10 M 65 20 L 100 30",
            vccs: "M 0 10 L 30 10 M 0 30 L 30 30 M 50 5 L 65 20 L 50 35 L 35 20 Z M 50 13 L 50 27 M 47 22 L 50 27 L 53 22 M 65 20 L 100 10 M 65 20 L 100 30",
            cccs: "M 0 20 L 35 20 M 50 5 L 65 20 L 50 35 L 35 20 Z M 50 13 L 50 27 M 47 22 L 50 27 L 53 22 M 65 20 L 100 20",
            ccvs: "M 0 20 L 35 20 M 50 5 L 65 20 L 50 35 L 35 20 Z M 46 16 L 54 16 M 50 12 L 50 20 M 46 26 L 54 26 M 65 20 L 100 20",
            zener: "M 0 20 L 42 20 M 42 8 L 42 32 L 62 20 Z M 57 8 L 62 8 L 62 32 L 67 32 M 62 20 L 100 20",
            led: "M 0 20 L 42 20 M 42 8 L 42 32 L 62 20 Z M 62 8 L 62 32 M 62 20 L 100 20 M 66 4 L 74 -2 M 70 -2 L 74 -2 L 74 2 M 72 10 L 80 4 M 76 4 L 80 4 L 80 8",
            potentiometer: "M 0 20 L 18 20 L 23 12 L 31 28 L 39 12 L 47 28 L 55 12 L 63 28 L 71 12 L 77 20 L 100 20 M 50 0 L 50 14 M 45 8 L 50 14 L 55 8",
            switch: "M 0 20 L 30 20 M 70 20 L 100 20 M 30 20 L 62 9 M 28 20 A 2 2 0 1 0 32 20 A 2 2 0 1 0 28 20 M 68 20 A 2 2 0 1 0 72 20 A 2 2 0 1 0 68 20 M 35 40 L 35 26 M 65 40 L 65 26",
            transformer: "M 0 8 L 18 8 M 0 32 L 18 32 M 100 8 L 82 8 M 100 32 L 82 32 M 18 4 A 4 6 0 0 1 18 16 A 4 6 0 0 1 18 28 A 4 6 0 0 1 18 40 M 82 4 A 4 6 0 0 0 82 16 A 4 6 0 0 0 82 28 A 4 6 0 0 0 82 40 M 47 2 L 47 38 M 53 2 L 53 38"
        };

        // Suggested editable parameters per component type for the inspector
        this.paramHints = {
            resistor: { R: 1000 },
            capacitor: { C: 1e-6 },
            inductor: { L: 1e-3 },
            diode: { Is: 1e-14, N: 1 },
            voltage_source: { V: 5, freq: 0, wave: "sine", phase: 0, offset: 0, duty: 0.5, ac_mag: 0 },
            current_source: { I: 0.001 },
            opamp: { gain: 1e5, Rin: 1e6, Rout: 50 },
            nmos: { K: 1e-3, Vth: 1, lambda: 0 },
            pmos: { K: 1e-3, Vth: 1, lambda: 0 },
            bjt_npn: { Is: 1e-15, beta_f: 100, beta_r: 1 },
            bjt_pnp: { Is: 1e-15, beta_f: 100, beta_r: 1 },
            analog_comparator: { threshold: 2.5 },
            digital_interface_out: { V: 5, delay: 0 },
            vcvs: { gain: 1 },
            vccs: { gm: 1e-3 },
            cccs: { gain: 1, control: "V1" },
            ccvs: { r: 1000, control: "V1" },
            zener: { Vz: 5.1, Is: 1e-14 },
            led: { Is: 1e-20, N: 2 },
            potentiometer: { R: 10000, wiper: 0.5 },
            switch: { threshold: 2.5, Ron: 1, Roff: 1e9 },
            transformer: { ratio: 2 }
        };
        this.selectedNodeId = null;

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
            appStore.syncWsEdit("replace_graph");
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

                const oldX = this.draggedNode.pos.x;
                const oldY = this.draggedNode.pos.y;
                if (snapX !== oldX || snapY !== oldY) {
                    // Wire endpoints anchored at the component's position
                    // follow it while dragging
                    appStore.graph.edges.forEach(edge => {
                        const pts = edge.path;
                        if (!pts || pts.length === 0) return;
                        [0, pts.length - 1].forEach(idx => {
                            if (pts[idx][0] === oldX && pts[idx][1] === oldY) {
                                pts[idx] = [snapX, snapY];
                            }
                        });
                    });

                    this.draggedNode.pos.x = snapX;
                    this.draggedNode.pos.y = snapY;
                    this.render(appStore.graph);
                }
            }
        });

        this.svg.addEventListener("mouseup", () => {
            if (this.draggedNode) {
                // Whole-graph sync so the server broadcast reflects the new
                // positions and rerouted wires instead of reverting them
                appStore.syncWsEdit("replace_graph");
                this.draggedNode = null;
            }
        });
    }

    zoom(delta) {
        this.scale = Math.max(0.5, Math.min(2.5, this.scale + delta));
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

    orthogonalRoute(points) {
        // Builds an SVG path that only travels along horizontal and vertical
        // segments (Manhattan routing). Two-point wires get a mid-column
        // Z-bend so they never render as a diagonal; multi-point paths
        // (e.g. imported from draw.io) are connected corner-to-corner with
        // an L-bend between each pair.
        const s = this.scale;
        if (points.length < 2) return "";

        let d = `M ${points[0][0] * s} ${points[0][1] * s}`;
        if (points.length === 2) {
            const [x1, y1] = points[0];
            const [x2, y2] = points[1];
            const midX = (x1 + x2) / 2;
            d += ` L ${midX * s} ${y1 * s} L ${midX * s} ${y2 * s} L ${x2 * s} ${y2 * s}`;
            return d;
        }
        for (let i = 1; i < points.length; i++) {
            const [px, py] = points[i - 1];
            const [cx, cy] = points[i];
            if (px !== cx && py !== cy) {
                // Insert an L-bend so the segment stays orthogonal
                d += ` L ${cx * s} ${py * s}`;
            }
            d += ` L ${cx * s} ${cy * s}`;
        }
        return d;
    }

    appendGridLayer(w, h) {
        // The dot lattice is ~1000 SVG nodes; rebuild it only when the
        // scale or canvas size changes, not on every render (renders fire
        // per mousemove while dragging components)
        const key = `${this.scale}|${w}|${h}|${this.gridSize}`;
        if (!this._gridLayer || this._gridKey !== key) {
            const layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
            for (let gx = 0; gx < w; gx += this.gridSize) {
                for (let gy = 0; gy < h; gy += this.gridSize) {
                    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
                    dot.setAttribute("cx", gx * this.scale);
                    dot.setAttribute("cy", gy * this.scale);
                    dot.setAttribute("r", "1");
                    dot.setAttribute("fill", "rgba(255, 255, 255, 0.15)");
                    layer.appendChild(dot);
                }
            }
            this._gridLayer = layer;
            this._gridKey = key;
        }
        this.svg.appendChild(this._gridLayer);
    }

    render(graph) {
        this.activeGraph = graph;
        if (this.selectedNodeId && !graph.nodes.some(n => n.id === this.selectedNodeId)) {
            this.clearSelection();  // Selected component was deleted or replaced
        }
        this.svg.innerHTML = "";

        // 1. Draw snap grid dots
        const w = this.svg.clientWidth || 800;
        const h = this.svg.clientHeight || 500;
        this.appendGridLayer(w, h);

        // 2. Render Wires (Edges)
        graph.edges.forEach(edge => {
            const pathData = edge.path;
            if (pathData.length < 2) return;

            const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            const d = this.orthogonalRoute(pathData);

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

            const isSelected = node.id === this.selectedNodeId;
            path.setAttribute("d", scaledPath);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke", isSelected ? "#22d3ee" : "#f43f5e"); // Cyan when selected
            path.setAttribute("stroke-width", isSelected ? "2.5" : "2");
            path.setAttribute("filter", isSelected
                ? "drop-shadow(0 0 6px rgba(34,211,238,0.5))"
                : "drop-shadow(0 0 5px rgba(244,63,94,0.3))");
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

            // Bind component dragging (mousedown also selects for the inspector)
            group.addEventListener("mousedown", (e) => {
                if (e.target.tagName !== "circle") {  // Skip pin clicks
                    this.selectNode(node);
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
                appStore.syncWsEdit("replace_graph");
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
                } else if (node.type === "nmos" || node.type === "pmos") {
                    if (pinName === "gate") { pinX = 0; pinY = 20; }
                    else if (pinName === "drain") { pinX = 100; pinY = 5; }
                    else if (pinName === "source") { pinX = 100; pinY = 35; }
                } else if (node.type === "bjt_npn" || node.type === "bjt_pnp") {
                    if (pinName === "base") { pinX = 0; pinY = 20; }
                    else if (pinName === "collector") { pinX = 100; pinY = 5; }
                    else if (pinName === "emitter") { pinX = 100; pinY = 35; }
                } else if (node.type === "vcvs" || node.type === "vccs") {
                    if (pinName === "cp") { pinX = 0; pinY = 10; }
                    else if (pinName === "cn") { pinX = 0; pinY = 30; }
                    else if (pinName === "p") { pinX = 100; pinY = 10; }
                    else if (pinName === "n") { pinX = 100; pinY = 30; }
                } else if (node.type === "cccs" || node.type === "ccvs") {
                    if (pinName === "p") { pinX = 100; pinY = 20; }
                    else if (pinName === "n") { pinX = 0; pinY = 20; }
                } else if (node.type === "potentiometer") {
                    if (pinName === "a") { pinX = 0; pinY = 20; }
                    else if (pinName === "b") { pinX = 100; pinY = 20; }
                    else if (pinName === "wiper") { pinX = 50; pinY = 0; }
                } else if (node.type === "switch") {
                    if (pinName === "p") { pinX = 0; pinY = 20; }
                    else if (pinName === "n") { pinX = 100; pinY = 20; }
                    else if (pinName === "cp") { pinX = 35; pinY = 40; }
                    else if (pinName === "cn") { pinX = 65; pinY = 40; }
                } else if (node.type === "transformer") {
                    if (pinName === "p1") { pinX = 0; pinY = 8; }
                    else if (pinName === "p2") { pinX = 0; pinY = 32; }
                    else if (pinName === "s1") { pinX = 100; pinY = 8; }
                    else if (pinName === "s2") { pinX = 100; pinY = 32; }
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

    selectNode(node) {
        this.selectedNodeId = node.id;
        this.renderInspector(node);
        this.render(appStore.graph);
    }

    clearSelection() {
        this.selectedNodeId = null;
        const empty = document.getElementById("inspector-empty");
        const fields = document.getElementById("inspector-fields");
        const apply = document.getElementById("inspector-apply");
        if (!empty) return;
        empty.classList.remove("hidden");
        fields.classList.add("hidden");
        apply.classList.add("hidden");
    }

    renderInspector(node) {
        const empty = document.getElementById("inspector-empty");
        const fields = document.getElementById("inspector-fields");
        const apply = document.getElementById("inspector-apply");
        if (!fields) return;

        empty.classList.add("hidden");
        fields.classList.remove("hidden");
        apply.classList.remove("hidden");
        fields.innerHTML = "";

        const header = document.createElement("div");
        header.className = "text-[10px] font-bold text-cyan-400 code-font mb-1";
        header.textContent = `${node.id} · ${node.type}`;
        fields.appendChild(header);

        // Merge suggested defaults with the component's current params so
        // every relevant knob is visible even before it has been set
        const merged = { ...(this.paramHints[node.type] || {}), ...node.params };
        Object.keys(merged).forEach(key => {
            const row = document.createElement("label");
            row.className = "flex items-center justify-between gap-2";
            row.innerHTML = `
                <span class="text-[10px] text-gray-400 code-font">${key}</span>
                <input type="text" value="${merged[key]}" data-param="${key}"
                       class="w-24 bg-gray-950 border border-gray-800 rounded px-1.5 py-0.5 text-[10px] text-gray-200 code-font focus:border-cyan-500/50 outline-none">
            `;
            fields.appendChild(row);
        });

        apply.onclick = async () => {
            const params = {};
            fields.querySelectorAll("input[data-param]").forEach(input => {
                const raw = input.value.trim();
                const isNumeric = /^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$/.test(raw);
                params[input.getAttribute("data-param")] = isNumeric ? parseFloat(raw) : raw;
            });
            await appStore.updateComponentParams(node.id, params);
        };
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
