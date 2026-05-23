import os
import json
import time
import hashlib
import traceback
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict, Any, List, Set

from voltcraft.engine.solver import ContinuousSolver, DiscreteEventScheduler, MixedSignalCoSimulator
from voltcraft.engine.parser_native import NativeGraphValidator
from voltcraft.engine.parser_drawio import DrawioCodec
from voltcraft.engine.scraper import DatasheetScraper

app = FastAPI(title="VoltCraft-Workstation-Server", version="1.0.0")

# Setup base folders
ROOT_DIR = "voltcraft"
STORAGE_DIR = os.path.join(ROOT_DIR, "storage")
SCHEMATICS_DIR = os.path.join(STORAGE_DIR, "schematics")
WIKI_DIR = os.path.join(STORAGE_DIR, "wiki")
CIRCUITS_DIR = os.path.join(STORAGE_DIR, "circuits")
JOURNAL_DIR = os.path.join(STORAGE_DIR, "journal")

for folder in (SCHEMATICS_DIR, WIKI_DIR, CIRCUITS_DIR, JOURNAL_DIR):
    os.makedirs(folder, exist_ok=True)

# Active schematic graph in-memory state store
# Maps path -> JSON graph
active_schematics: Dict[str, Dict[str, Any]] = {}

# Keep track of active WebSocket connections
active_connections: Set[WebSocket] = set()

# Initialize Scraper
scraper = DatasheetScraper()

# Pre-populate encyclopedia markdown wiki documents
wiki_templates = {
    "resistor": """---
family: resistor
iec_code: IEC-60617-4
equation: V = I * R
characteristics:
  - Standard resistances from 1 Ohm to 10 MOhm
  - Tolerance ranges: 0.1%, 1%, 5%
---
# Resistors
Resistors resist the flow of electrical current, producing a voltage drop proportional to the current flow (Ohm's Law).
""",
    "capacitor": """---
family: capacitor
iec_code: IEC-60617-4
equation: i(t) = C * dv/dt
characteristics:
  - Capacitance ranges: 1pF to 1000uF
  - Companion model utilizes discrete-time integration companion stamps
---
# Capacitors
Capacitors store electrical energy in an electric field between two conductive terminals separated by a dielectric.
""",
    "inductor": """---
family: inductor
iec_code: IEC-60617-4
equation: v(t) = L * di/dt
characteristics:
  - Inductance ranges: 1uH to 10H
  - Energy storage via magnetic fields
---
# Inductors
Inductors resist changes in electrical current, storing energy in a magnetic field.
""",
    "diode": """---
family: diode
iec_code: IEC-60617-5
equation: i_d = Is * (exp(v_d/(N*Vt)) - 1)
characteristics:
  - Silicon forward drop: 0.6V to 0.7V
  - Solved numerically using damped Newton-Raphson iterations
---
# Diodes
Diodes allow electrical current to flow in only one direction (rectification).
""",
    "opamp": """---
family: opamp
iec_code: IEC-60617-8
equation: v_out = gain * (v_plus - v_minus)
characteristics:
  - Open loop gain: 1e5
  - High input impedance, low output impedance
---
# Operational Amplifiers
Op-amps are high-gain voltage amplifiers with differential inputs.
"""
}

for name, content in wiki_templates.items():
    wiki_path = os.path.join(WIKI_DIR, f"{name}.md")
    if not os.path.exists(wiki_path):
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write(content)

