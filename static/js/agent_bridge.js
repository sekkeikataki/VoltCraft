class VoltCraftAgentBridge {
    /**
     * Cooperative Agent WebSocket Bridge & Canvas Adapter.
     * Manages overlay lock-boxes, Designer & Verifier synchronization, and telemetry indicators.
     */
    constructor() {
        this.activeLocks = new Map();  // node_id -> agent_id lock tracking
        this.designerOverlayGroup = null;
        
        this.initBridge();
    }

    initBridge() {
        console.log("[VOLTCRAFT] Agent socket bridge adapter loaded.");
    }

    // Called by the app store for every WebSocket packet. The app delegates
    // instead of this bridge patching websocket.onmessage, which would be
    // lost each time the app rebuilds the socket on reconnect.
    onPacket(packet) {
        if (packet.type === "agent_action_triggered") {
            this.handleAgentTrigger(packet);
        } else if (packet.type === "schematic_mutated") {
            this.clearLocks();
        }
    }

    handleAgentTrigger(packet) {
        const agent = packet.agent_id;
        const action = packet.action;
        
        console.log(`[BRIDGE] Intercepted Agent event: ${agent} -> ${action}`);
        
        // Render dynamic glassmorphic overlay indicators on active node
        if (action === "place_component" || action === "wire_pins") {
            this.showAgentActivityOutline(agent, action);
        }
    }

    showAgentActivityOutline(agent, action) {
        const canvas = document.getElementById("cad-canvas");
        if (!canvas) return;

        // If exists, remove previous
        const existing = document.getElementById("agent-active-overlay");
        if (existing) existing.remove();

        const overlay = document.createElementNS("http://www.w3.org/2000/svg", "g");
        overlay.setAttribute("id", "agent-active-overlay");

        // Renders a neon cyan flash box on canvas to notify mechatronic updates
        const flashBox = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        flashBox.setAttribute("x", "40");
        flashBox.setAttribute("y", "40");
        flashBox.setAttribute("width", "300");
        flashBox.setAttribute("height", "200");
        flashBox.setAttribute("rx", "12");
        flashBox.setAttribute("fill", "rgba(6, 182, 212, 0.05)");
        flashBox.setAttribute("stroke", agent === "Designer" ? "#818cf8" : "#34d399");
        flashBox.setAttribute("stroke-width", "2");
        flashBox.setAttribute("stroke-dasharray", "5,5");
        flashBox.setAttribute("class", "animate-pulse");
        overlay.appendChild(flashBox);

        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.textContent = `⚡ AUTONOMOUS AGENT ACTIVE: ${agent.toUpperCase()}`;
        text.setAttribute("x", "50");
        text.setAttribute("y", "70");
        text.setAttribute("fill", agent === "Designer" ? "#a5b4fc" : "#6ee7b7");
        text.setAttribute("font-size", "10px");
        text.setAttribute("font-weight", "700");
        overlay.appendChild(text);

        canvas.appendChild(overlay);

        // Self-destruct overlay outline after 2 seconds
        setTimeout(() => {
            const el = document.getElementById("agent-active-overlay");
            if (el) el.remove();
        }, 2500);
    }

    clearLocks() {
        this.activeLocks.clear();
    }
}

// Bind to App instance
window.addEventListener("DOMContentLoaded", () => {
    appStore.agentBridge = new VoltCraftAgentBridge();
});
