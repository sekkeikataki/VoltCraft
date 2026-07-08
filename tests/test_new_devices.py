"""Tests for zener, LED, potentiometer, voltage-controlled switch, and
ideal transformer across DC, transient, AC, and the API."""
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, SCHEMATICS_DIR, active_schematics
from voltcraft.engine.solver import ContinuousSolver

client = TestClient(app)
DEV_FILE = "test_new_devices.vcg.json"


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in (DEV_FILE, DEV_FILE.replace(".vcg.json", ".drawio.xml")):
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


# --------------------------------------------------------------------------
# Zener diode
# --------------------------------------------------------------------------

def zener_regulator(vin, vz=5.1, r=1000.0):
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": vin}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": r}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "Z1", "type": "zener", "params": {"Vz": vz}, "pins": {"anode": "n0", "cathode": "n2"}},
    ]


def test_zener_regulates_reverse_voltage():
    # Above the knee the node clamps near Vz; well below it, no regulation
    solver = ContinuousSolver(zener_regulator(12.0, vz=5.1), [])
    x, cmap = solver.solve_dc()
    assert 5.1 <= x[cmap["n2"]] <= 5.35
    assert solver.last_solve_stats["converged"] is True

    # Different Vz tracks
    x, cmap = ContinuousSolver(zener_regulator(12.0, vz=8.2), []).solve_dc()
    assert 8.2 <= x[cmap["n2"]] <= 8.6

    # Input below Vz: the zener is off, node follows the divider (no load)
    x, cmap = ContinuousSolver(zener_regulator(3.0, vz=5.1), []).solve_dc()
    assert x[cmap["n2"]] == pytest.approx(3.0, abs=0.05)


def test_zener_forward_behaves_like_a_diode():
    # Forward-biased zener drops the usual ~0.7V
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "Z1", "type": "zener", "params": {"Vz": 5.1}, "pins": {"anode": "n2", "cathode": "n0"}},
    ]
    x, cmap = ContinuousSolver(nodes, []).solve_dc()
    assert 0.6 < x[cmap["n2"]] < 0.8


def test_zener_regulation_holds_over_input_sweep():
    # Sweep the input well above the knee; output stays clamped in a tight band
    solver = ContinuousSolver(zener_regulator(10.0, vz=5.1), [])
    values, results, cmap = solver.solve_dc_sweep("V1", "V", 8.0, 20.0, 13)
    outs = results[cmap["n2"]]
    assert np.all(outs >= 5.1) and np.all(outs <= 5.5)
    # Monotonic, mild rise with input (finite zener impedance)
    assert outs[-1] > outs[0]


# --------------------------------------------------------------------------
# LED
# --------------------------------------------------------------------------

def test_led_forward_drop():
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 330.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "D1", "type": "led", "params": {}, "pins": {"anode": "n2", "cathode": "n0"}},
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    v_led = x[cmap["n2"]]
    assert 1.7 < v_led < 2.3   # typical LED forward drop
    report = solver.dc_operating_report(x)
    assert report["D1"]["i"] > 0    # conducting


# --------------------------------------------------------------------------
# Potentiometer
# --------------------------------------------------------------------------

def test_potentiometer_divides_by_wiper_position():
    for w, expected in [(0.5, 5.0), (0.25, 7.5), (0.75, 2.5), (0.1, 9.0)]:
        nodes = [
            {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "na", "b": "n0"}},
            {"id": "P1", "type": "potentiometer", "params": {"R": 10000.0, "wiper": w},
             "pins": {"a": "na", "wiper": "nw", "b": "n0"}},
        ]
        x, cmap = ContinuousSolver(nodes, []).solve_dc()
        assert x[cmap["nw"]] == pytest.approx(expected, abs=1e-3), f"w={w}"


