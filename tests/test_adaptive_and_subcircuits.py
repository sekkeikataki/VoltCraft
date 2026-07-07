"""Tests for adaptive-timestep transient analysis and subcircuit flattening."""
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, SCHEMATICS_DIR, active_schematics
from voltcraft.engine.solver import ContinuousSolver
from voltcraft.engine.subcircuit import flatten_subcircuits, SubcircuitError
from voltcraft.engine.parser_native import NativeGraphValidator

client = TestClient(app)

SUB_FILES = ["test_subdef.vcg.json", "test_subparent.vcg.json",
             "test_subdef.drawio.xml", "test_subparent.drawio.xml"]


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in SUB_FILES:
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Adaptive transient
# ---------------------------------------------------------------------------

def rc_nodes(tau_r=1000.0, tau_c=1e-5):
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": tau_r}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "C1", "type": "capacitor", "params": {"C": tau_c}, "pins": {"a": "n2", "b": "n0"}}
    ]


def test_adaptive_transient_tracks_analytic_rc():
    # tau = 10 ms; every accepted point must match 5*(1 - exp(-t/tau))
    solver = ContinuousSolver(rc_nodes(), [])
    results, times, cmap = solver.solve_transient_adaptive(
        0.0, 0.05, dt_init=1e-5, lte_tol=1e-4, method="trapezoidal", uic=True)

    idx = cmap["n2"]
    tau = 1000.0 * 1e-5
    worst = 0.0
    for k, t in enumerate(times):
        expected = 5.0 * (1.0 - math.exp(-t / tau))
        worst = max(worst, abs(results[idx][k] - expected))
    assert worst < 0.01, f"worst-case error {worst}"

    stats = solver.last_solve_stats
    assert stats["analysis"] == "transient_adaptive"
    assert stats["timesteps"] == len(times) - 1
    assert results.shape[1] == len(times)
    # The controller must actually adapt: step sizes span a real range
    assert stats["dt_max_used"] >= 4.0 * stats["dt_min_used"]
    # And finish at exactly t_stop
    assert times[-1] == pytest.approx(0.05, abs=1e-12)