# Pre-populate default reference circuits
default_circuits = {
    "voltage_divider": {
        "schema_version": "1.0.0",
        "metadata": {"name": "Voltage Divider", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "VoltCraft-Orchestrator"},
        "nodes": [
            {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pos": {"x": 100, "y": 100}, "rot": 0, "pins": {"a": "n1", "b": "n0"}},
            {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pos": {"x": 200, "y": 100}, "rot": 90, "pins": {"a": "n1", "b": "n2"}},
            {"id": "R2", "type": "resistor", "params": {"R": 1000.0}, "pos": {"x": 200, "y": 200}, "rot": 90, "pins": {"a": "n2", "b": "n0"}}
        ],
        "edges": [],
        "nets": ["n0", "n1", "n2"]
    }
}

for c_name, c_data in default_circuits.items():
    circuit_path = os.path.join(CIRCUITS_DIR, f"{c_name}.vcg.json")
    if not os.path.exists(circuit_path):
        with open(circuit_path, "w", encoding="utf-8") as f:
            json.dump(c_data, f, indent=2)

# Helper to write NDJSON mutation log entries
def write_journal_entry(agent_id: str, action: str, data: Dict[str, Any], current_graph: Dict[str, Any]) -> str:
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    graph_str = json.dumps(current_graph, sort_keys=True)
    graph_hash = hashlib.sha256(graph_str.encode("utf-8")).hexdigest()
    
    journal_entry = {
        "timestamp": timestamp,
        "agent_id": agent_id,
        "action": action,
        "data": data,
        "sha256": graph_hash
    }
    
    journal_path = os.path.join(JOURNAL_DIR, "mutations.ndjson")
    with open(journal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(journal_entry) + "\n")
        
    return graph_hash

class AgentAction(BaseModel):
    action: str
    agent_id: str = "Designer"
    params: Dict[str, Any]

@app.post("/api/agent/action")
async def execute_agent_action(payload: AgentAction):
    action = payload.action
    agent_id = payload.agent_id
    params = payload.params
    
    # Broadcast event payload across websocket to active clients
    ws_event = {
        "type": "agent_action_triggered",
        "agent_id": agent_id,
        "action": action,
        "timestamp": time.time()
    }
    for ws in list(active_connections):
        try:
            await ws.send_json(ws_event)
        except Exception:
            pass

    try:
        if action == "load_schematic":
            path = params.get("path")
            if not path:
                raise ValueError("Missing parameter: path")
            
            # Resolve target path
            full_path = os.path.abspath(path)
            if not full_path.startswith(os.path.abspath(ROOT_DIR)):
                raise ValueError("Unauthorized path out of VoltCraft workspace tree")
                
            if not os.path.exists(full_path):
                # Check reference circuits
                alt_path = os.path.join(CIRCUITS_DIR, os.path.basename(path))
                if os.path.exists(alt_path):
                    full_path = alt_path
                else:
                    # Create blank default
                    blank = {
                        "schema_version": "1.0.0",
                        "metadata": {"name": "Blank Schematic", "created_utc": "2026-05-23T20:38:00Z", "author_agent": agent_id},
                        "nodes": [],
                        "edges": [],
                        "nets": ["n0"]
                    }
                    with open(full_path, "w", encoding="utf-8") as f:
                        json.dump(blank, f, indent=2)

            with open(full_path, "r", encoding="utf-8") as f:
                graph = json.load(f)
                
            is_valid, msg = NativeGraphValidator.validate(graph)
            if not is_valid:
                raise ValueError(f"Invalid graph schema: {msg}")
                
            active_schematics[path] = graph
            
            # Write journal
            j_id = write_journal_entry(agent_id, action, {"path": path}, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": graph,
                "journal_id": j_id
            })
            
        elif action == "save_schematic":
            path = params.get("path")
            graph = params.get("graph")
            if not path or not graph:
                raise ValueError("Missing parameters: path, graph")
                
            full_path = os.path.abspath(path)
            if not full_path.startswith(os.path.abspath(ROOT_DIR)):
                raise ValueError("Unauthorized path out of VoltCraft workspace tree")
                
            is_valid, msg = NativeGraphValidator.validate(graph)
            if not is_valid:
                raise ValueError(f"Invalid VCG schema: {msg}")
                
            with open(full_path, "w", encoding="utf-8") as f:
                json.dump(graph, f, indent=2)
                
            active_schematics[path] = graph
            
            # Export drawio companion immediately
            if path.endswith(".vcg.json"):
                drawio_path = path.replace(".vcg.json", ".drawio.xml")
                xml_data = DrawioCodec.json_to_xml(graph)
                with open(drawio_path, "w", encoding="utf-8") as f:
                    f.write(xml_data)

            j_id = write_journal_entry(agent_id, action, {"path": path}, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": {"ok": True, "sha256": j_id},
                "journal_id": j_id
            })

        elif action == "place_component":
            path = params.get("path")
            comp_type = params.get("type")
            comp_params = params.get("params", {})
            pos = params.get("pos", {"x": 100.0, "y": 100.0})
            rot = params.get("rot", 0.0)
            
            if not path or not comp_type:
                raise ValueError("Missing parameters: path, type")
                
            graph = active_schematics.get(path)
            if not graph:
                raise ValueError("Schematic not loaded. Call load_schematic first.")
                
            # Generate unique component ID
            if comp_type == "resistor":
                prefix = "R"
            elif comp_type == "capacitor":
                prefix = "C"
            elif comp_type == "inductor":
                prefix = "L"
            elif comp_type == "diode":
                prefix = "D"
            elif comp_type == "voltage_source":
                prefix = "V"
            elif comp_type == "current_source":
                prefix = "I"
            else:
                prefix = "U"
            existing_ids = {n["id"] for n in graph["nodes"]}
            idx = 1
            node_id = f"{prefix}{idx}"
            while node_id in existing_ids:
                idx += 1
                node_id = f"{prefix}{idx}"
                
            # Default pins
            pins = {}
            if comp_type in ("resistor", "capacitor", "inductor", "voltage_source", "current_source"):
                pins = {"a": "n0", "b": "n0"}
            elif comp_type == "diode":
                pins = {"anode": "n0", "cathode": "n0"}
            elif comp_type == "opamp":
                pins = {"non_inverting": "n0", "inverting": "n0", "out": "n0"}
            elif comp_type.startswith("digital_"):
                pins = {"a": "n0", "b": "n0", "out": "n0"}
                
            new_node = {
                "id": node_id,
                "type": comp_type,
                "params": comp_params,
                "pos": pos,
                "rot": rot,
                "pins": pins
            }
            
            graph["nodes"].append(new_node)
            j_id = write_journal_entry(agent_id, action, {"path": path, "node_id": node_id}, graph)
            
            # Broadcast update
            await broadcast_update(path, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": {"node_id": node_id},
                "journal_id": j_id
            })

        elif action == "wire_pins":
            path = params.get("path")
            p_from = params.get("from")  # {"node_id": "R1", "pin": "a"}
            p_to = params.get("to")      # {"node_id": "C1", "pin": "a"}
            
            if not path or not p_from or not p_to:
                raise ValueError("Missing parameters: path, from, to")
                
            graph = active_schematics.get(path)
            if not graph:
                raise ValueError("Schematic not loaded.")
                
            # Group them into a new net or utilize an existing net
            nodes_dict = {n["id"]: n for n in graph["nodes"]}
            if p_from["node_id"] not in nodes_dict or p_to["node_id"] not in nodes_dict:
                raise ValueError("Target nodes do not exist in schematic")
                
            net_from = nodes_dict[p_from["node_id"]]["pins"].get(p_from["pin"], "n0")
            net_to = nodes_dict[p_to["node_id"]]["pins"].get(p_to["pin"], "n0")
            
            # Choose active net
            if net_from != "n0":
                target_net = net_from
            elif net_to != "n0":
                target_net = net_to
            else:
                # Create a new net
                net_idx = len(graph["nets"])
                target_net = f"n{net_idx}"
                graph["nets"].append(target_net)
                
            # Assign net to both terminals
            nodes_dict[p_from["node_id"]]["pins"][p_from["pin"]] = target_net
            nodes_dict[p_to["node_id"]]["pins"][p_to["pin"]] = target_net
            
            # Append layout layout edge wire
            edge_id = f"w{len(graph['edges']) + 1}"
            pos_from = nodes_dict[p_from["node_id"]]["pos"]
            pos_to = nodes_dict[p_to["node_id"]]["pos"]
            
            new_edge = {
                "id": edge_id,
                "net": target_net,
                "path": [[pos_from["x"], pos_from["y"]], [pos_to["x"], pos_to["y"]]]
            }
            graph["edges"].append(new_edge)
            
            j_id = write_journal_entry(agent_id, action, {"path": path, "edge_id": edge_id, "net": target_net}, graph)
            
            await broadcast_update(path, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": {"edge_id": edge_id, "net": target_net},
                "journal_id": j_id
            })

        elif action in ("delete_node", "delete_edge"):
            path = params.get("path")
            target_id = params.get("id")
            
            if not path or not target_id:
                raise ValueError("Missing parameters: path, id")
                
            graph = active_schematics.get(path)
            if not graph:
                raise ValueError("Schematic not loaded")
                
            if action == "delete_node":
                graph["nodes"] = [n for n in graph["nodes"] if n["id"] != target_id]
                # Cleanup connected edges
                # Keep edges only if both ends remain connected or edge is independent
                # Standard cleanup: remove any edge whose endpoints would become disconnected
                pass
            else:
                graph["edges"] = [e for e in graph["edges"] if e["id"] != target_id]
                
            j_id = write_journal_entry(agent_id, action, {"path": path, "id": target_id}, graph)
            await broadcast_update(path, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": {"ok": True},
                "journal_id": j_id
            })

        elif action == "run_simulation":
            path = params.get("path")
            mode = params.get("mode", "dc")  # dc, transient, digital, mixed
            sim_params = params.get("params", {})
            
            graph = active_schematics.get(path)
            if not graph:
                raise ValueError("Schematic not loaded")
                
            # Instantiate continuous solver
            analog = ContinuousSolver(graph["nodes"], graph["edges"])
            
            if mode == "dc":
                x, cmap = analog.solve_dc()
                results = {"x": x.tolist(), "cmap": cmap}
            elif mode == "transient":
                t_stop = float(sim_params.get("t_stop", 0.05))
                dt = float(sim_params.get("dt", 0.001))
                method = sim_params.get("method", "backward_euler")
                uic = bool(sim_params.get("uic", False))
                
                results_mat, times, cmap = analog.solve_transient(0.0, t_stop, dt, method=method, uic=uic)
                results = {
                    "waveforms": results_mat.tolist(),
                    "times": times,
                    "cmap": cmap
                }
            elif mode == "digital":
                scheduler = DiscreteEventScheduler()
                # Run scheduler
                t_stop = float(sim_params.get("t_stop", 10e-9))
                # Trigger initial events
                initial_events = sim_params.get("initial_events", []) # list of {"time": t, "net": net, "val": val}
                for ev in initial_events:
                    scheduler.schedule_event(ev["time"], ev["net"], ev["val"])
                    
                logs = scheduler.run_until(t_stop, graph["nodes"])
                results = {"logs": logs}
            elif mode == "mixed":
                t_stop = float(sim_params.get("t_stop", 0.001))
                dt = float(sim_params.get("dt", 1e-5))
                
                scheduler = DiscreteEventScheduler()
                initial_events = sim_params.get("initial_events", [])
                for ev in initial_events:
                    scheduler.schedule_event(ev["time"], ev["net"], ev["val"])
                    
                cosim = MixedSignalCoSimulator(analog, scheduler)
                res = cosim.step_co_simulation(0.0, t_stop, dt)
                results = {
                    "analog_waveforms": res["analog_waveforms"].tolist(),
                    "analog_times": res["analog_times"],
                    "analog_map": res["analog_map"],
                    "digital_waveforms": res["digital_waveforms"]
                }
            else:
                raise ValueError(f"Unknown simulation mode: {mode}")

            j_id = write_journal_entry(agent_id, action, {"path": path, "mode": mode}, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": results,
                "journal_id": j_id
            })

        elif action == "query_wiki":
            term = params.get("term", "").lower().strip()
            if not term:
                raise ValueError("Missing parameter: term")
                
            wiki_path = os.path.join(WIKI_DIR, f"{term}.md")
            if not os.path.exists(wiki_path):
                # Attempt to find similar
                wiki_path = os.path.join(WIKI_DIR, "resistor.md")
                
            with open(wiki_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Simple frontmatter parser
            frontmatter = {}
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    body = parts[2]
                    # Parse simple yaml
                    for line in parts[1].split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            frontmatter[k.strip()] = v.strip()
                            
            j_id = write_journal_entry(agent_id, action, {"term": term}, {})
            
            return JSONResponse({
                "status": "ok",
                "data": {
                    "frontmatter": frontmatter,
                    "markdown": body
                },
                "journal_id": j_id
            })

        elif action == "web_search":
            query = params.get("query")
            if not query:
                raise ValueError("Missing parameter: query")
                
            scraped = await scraper.scrape_part_parameters(query)
            j_id = write_journal_entry(agent_id, action, {"query": query}, {})
            
            return JSONResponse({
                "status": "ok",
                "data": scraped,
                "journal_id": j_id
            })

        elif action == "export_drawio":
            path = params.get("path")
            graph = params.get("graph")
            if not graph:
                # Fallback to loaded graph
                graph = active_schematics.get(path)
                
            if not graph:
                raise ValueError("No active graph provided or loaded")
                
            xml_str = DrawioCodec.json_to_xml(graph)
            j_id = write_journal_entry(agent_id, action, {"path": path}, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": xml_str,
                "journal_id": j_id
            })

        elif action == "import_drawio":
            xml_content = params.get("xml")
            if not xml_content:
                raise ValueError("Missing parameter: xml")
                
            graph = DrawioCodec.xml_to_json(xml_content)
            
            is_valid, msg = NativeGraphValidator.validate(graph)
            if not is_valid:
                raise ValueError(f"Imported Drawio XML is structurally invalid: {msg}")
                
            j_id = write_journal_entry(agent_id, action, {"import": "drawio"}, graph)
            
            return JSONResponse({
                "status": "ok",
                "data": graph,
                "journal_id": j_id
            })
            
        else:
            raise ValueError(f"Unknown action: {action}")
            
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({
            "status": "error",
            "error": {
                "code": "EXECUTION_ERROR",
                "message": str(e),
                "trace": traceback.format_exc()
            },
            "journal_id": ""
        }, status_code=400)