def test_potentiometer_as_rheostat_limits_current():
    # Wiper tied to b makes a rheostat of R*w in series; check the current
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "na", "b": "n0"}},
        {"id": "P1", "type": "potentiometer", "params": {"R": 10000.0, "wiper": 0.5},
         "pins": {"a": "na", "wiper": "nw", "b": "nw"}},
        {"id": "RL", "type": "resistor", "params": {"R": 5000.0}, "pins": {"a": "nw", "b": "n0"}},
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    # a->wiper resistance is R*w = 5k (b->wiper shorted), in series with 5k load
    assert x[cmap["nw"]] == pytest.approx(10.0 * 5000.0 / (5000.0 + 5000.0), abs=1e-2)


# --------------------------------------------------------------------------
# Voltage-controlled switch
# --------------------------------------------------------------------------

def switch_circuit(ctrl, inverted=False):
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "VC", "type": "voltage_source", "params": {"V": ctrl}, "pins": {"a": "nc", "b": "n0"}},
        {"id": "SW", "type": "switch",
         "params": {"threshold": 2.5, "Ron": 1.0, "Roff": 1e9, "inverted": inverted},
         "pins": {"p": "n1", "n": "nl", "cp": "nc", "cn": "n0"}},
        {"id": "RL", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "nl", "b": "n0"}},
    ]


def test_switch_opens_and_closes_on_control():
    x, cmap = ContinuousSolver(switch_circuit(5.0), []).solve_dc()
    assert x[cmap["nl"]] > 9.9          # closed: load sees the source

    x, cmap = ContinuousSolver(switch_circuit(0.0), []).solve_dc()
    assert x[cmap["nl"]] < 1e-3         # open: load isolated


def test_switch_inverted_logic():
    # Inverted switch is open when control is high
    x, cmap = ContinuousSolver(switch_circuit(5.0, inverted=True), []).solve_dc()
    assert x[cmap["nl"]] < 1e-3
    x, cmap = ContinuousSolver(switch_circuit(0.0, inverted=True), []).solve_dc()
    assert x[cmap["nl"]] > 9.9


def test_switch_operating_report_state():
    solver = ContinuousSolver(switch_circuit(5.0), [])
    x, _ = solver.solve_dc()
    report = solver.dc_operating_report(x)
    assert report["SW"]["closed"] == 1.0


# --------------------------------------------------------------------------
# Ideal transformer
# --------------------------------------------------------------------------

def transformer_circuit(a, vp=10.0, rload=1000.0, freq=0.0):
    src = {"V": vp}
    if freq:
        src = {"V": vp, "freq": freq}
    return [
        {"id": "V1", "type": "voltage_source", "params": src, "pins": {"a": "p1", "b": "n0"}},
        {"id": "T1", "type": "transformer", "params": {"ratio": a},
         "pins": {"p1": "p1", "p2": "n0", "s1": "s1", "s2": "n0"}},
        {"id": "RL", "type": "resistor", "params": {"R": rload}, "pins": {"a": "s1", "b": "n0"}},
    ]


def test_transformer_voltage_ratio():
    for a in (2.0, 0.5, 1.0, 4.0):
        x, cmap = ContinuousSolver(transformer_circuit(a), []).solve_dc()
        assert x[cmap["s1"]] == pytest.approx(10.0 / a, abs=1e-4), f"a={a}"


def test_transformer_conserves_power():
    # Step-down a=2, 10V primary, 1k load: 5V/5mA/25mW on the secondary,
    # and the primary must draw exactly that power (lossless)
    solver = ContinuousSolver(transformer_circuit(2.0), [])
    x, cmap = solver.solve_dc()
    report = solver.dc_operating_report(x)
    assert report["RL"]["p"] == pytest.approx(0.025, abs=1e-6)
    assert report["T1"]["p"] == pytest.approx(0.025, abs=1e-6)   # primary in = load out
    # a=2 reflects the 1k load to 250 ohms at the primary: 10V/250 = 40mA...
    # but the branch current is the primary winding current i_p = i_s/a =
    # 5mA/2 = 2.5mA (the transformer conserves power, not current)
    assert report["T1"]["i"] == pytest.approx(0.0025, abs=1e-5)


