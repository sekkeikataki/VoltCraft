"""Tests for Monte Carlo analysis, CSV export, BJT junction capacitance,
and the WebSocket replace_graph collaborative sync."""
import json
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, results_to_csv, SCHEMATICS_DIR, active_schematics
from voltcraft.engine.solver import ContinuousSolver

client = TestClient(app)

MC_FILE = "test_mc_export.vcg.json"


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in (MC_FILE, MC_FILE.replace(".vcg.json", ".drawio.xml")):
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


def toleranced_divider():
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0, "tol": 0.1}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "R2", "type": "resistor", "params": {"R": 1000.0, "tol": 0.1}, "pins": {"a": "n2", "b": "n0"}}
    ]


def test_monte_carlo_divider_statistics():
    nodes = toleranced_divider()
    solver = ContinuousSolver(nodes, [])
    mc, cmap = solver.solve_monte_carlo(runs=300, seed=42)

    idx = cmap["n2"]
    samples = mc["samples"][idx]

    # Worst-case corners with +/-10% resistors: 10*0.9/(0.9+1.1) .. mirrored
    assert np.all(samples >= 4.5 - 1e-9)
    assert np.all(samples <= 5.5 + 1e-9)
    assert mc["mean"][idx] == pytest.approx(5.0, abs=0.1)
    assert 0.1 < mc["std"][idx] < 0.3
    assert mc["min"][idx] < mc["mean"][idx] < mc["max"][idx]

    # Nominal values restored after the run
    assert nodes[1]["params"]["R"] == 1000.0
    assert nodes[2]["params"]["R"] == 1000.0

    stats = solver.last_solve_stats
    assert stats["analysis"] == "monte_carlo"
    assert stats["runs"] == 300
    assert stats["toleranced_components"] == 2
    assert stats["converged"] is True


def test_monte_carlo_is_seed_deterministic():
    mc1, cmap = ContinuousSolver(toleranced_divider(), []).solve_monte_carlo(runs=50, seed=7)
    mc2, _ = ContinuousSolver(toleranced_divider(), []).solve_monte_carlo(runs=50, seed=7)
    assert np.allclose(mc1["samples"], mc2["samples"])


def test_monte_carlo_validation():
    solver = ContinuousSolver(toleranced_divider(), [])
    with pytest.raises(ValueError, match="at least 2 runs"):
        solver.solve_monte_carlo(runs=1)
    with pytest.raises(ValueError, match="distribution"):
        solver.solve_monte_carlo(runs=10, distribution="cauchy")

    no_tol = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n0"}}
    ]
    with pytest.raises(ValueError, match="tol"):
        ContinuousSolver(no_tol, []).solve_monte_carlo(runs=10)


def test_bjt_junction_capacitance_ac_pole():
    # Cutoff-biased npn: junctions off, Cje + Cjc (collector at AC ground
    # through a tiny Rc) form the classic RC pole with the base resistor
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 0.0, "ac_mag": 1.0}, "pins": {"a": "n_in", "b": "n0"}},
        {"id": "RB", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n_in", "b": "n_b"}},
        {"id": "RC", "type": "resistor", "params": {"R": 1.0}, "pins": {"a": "n_c", "b": "n0"}},
        {"id": "Q1", "type": "bjt_npn", "params": {"Is": 1e-15, "beta_f": 100.0, "Cje": 5e-7, "Cjc": 5e-7},
         "pins": {"collector": "n_c", "base": "n_b", "emitter": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    freqs, mag_db, phase_deg, cmap = solver.solve_ac(1.0, 1e5, 40)

    fc = 1.0 / (2.0 * math.pi * 1000.0 * 1e-6)  # Cje + Cjc = 1uF
    k_fc = min(range(len(freqs)), key=lambda i: abs(freqs[i] - fc))
    assert mag_db[cmap["n_b"]][k_fc] == pytest.approx(-3.01, abs=0.25)


def save_and_load(path, nodes):
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "MC", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [dict(n, pos={"x": 100, "y": 100}, rot=0) for n in nodes],
        "edges": [],
        "nets": ["n0", "n1", "n2"]
    }
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": path, "graph": graph}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": path}
    }).json()["status"] == "ok"