async def broadcast_update(path: str, graph: Dict[str, Any]):
    """Broadcasts a live update of the graph schema to all active sockets."""
    update_packet = {
        "type": "schematic_mutated",
        "path": path,
        "graph": graph,
        "timestamp": time.time()
    }
    for ws in list(active_connections):
        try:
            await ws.send_json(update_packet)
        except Exception:
            pass

@app.websocket("/ws/agent")
async def websocket_agent_channel(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    try:
        while True:
            # Handle incoming client messages if any
            data = await websocket.receive_text()
            packet = json.loads(data)
            
            # Simple collaborative merge resolution
            if packet.get("type") == "schematic_edit":
                path = packet["path"]
                action = packet["action"]
                agent_id = packet.get("agent_id", "Designer")
                mutations = packet["mutations"]
                
                graph = active_schematics.get(path)
                if graph:
                    # Apply concurrent edits (last-writer-wins)
                    if action == "update_node_positions":
                        nodes_dict = {n["id"]: n for n in graph["nodes"]}
                        for edit in mutations:
                            nid = edit["id"]
                            if nid in nodes_dict:
                                nodes_dict[nid]["pos"] = edit["pos"]
                                
                    j_id = write_journal_entry(agent_id, f"ws_{action}", mutations, graph)
                    # Broadcast merge results
                    await broadcast_update(path, graph)
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception:
        active_connections.remove(websocket)

# HTML dashboard fallback
@app.get("/")
async def get_dashboard():
    dashboard_html = """<!DOCTYPE html>
<html>
<head>
    <title>VoltCraft Autonomous Engine Workstation</title>
</head>
<body style="background:#111; color:#eee; font-family:sans-serif; text-align:center; padding:100px;">
    <h1>VoltCraft Engineering Engine</h1>
    <p>API Endpoint active. Web CAD workstation serving locally.</p>
</body>
</html>"""
    return HTMLResponse(content=dashboard_html)