def test_transformer_transient_ac_coupling():
    # A 1kHz sine through a step-up transformer appears scaled on the secondary
    solver = ContinuousSolver(transformer_circuit(0.5, vp=5.0, freq=1000.0), [])
    results, times, cmap = solver.solve_transient(0.0, 2e-3, 1e-5, uic=True)
    prim = results[cmap["p1"]]
    sec = results[cmap["s1"]]
    # Secondary peak is 2x primary peak (a=0.5 steps up)
    assert np.max(sec) == pytest.approx(2.0 * np.max(prim), rel=1e-3)


def test_transformer_ac_transfer():
    solver = ContinuousSolver(
        [
            {"id": "V1", "type": "voltage_source", "params": {"V": 1.0, "ac_mag": 1.0}, "pins": {"a": "p1", "b": "n0"}},
            {"id": "T1", "type": "transformer", "params": {"ratio": 2.0}, "pins": {"p1": "p1", "p2": "n0", "s1": "s1", "s2": "n0"}},
            {"id": "RL", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "s1", "b": "n0"}},
        ], [])
    freqs, mag_db, phase_deg, cmap = solver.solve_ac(1.0, 1e4, 5)
    # Flat -6.02 dB (half) transfer across frequency for a=2
    assert mag_db[cmap["s1"]][0] == pytest.approx(20.0 * math.log10(0.5), abs=0.05)
    assert mag_db[cmap["s1"]][-1] == pytest.approx(mag_db[cmap["s1"]][0], abs=0.05)


# --------------------------------------------------------------------------
# API integration
# --------------------------------------------------------------------------

def test_api_places_and_simulates_new_devices():
    path = os.path.join(SCHEMATICS_DIR, DEV_FILE)
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": path}
    }).json()["status"] == "ok"

    ids = {}
    for ctype in ("zener", "led", "potentiometer", "switch", "transformer"):
        r = client.post("/api/agent/action", json={
            "action": "place_component", "params": {"path": path, "type": ctype, "params": {}}
        })
        body = r.json()
        assert body["status"] == "ok", body
        ids[ctype] = body["data"]["node_id"]

    assert ids["potentiometer"].startswith("RV")
    assert ids["switch"].startswith("SW")
    assert ids["transformer"].startswith("T")

    live = active_schematics[path]
    pins = {n["id"]: set(n["pins"].keys()) for n in live["nodes"]}
    assert pins[ids["potentiometer"]] == {"a", "wiper", "b"}
    assert pins[ids["switch"]] == {"p", "n", "cp", "cn"}
    assert pins[ids["transformer"]] == {"p1", "p2", "s1", "s2"}

    # Wiki entries resolve
    for term in ("zener", "led", "potentiometer", "switch", "transformer"):
        r = client.post("/api/agent/action", json={"action": "query_wiki", "params": {"term": term}})
        assert r.json()["status"] == "ok"


def test_api_transformer_dc():
    path = os.path.join(SCHEMATICS_DIR, DEV_FILE)
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "Xfmr", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [
            {"id": "V1", "type": "voltage_source", "params": {"V": 20.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "p1", "b": "n0"}},
            {"id": "T1", "type": "transformer", "params": {"ratio": 4.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"p1": "p1", "p2": "n0", "s1": "s1", "s2": "n0"}},
            {"id": "RL", "type": "resistor", "params": {"R": 1000.0},
             "pos": {"x": 0, "y": 0}, "rot": 0, "pins": {"a": "s1", "b": "n0"}}
        ],
        "edges": [],
        "nets": ["n0", "p1", "s1"]
    }
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": path, "graph": graph}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": path}
    }).json()["status"] == "ok"
    r = client.post("/api/agent/action", json={
        "action": "run_simulation", "params": {"path": path, "mode": "dc", "params": {}}
    })
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert data["x"][data["cmap"]["s1"]] == pytest.approx(5.0, abs=1e-3)   # 20V / 4
    # Branch currents for both transformer windings are addressable
    assert "branch_T1:p" in data["cmap"]
    assert "branch_T1:s" in data["cmap"]