def test_adaptive_transient_refines_square_wave_edges():
    # Discontinuous drive: steps spanning an edge produce large LTE and are
    # rejected until the edge is resolved
    nodes = [
        {"id": "V1", "type": "voltage_source",
         "params": {"V": 5.0, "freq": 100.0, "wave": "square"}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "C1", "type": "capacitor", "params": {"C": 1e-7}, "pins": {"a": "n2", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    results, times, cmap = solver.solve_transient_adaptive(
        0.0, 0.02, dt_init=3e-4, lte_tol=1e-4, method="trapezoidal", uic=True)

    stats = solver.last_solve_stats
    assert stats["rejected_steps"] > 0

    out = results[cmap["n2"]]
    assert np.max(out) > 4.9
    assert np.min(out) < -4.9


def test_adaptive_transient_validation():
    solver = ContinuousSolver(rc_nodes(), [])
    with pytest.raises(ValueError, match="lte_tol"):
        solver.solve_transient_adaptive(0.0, 0.01, lte_tol=0.0)
    with pytest.raises(ValueError, match="t_stop"):
        solver.solve_transient_adaptive(0.01, 0.01)


# ---------------------------------------------------------------------------
# Subcircuits
# ---------------------------------------------------------------------------

def divider_definition():
    # vin -(1k)- mid -(1k)- vout -(2k)- gnd; 'mid' is internal-only
    return {
        "schema_version": "1.0.0",
        "metadata": {"name": "Divider Block", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "ports": ["vin", "vout"],
        "nodes": [
            {"id": "R1", "type": "resistor", "params": {"R": 1000.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "vin", "b": "mid"}},
            {"id": "R2", "type": "resistor", "params": {"R": 1000.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "mid", "b": "vout"}},
            {"id": "R3", "type": "resistor", "params": {"R": 2000.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "vout", "b": "n0"}}
        ],
        "edges": [],
        "nets": ["n0", "vin", "mid", "vout"]
    }


def parent_graph(ref="divider"):
    return {
        "schema_version": "1.0.0",
        "metadata": {"name": "Parent", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [
            {"id": "V1", "type": "voltage_source", "params": {"V": 10.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "n1", "b": "n0"}},
            {"id": "X1", "type": "subcircuit", "params": {"ref": ref},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"vin": "n1", "vout": "n2"}}
        ],
        "edges": [],
        "nets": ["n0", "n1", "n2"]
    }


def test_flatten_and_solve_subcircuit():
    defs = {"divider": divider_definition()}
    flat = flatten_subcircuits(parent_graph(), lambda ref: defs[ref])

    ids = {n["id"] for n in flat["nodes"]}
    assert ids == {"V1", "X1.R1", "X1.R2", "X1.R3"}
    assert "X1.mid" in flat["nets"]          # internal net is namespaced
    assert "X1.vin" not in flat["nets"]      # ports map onto parent nets

    is_valid, msg = NativeGraphValidator.validate(flat)
    assert is_valid, msg

    solver = ContinuousSolver(flat["nodes"], flat["edges"])
    x, cmap = solver.solve_dc()
    # 10V across 1k+1k+2k, vout tap above the 2k: 5V
    assert x[cmap["n2"]] == pytest.approx(5.0, abs=1e-3)


def test_flatten_nested_subcircuits():
    stage = {
        "schema_version": "1.0.0",
        "metadata": {"name": "Stage", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "ports": ["p_in", "p_out"],
        "nodes": [
            {"id": "XD", "type": "subcircuit", "params": {"ref": "divider"},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"vin": "p_in", "vout": "p_out"}}
        ],
        "edges": [],
        "nets": ["n0", "p_in", "p_out"]
    }
    defs = {"divider": divider_definition(), "stage": stage}
    parent = parent_graph(ref="stage")
    parent["nodes"][1]["pins"] = {"p_in": "n1", "p_out": "n2"}
    flat = flatten_subcircuits(parent, lambda ref: defs[ref])

    ids = {n["id"] for n in flat["nodes"]}
    assert "X1.XD.R1" in ids

    solver = ContinuousSolver(flat["nodes"], flat["edges"])
    x, cmap = solver.solve_dc()
    assert x[cmap["n2"]] == pytest.approx(5.0, abs=1e-3)


def test_flatten_error_cases():
    defs = {"divider": divider_definition()}

    # Unconnected port
    bad_parent = parent_graph()
    del bad_parent["nodes"][1]["pins"]["vout"]
    with pytest.raises(SubcircuitError, match="unconnected"):
        flatten_subcircuits(bad_parent, lambda ref: defs[ref])

    # Missing ref
    bad_parent = parent_graph()
    bad_parent["nodes"][1]["params"] = {}
    with pytest.raises(SubcircuitError, match="params.ref"):
        flatten_subcircuits(bad_parent, lambda ref: defs[ref])

    # Definition without ports
    no_ports = divider_definition()
    del no_ports["ports"]
    with pytest.raises(SubcircuitError, match="no ports"):
        flatten_subcircuits(parent_graph(), lambda ref: no_ports)

    # Port not present in the definition's nets
    bad_def = divider_definition()
    bad_def["ports"] = ["vin", "vout", "ghost"]
    bad_parent = parent_graph()
    bad_parent["nodes"][1]["pins"]["ghost"] = "n0"
    with pytest.raises(SubcircuitError, match="ghost"):
        flatten_subcircuits(bad_parent, lambda ref: bad_def)

    # Definition cycle
    cyclic = divider_definition()
    cyclic["nodes"].append({
        "id": "XC", "type": "subcircuit", "params": {"ref": "divider"},
        "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"vin": "vin", "vout": "vout"}
    })
    with pytest.raises(SubcircuitError, match="max depth"):
        flatten_subcircuits(parent_graph(), lambda ref: cyclic)


def test_validator_checks_ports():
    graph = divider_definition()
    graph["ports"] = ["vin", "not_a_net"]
    is_valid, msg = NativeGraphValidator.validate(graph)
    assert not is_valid
    assert "not_a_net" in msg


def test_api_simulates_subcircuit_instance():
    def_path = os.path.join(SCHEMATICS_DIR, "test_subdef.vcg.json")
    parent_path = os.path.join(SCHEMATICS_DIR, "test_subparent.vcg.json")

    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": def_path, "graph": divider_definition()}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": parent_path, "graph": parent_graph(ref=def_path)}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": parent_path}
    }).json()["status"] == "ok"

    r = client.post("/api/agent/action", json={
        "action": "run_simulation", "params": {"path": parent_path, "mode": "dc", "params": {}}
    })
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert data["x"][data["cmap"]["n2"]] == pytest.approx(5.0, abs=1e-3)
    # Flattened internals are addressable in the result map
    assert "X1.mid" in data["cmap"]
    assert data["operating_point"]["X1.R1"]["i"] == pytest.approx(10.0 / 4000.0, abs=1e-6)

    # The stored parent graph itself is untouched by flattening
    live = active_schematics[parent_path]
    assert {n["id"] for n in live["nodes"]} == {"V1", "X1"}

    # A definition outside the workspace is rejected
    bad_parent = parent_graph(ref="/etc/passwd")
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": parent_path, "graph": bad_parent}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": parent_path}
    }).json()["status"] == "ok"
    r = client.post("/api/agent/action", json={
        "action": "run_simulation", "params": {"path": parent_path, "mode": "dc", "params": {}}
    })
    assert r.status_code == 400
    assert "Unauthorized" in r.json()["error"]["message"]
