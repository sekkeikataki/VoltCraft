import os
import re
import json
import time
import hashlib
import traceback
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Any, Awaitable, Callable, Dict, Set

from voltcraft.engine.solver import ContinuousSolver, DiscreteEventScheduler, MixedSignalCoSimulator
from voltcraft.engine.parser_native import NativeGraphValidator
from voltcraft.engine.parser_drawio import DrawioCodec
from voltcraft.engine.scraper import DatasheetScraper
from voltcraft.engine.subcircuit import flatten_subcircuits
from voltcraft.engine import measurements

app = FastAPI(title="VoltCraft-Workstation-Server", version="1.0.0")

# Mount static files directory
app.mount("/static", StaticFiles(directory="voltcraft/static"), name="static")

# Setup base folders
ROOT_DIR = "voltcraft"
WORKSPACE_ROOT = os.path.realpath(ROOT_DIR)
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
""",
    "mosfet": """---
family: mosfet
iec_code: IEC-60617-5
equation: Id = K/2 * (Vgs - Vth)^2 * (1 + lambda*Vds)
characteristics:
  - Level-1 square-law model (types nmos / pmos)
  - Params: K [A/V^2], Vth [V], lambda [1/V]
  - Regions: cutoff, triode, saturation; symmetric drain/source
---
# MOSFETs
Voltage-controlled transistors. The gate voltage relative to the source
sets the channel current; VoltCraft solves the square-law model with
Newton-Raphson companion linearization.
""",
    "bjt": """---
family: bjt
iec_code: IEC-60617-5
equation: Ic = Is*(exp(Vbe/Vt) - exp(Vbc/Vt)) (Ebers-Moll)
characteristics:
  - Ebers-Moll model (types bjt_npn / bjt_pnp)
  - Params: Is [A], beta_f, beta_r, Vt [V]
  - Active-region current gain Ic/Ib = beta_f
---
# Bipolar Junction Transistors
Current-controlled transistors modeled with the full Ebers-Moll
two-junction equations, valid in cutoff, active, and saturation regions.
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

def resolve_workspace_path(path: str) -> str:
    """
    Resolves a user-supplied path and ensures it stays inside the VoltCraft
    workspace tree. A plain prefix comparison would accept sibling directories
    such as 'voltcraft_evil', so compare directory components instead.
    """
    full_path = os.path.realpath(path)
    if os.path.commonpath([WORKSPACE_ROOT, full_path]) != WORKSPACE_ROOT:
        raise ValueError("Unauthorized path out of VoltCraft workspace tree")
    return full_path

def allocate_indexed_name(prefix: str, taken: Set[str], start: int = 1) -> str:
    """Returns the first '<prefix><idx>' name not present in taken."""
    idx = start
    while f"{prefix}{idx}" in taken:
        idx += 1
    return f"{prefix}{idx}"

def require_params(params: Dict[str, Any], *names: str) -> None:
    """Raises a ValueError naming every required parameter that is absent."""
    missing = [name for name in names if not params.get(name)]
    if missing:
        label = "parameter" if len(missing) == 1 else "parameters"
        raise ValueError(f"Missing {label}: {', '.join(missing)}")

def get_loaded_graph(path: str) -> Dict[str, Any]:
    """Returns the in-memory graph for path, or raises if it was never loaded."""
    graph = active_schematics.get(path)
    if not graph:
        raise ValueError("Schematic not loaded. Call load_schematic first.")
    return graph

def load_subcircuit_definition(ref: str) -> Dict[str, Any]:
    """Loads and validates a subcircuit definition from the workspace tree."""
    full_path = resolve_workspace_path(ref)
    if not os.path.exists(full_path):
        raise ValueError(f"Subcircuit definition not found: {ref}")
    with open(full_path, "r", encoding="utf-8") as f:
        sub = json.load(f)
    is_valid, msg = NativeGraphValidator.validate(sub)
    if not is_valid:
        raise ValueError(f"Subcircuit definition '{ref}' is invalid: {msg}")
    return sub

async def send_to_active_connections(packet: Dict[str, Any]) -> None:
    """Sends a JSON packet to every active WebSocket, dropping dead sockets."""
    for ws in list(active_connections):
        try:
            await ws.send_json(packet)
        except Exception:
            active_connections.discard(ws)