def test_api_monte_carlo_and_csv_export():
    path = os.path.join(SCHEMATICS_DIR, MC_FILE)
    save_and_load(path, toleranced_divider())

    r = client.post("/api/agent/action", json={
        "action": "run_simulation",
        "params": {"path": path, "mode": "monte_carlo", "params": {"runs": 100, "seed": 3}}
    })
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    idx = data["cmap"]["n2"]
    assert 4.5 <= data["min"][idx] <= data["mean"][idx] <= data["max"][idx] <= 5.5
    assert len(data["samples"][idx]) == 100
    assert data["stats"]["analysis"] == "monte_carlo"

    # CSV export: transient
    r = client.post("/api/agent/action", json={
        "action": "export_csv",
        "params": {"path": path, "mode": "transient",
                   "params": {"t_stop": 0.001, "dt": 1e-4, "uic": True}}
    })
    body = r.json()
    assert body["status"] == "ok"
    lines = body["data"].strip().split("\n")
    header = lines[0].split(",")
    assert header[0] == "time_s"
    assert "n2" in header
    assert len(lines) == 1 + 11  # header + 10 steps + initial point

    # Values in the CSV match the header ordering
    first_row = lines[1].split(",")
    assert float(first_row[0]) == 0.0

    # CSV export: monte carlo statistics table
    r = client.post("/api/agent/action", json={
        "action": "export_csv",
        "params": {"path": path, "mode": "monte_carlo", "params": {"runs": 20, "seed": 1}}
    })
    body = r.json()
    assert body["status"] == "ok"
    lines = body["data"].strip().split("\n")
    assert lines[0].split(",")[0] == "metric"
    assert [row.split(",")[0] for row in lines[1:]] == ["mean", "std", "min", "max"]

    # CSV export: AC sweep columns
    r = client.post("/api/agent/action", json={
        "action": "export_csv",
        "params": {"path": path, "mode": "ac",
                   "params": {"f_start": 1.0, "f_stop": 1e3, "points_per_decade": 5}}
    })
    body = r.json()
    assert body["status"] == "ok"
    header = body["data"].split("\n")[0]
    assert header.startswith("freq_hz")
    assert "mag_db(n2)" in header and "phase_deg(n2)" in header

    # Unsupported mode is a clean error
    r = client.post("/api/agent/action", json={
        "action": "export_csv", "params": {"path": path, "mode": "digital", "params": {}}
    })
    assert r.status_code == 400


def test_results_to_csv_rejects_unmapped_modes():
    with pytest.raises(ValueError, match="not supported"):
        results_to_csv("digital", {"logs": {}})


def test_websocket_replace_graph_sync():
    path = os.path.join(SCHEMATICS_DIR, MC_FILE)
    save_and_load(path, toleranced_divider())

    live = active_schematics[path]
    edited = json.loads(json.dumps(live))
    edited["nodes"][1]["pos"] = {"x": 420, "y": 240}
    edited["nodes"][1]["rot"] = 90

    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({
            "type": "schematic_edit",
            "path": path,
            "action": "replace_graph",
            "agent_id": "Designer",
            "mutations": [],
            "graph": edited
        }))
        packet = ws.receive_json()

    assert packet["type"] == "schematic_mutated"
    moved = next(n for n in packet["graph"]["nodes"] if n["id"] == "R1")
    assert moved["pos"] == {"x": 420, "y": 240}
    assert moved["rot"] == 90

    # Server state was actually replaced (this is what used to revert)
    stored = next(n for n in active_schematics[path]["nodes"] if n["id"] == "R1")
    assert stored["pos"] == {"x": 420, "y": 240}

    # An invalid replacement graph is dropped and the old state re-broadcast
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_text(json.dumps({
            "type": "schematic_edit",
            "path": path,
            "action": "replace_graph",
            "agent_id": "Designer",
            "mutations": [],
            "graph": {"schema_version": "1.0.0"}
        }))
        packet = ws.receive_json()
    assert len(packet["graph"]["nodes"]) == 3  # authoritative state kept