async def broadcast_update(path: str, graph: Dict[str, Any]) -> None:
    """Broadcasts a live update of the graph schema to all active sockets."""
    await send_to_active_connections({
        "type": "schematic_mutated",
        "path": path,
        "graph": graph,
        "timestamp": time.time()
    })

# ---------------------------------------------------------------------------
# Agent action handlers
#
# Each handler receives (agent_id, params) and returns the JSON-serializable
# response body. Raising ValueError yields a 400 EXECUTION_ERROR response.
# ---------------------------------------------------------------------------

ActionHandler = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
ACTION_HANDLERS: Dict[str, ActionHandler] = {}

def agent_action(name: str) -> Callable[[ActionHandler], ActionHandler]:
    """Registers a coroutine as the handler for the named agent action."""
    def register(handler: ActionHandler) -> ActionHandler:
        ACTION_HANDLERS[name] = handler
        return handler
    return register

@agent_action("load_schematic")
async def action_load_schematic(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path")
    path = params["path"]

    # Resolve target path
    full_path = resolve_workspace_path(path)

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
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                json.dump(blank, f, indent=2)

    with open(full_path, "r", encoding="utf-8") as f:
        graph = json.load(f)

    is_valid, msg = NativeGraphValidator.validate(graph)
    if not is_valid:
        raise ValueError(f"Invalid graph schema: {msg}")

    active_schematics[path] = graph

    j_id = write_journal_entry(agent_id, "load_schematic", {"path": path}, graph)
    return {"status": "ok", "data": graph, "journal_id": j_id}

@agent_action("save_schematic")
async def action_save_schematic(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path", "graph")
    path = params["path"]
    graph = params["graph"]

    full_path = resolve_workspace_path(path)

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

    j_id = write_journal_entry(agent_id, "save_schematic", {"path": path}, graph)
    return {"status": "ok", "data": {"ok": True, "sha256": j_id}, "journal_id": j_id}

# Component ID prefixes and default pin layouts by type
ID_PREFIXES = {
    "resistor": "R",
    "capacitor": "C",
    "inductor": "L",
    "diode": "D",
    "voltage_source": "V",
    "current_source": "I",
    "nmos": "M",
    "pmos": "M",
    "bjt_npn": "Q",
    "bjt_pnp": "Q",
    "subcircuit": "X",
    "vcvs": "E",
    "vccs": "G",
    "cccs": "F",
    "ccvs": "H"
}

TWO_TERMINAL_TYPES = ("resistor", "capacitor", "inductor", "voltage_source", "current_source")

DEFAULT_PIN_LAYOUTS = {
    "diode": ("anode", "cathode"),
    "opamp": ("non_inverting", "inverting", "out"),
    "analog_comparator": ("analog_in", "digital_out"),
    "digital_interface_out": ("digital_in", "analog_out"),
    "nmos": ("gate", "drain", "source"),
    "pmos": ("gate", "drain", "source"),
    "bjt_npn": ("base", "collector", "emitter"),
    "bjt_pnp": ("base", "collector", "emitter"),
    "vcvs": ("p", "n", "cp", "cn"),
    "vccs": ("p", "n", "cp", "cn"),
    "cccs": ("p", "n"),
    "ccvs": ("p", "n")
}

def default_pins_for(comp_type: str) -> Dict[str, str]:
    """Returns the unconnected (grounded) pin map for a component type."""
    if comp_type in TWO_TERMINAL_TYPES:
        pin_names = ("a", "b")
    elif comp_type in DEFAULT_PIN_LAYOUTS:
        pin_names = DEFAULT_PIN_LAYOUTS[comp_type]
    elif comp_type.startswith("digital_"):
        pin_names = ("a", "b", "out")
    else:
        pin_names = ()
    return {pin: "n0" for pin in pin_names}

@agent_action("place_component")
async def action_place_component(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path", "type")
    path = params["path"]
    comp_type = params["type"]
    comp_params = params.get("params", {})
    pos = params.get("pos", {"x": 100.0, "y": 100.0})
    rot = params.get("rot", 0.0)

    graph = get_loaded_graph(path)

    # Generate unique component ID
    prefix = ID_PREFIXES.get(comp_type, "U")
    existing_ids = {n["id"] for n in graph["nodes"]}
    node_id = allocate_indexed_name(prefix, existing_ids)

    new_node = {
        "id": node_id,
        "type": comp_type,
        "params": comp_params,
        "pos": pos,
        "rot": rot,
        "pins": default_pins_for(comp_type)
    }

    graph["nodes"].append(new_node)
    j_id = write_journal_entry(agent_id, "place_component", {"path": path, "node_id": node_id}, graph)

    await broadcast_update(path, graph)
    return {"status": "ok", "data": {"node_id": node_id}, "journal_id": j_id}

@agent_action("wire_pins")
async def action_wire_pins(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path", "from", "to")
    path = params["path"]
    p_from = params["from"]  # {"node_id": "R1", "pin": "a"}
    p_to = params["to"]      # {"node_id": "C1", "pin": "a"}

    graph = get_loaded_graph(path)

    nodes_dict = {n["id"]: n for n in graph["nodes"]}
    if p_from["node_id"] not in nodes_dict or p_to["node_id"] not in nodes_dict:
        raise ValueError("Target nodes do not exist in schematic")

    net_from = nodes_dict[p_from["node_id"]]["pins"].get(p_from["pin"], "n0")
    net_to = nodes_dict[p_to["node_id"]]["pins"].get(p_to["pin"], "n0")

    # Choose the target net ("n0" doubles as the unassigned state)
    if net_from != "n0" and net_to != "n0" and net_from != net_to:
        # Both pins already belong to distinct nets: wiring them makes the
        # two nets one electrical node, so merge net_to into net_from
        target_net = net_from
        for node in graph["nodes"]:
            for pin, net in node["pins"].items():
                if net == net_to:
                    node["pins"][pin] = target_net
        for edge in graph["edges"]:
            if edge["net"] == net_to:
                edge["net"] = target_net
        graph["nets"] = [net for net in graph["nets"] if net != net_to]
    elif net_from != "n0":
        target_net = net_from
    elif net_to != "n0":
        target_net = net_to
    else:
        # Create a new net; nets are not necessarily contiguous
        # (e.g. after deletions), so probe for a free name
        target_net = allocate_indexed_name("n", set(graph["nets"]), start=len(graph["nets"]))
        graph["nets"].append(target_net)

    # Assign net to both terminals
    nodes_dict[p_from["node_id"]]["pins"][p_from["pin"]] = target_net
    nodes_dict[p_to["node_id"]]["pins"][p_to["pin"]] = target_net

    # Append layout edge wire
    existing_edge_ids = {e["id"] for e in graph["edges"]}
    edge_id = allocate_indexed_name("w", existing_edge_ids, start=len(graph["edges"]) + 1)
    pos_from = nodes_dict[p_from["node_id"]]["pos"]
    pos_to = nodes_dict[p_to["node_id"]]["pos"]

    graph["edges"].append({
        "id": edge_id,
        "net": target_net,
        "path": [[pos_from["x"], pos_from["y"]], [pos_to["x"], pos_to["y"]]]
    })

    j_id = write_journal_entry(agent_id, "wire_pins", {"path": path, "edge_id": edge_id, "net": target_net}, graph)

    await broadcast_update(path, graph)
    return {"status": "ok", "data": {"edge_id": edge_id, "net": target_net}, "journal_id": j_id}

@agent_action("update_params")
async def action_update_params(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path", "id", "params")
    path = params["path"]
    target_id = params["id"]
    new_params = params["params"]

    if not isinstance(new_params, dict):
        raise ValueError("params must be a dictionary of component parameters")

    graph = get_loaded_graph(path)
    node = next((n for n in graph["nodes"] if n["id"] == target_id), None)
    if node is None:
        raise ValueError(f"Component '{target_id}' does not exist in schematic")

    node["params"].update(new_params)

    j_id = write_journal_entry(agent_id, "update_params", {"path": path, "id": target_id, "params": new_params}, graph)

    await broadcast_update(path, graph)
    return {"status": "ok", "data": {"id": target_id, "params": node["params"]}, "journal_id": j_id}

async def _delete_graph_item(agent_id: str, params: Dict[str, Any], action: str) -> Dict[str, Any]:
    require_params(params, "path", "id")
    path = params["path"]
    target_id = params["id"]

    graph = get_loaded_graph(path)

    if action == "delete_node":
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != target_id]
        # Cleanup: drop wires and nets that no longer connect to any pin
        live_nets = {net for n in graph["nodes"] for net in n.get("pins", {}).values()}
        live_nets.add("n0")
        graph["edges"] = [e for e in graph["edges"] if e["net"] in live_nets]
        graph["nets"] = [net for net in graph["nets"] if net in live_nets]
    else:
        graph["edges"] = [e for e in graph["edges"] if e["id"] != target_id]

    j_id = write_journal_entry(agent_id, action, {"path": path, "id": target_id}, graph)

    await broadcast_update(path, graph)
    return {"status": "ok", "data": {"ok": True}, "journal_id": j_id}

@agent_action("delete_node")
async def action_delete_node(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await _delete_graph_item(agent_id, params, "delete_node")

@agent_action("delete_edge")
async def action_delete_edge(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await _delete_graph_item(agent_id, params, "delete_edge")

def _compute_simulation(path: str, mode: str, sim_params: Dict[str, Any]) -> Dict[str, Any]:
    """Runs one analysis on the loaded schematic and returns its results."""
    graph = get_loaded_graph(path)

    # Expand subcircuit instances into a flat netlist for the solvers
    if any(n["type"] == "subcircuit" for n in graph["nodes"]):
        sim_graph = flatten_subcircuits(graph, load_subcircuit_definition)
    else:
        sim_graph = graph

    # Instantiate continuous solver
    analog = ContinuousSolver(sim_graph["nodes"], sim_graph["edges"])

    if mode == "dc":
        x, cmap = analog.solve_dc()
        results = {
            "x": x.tolist(),
            "cmap": cmap,
            "operating_point": analog.dc_operating_report(x),
            "stats": analog.last_solve_stats
        }
    elif mode == "transient":
        t_stop = float(sim_params.get("t_stop", 0.05))
        dt = float(sim_params.get("dt", 0.001))
        method = sim_params.get("method", "backward_euler")
        uic = bool(sim_params.get("uic", False))

        if bool(sim_params.get("adaptive", False)):
            lte_tol = float(sim_params.get("lte_tol", 1e-4))
            results_mat, times, cmap = analog.solve_transient_adaptive(
                0.0, t_stop, dt_init=dt, lte_tol=lte_tol, method=method, uic=uic)
        else:
            results_mat, times, cmap = analog.solve_transient(0.0, t_stop, dt, method=method, uic=uic)
        results = {
            "waveforms": results_mat.tolist(),
            "times": times,
            "cmap": cmap,
            "stats": analog.last_solve_stats
        }
    elif mode == "dc_sweep":
        component = sim_params.get("component")
        param = sim_params.get("param")
        if not component or not param:
            raise ValueError("Missing parameters: component, param")
        start = float(sim_params.get("start", 0.0))
        stop = float(sim_params.get("stop", 10.0))
        points = int(sim_params.get("points", 25))

        values, results_mat, cmap = analog.solve_dc_sweep(component, param, start, stop, points)
        results = {
            "values": values,
            "waveforms": results_mat.tolist(),
            "cmap": cmap,
            "stats": analog.last_solve_stats
        }
    elif mode == "monte_carlo":
        runs = int(sim_params.get("runs", 100))
        seed = sim_params.get("seed")
        distribution = sim_params.get("distribution", "uniform")

        mc, cmap = analog.solve_monte_carlo(runs=runs, seed=seed, distribution=distribution)
        results = {
            "mean": mc["mean"].tolist(),
            "std": mc["std"].tolist(),
            "min": mc["min"].tolist(),
            "max": mc["max"].tolist(),
            "samples": mc["samples"].tolist(),
            "cmap": cmap,
            "stats": analog.last_solve_stats
        }
    elif mode == "ac":
        f_start = float(sim_params.get("f_start", 1.0))
        f_stop = float(sim_params.get("f_stop", 1e6))
        points_per_decade = int(sim_params.get("points_per_decade", 20))

        freqs, magnitude_db, phase_deg, cmap = analog.solve_ac(f_start, f_stop, points_per_decade)
        results = {
            "freqs": freqs,
            "magnitude_db": magnitude_db.tolist(),
            "phase_deg": phase_deg.tolist(),
            "cmap": cmap,
            "stats": analog.last_solve_stats
        }
    elif mode == "digital":
        scheduler = DiscreteEventScheduler()
        # Run scheduler
        t_stop = float(sim_params.get("t_stop", 10e-9))
        # Trigger initial events
        initial_events = sim_params.get("initial_events", [])  # list of {"time": t, "net": net, "val": val}
        for ev in initial_events:
            scheduler.schedule_event(ev["time"], ev["net"], ev["val"])

        logs = scheduler.run_until(t_stop, sim_graph["nodes"])
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

    return results

@agent_action("run_simulation")
async def action_run_simulation(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path")
    path = params["path"]
    mode = params.get("mode", "dc")  # dc, transient, dc_sweep, monte_carlo, ac, digital, mixed
    sim_params = params.get("params", {})

    results = _compute_simulation(path, mode, sim_params)

    j_id = write_journal_entry(agent_id, "run_simulation", {"path": path, "mode": mode}, get_loaded_graph(path))
    return {"status": "ok", "data": results, "journal_id": j_id}

def _fmt(value: Any) -> str:
    return f"{value:.10g}" if isinstance(value, float) else str(value)

def results_to_csv(mode: str, results: Dict[str, Any]) -> str:
    """Serializes analysis results as CSV with one column per net/branch."""
    cmap = results.get("cmap")
    if not cmap:
        raise ValueError(f"CSV export is not supported for mode: {mode}")
    keys = sorted(cmap, key=cmap.get)

    lines = []
    if mode in ("transient", "dc_sweep"):
        stats = results.get("stats", {})
        if mode == "transient":
            x_label, xs = "time_s", results["times"]
        else:
            x_label, xs = f"{stats.get('component')}.{stats.get('param')}", results["values"]
        waveforms = results["waveforms"]
        lines.append(",".join([x_label] + keys))
        for k, x_val in enumerate(xs):
            lines.append(",".join([_fmt(x_val)] + [_fmt(waveforms[cmap[key]][k]) for key in keys]))
    elif mode == "ac":
        header = ["freq_hz"] + [f"mag_db({k})" for k in keys] + [f"phase_deg({k})" for k in keys]
        lines.append(",".join(header))
        for k, freq in enumerate(results["freqs"]):
            row = [_fmt(freq)]
            row += [_fmt(results["magnitude_db"][cmap[key]][k]) for key in keys]
            row += [_fmt(results["phase_deg"][cmap[key]][k]) for key in keys]
            lines.append(",".join(row))
    elif mode == "dc":
        lines.append(",".join(keys))
        lines.append(",".join(_fmt(results["x"][cmap[key]]) for key in keys))
    elif mode == "monte_carlo":
        lines.append(",".join(["metric"] + keys))
        for metric in ("mean", "std", "min", "max"):
            lines.append(",".join([metric] + [_fmt(results[metric][cmap[key]]) for key in keys]))
    else:
        raise ValueError(f"CSV export is not supported for mode: {mode}")

    return "\n".join(lines) + "\n"

@agent_action("export_csv")
async def action_export_csv(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "path")
    path = params["path"]
    mode = params.get("mode", "dc")
    sim_params = params.get("params", {})

    results = _compute_simulation(path, mode, sim_params)
    csv_text = results_to_csv(mode, results)

    j_id = write_journal_entry(agent_id, "export_csv", {"path": path, "mode": mode}, get_loaded_graph(path))
    return {"status": "ok", "data": csv_text, "journal_id": j_id}

@agent_action("measure")
async def action_measure(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Runs a transient analysis and reports scope measurements per net."""
    require_params(params, "path")
    path = params["path"]
    sim_params = params.get("params", {})

    results = _compute_simulation(path, "transient", sim_params)
    cmap = results["cmap"]
    times = results["times"]
    waveforms = results["waveforms"]

    # Measure the requested nets, or every non-ground net by default
    requested = params.get("nets")
    if requested:
        targets = [n for n in requested if n in cmap]
        missing = [n for n in requested if n not in cmap]
        if missing:
            raise ValueError(f"Unknown net(s) for measurement: {', '.join(missing)}")
    else:
        targets = [n for n in cmap if n != "n0"]

    measured = {net: measurements.measure_all(times, waveforms[cmap[net]]) for net in targets}

    j_id = write_journal_entry(agent_id, "measure", {"path": path, "nets": targets}, get_loaded_graph(path))
    return {
        "status": "ok",
        "data": {"measurements": measured, "stats": results.get("stats")},
        "journal_id": j_id
    }

@agent_action("query_wiki")
async def action_query_wiki(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    term = params.get("term", "").lower().strip()
    if not term:
        raise ValueError("Missing parameter: term")
    if not re.fullmatch(r"[a-z0-9_-]+", term):
        raise ValueError(f"Invalid wiki term: {term!r}")

    wiki_path = os.path.join(WIKI_DIR, f"{term}.md")
    if not os.path.exists(wiki_path):
        available = sorted(p[:-3] for p in os.listdir(WIKI_DIR) if p.endswith(".md"))
        raise ValueError(f"No wiki entry for '{term}'. Available: {', '.join(available)}")

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

    j_id = write_journal_entry(agent_id, "query_wiki", {"term": term}, {})
    return {"status": "ok", "data": {"frontmatter": frontmatter, "markdown": body}, "journal_id": j_id}

@agent_action("web_search")
async def action_web_search(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "query")
    query = params["query"]

    scraped = await scraper.scrape_part_parameters(query)
    j_id = write_journal_entry(agent_id, "web_search", {"query": query}, {})
    return {"status": "ok", "data": scraped, "journal_id": j_id}

@agent_action("export_drawio")
async def action_export_drawio(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    path = params.get("path")
    graph = params.get("graph")
    if not graph:
        # Fallback to loaded graph
        graph = active_schematics.get(path)

    if not graph:
        raise ValueError("No active graph provided or loaded")

    xml_str = DrawioCodec.json_to_xml(graph)
    j_id = write_journal_entry(agent_id, "export_drawio", {"path": path}, graph)
    return {"status": "ok", "data": xml_str, "journal_id": j_id}

@agent_action("import_drawio")
async def action_import_drawio(agent_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    require_params(params, "xml")
    xml_content = params["xml"]

    graph = DrawioCodec.xml_to_json(xml_content)

    is_valid, msg = NativeGraphValidator.validate(graph)
    if not is_valid:
        raise ValueError(f"Imported Drawio XML is structurally invalid: {msg}")

    j_id = write_journal_entry(agent_id, "import_drawio", {"import": "drawio"}, graph)
    return {"status": "ok", "data": graph, "journal_id": j_id}

class AgentAction(BaseModel):
    action: str
    agent_id: str = "Designer"
    params: Dict[str, Any]

@app.post("/api/agent/action")
async def execute_agent_action(payload: AgentAction):
    # Broadcast event payload across websocket to active clients
    await send_to_active_connections({
        "type": "agent_action_triggered",
        "agent_id": payload.agent_id,
        "action": payload.action,
        "timestamp": time.time()
    })

    try:
        handler = ACTION_HANDLERS.get(payload.action)
        if handler is None:
            raise ValueError(f"Unknown action: {payload.action}")
        return JSONResponse(await handler(payload.agent_id, payload.params))
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
                                if "pos" in edit:
                                    nodes_dict[nid]["pos"] = edit["pos"]
                                if "rot" in edit:
                                    nodes_dict[nid]["rot"] = edit["rot"]
                    elif action == "replace_graph":
                        # Whole-graph sync used by client-side edits (drag,
                        # rotate, undo/redo, clear); invalid payloads are
                        # dropped and the authoritative state is re-broadcast
                        new_graph = packet.get("graph")
                        is_valid, _msg = NativeGraphValidator.validate(new_graph)
                        if is_valid:
                            active_schematics[path] = new_graph
                            graph = new_graph

                    write_journal_entry(agent_id, f"ws_{action}", mutations, graph)
                    # Broadcast merge results
                    await broadcast_update(path, graph)
    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        active_connections.discard(websocket)

# HTML dashboard fallback
@app.get("/")
async def get_dashboard():
    template_path = os.path.join(ROOT_DIR, "templates", "index.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)

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
