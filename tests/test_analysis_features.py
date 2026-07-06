"""Tests for AC small-signal analysis, waveform sources, telemetry, and
the DC operating-point report."""
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, SCHEMATICS_DIR, active_schematics
from voltcraft.engine.solver import ContinuousSolver, source_voltage_at

client = TestClient(app)

AC_TEST_FILE = "test_feat_ac.vcg.json"


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in (AC_TEST_FILE, AC_TEST_FILE.replace(".vcg.json", ".drawio.xml")):
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


def rc_lowpass_nodes():
    # R=1k, C=1uF -> fc = 1/(2*pi*R*C) = 159.155 Hz
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": 1.0, "ac_mag": 1.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "C1", "type": "capacitor", "params": {"C": 1e-6}, "pins": {"a": "n2", "b": "n0"}}
    ]


def test_ac_rc_lowpass_bode_matches_theory():
    solver = ContinuousSolver(rc_lowpass_nodes(), [])
    freqs, mag_db, phase_deg, cmap = solver.solve_ac(1.0, 1e5, points_per_decade=40)

    idx_out = cmap["n2"]
    fc = 1.0 / (2.0 * math.pi * 1000.0 * 1e-6)

    # Compare every sweep point against the analytic first-order response
    for k, f in enumerate(freqs):
        h = 1.0 / complex(1.0, f / fc)
        assert abs(mag_db[idx_out][k] - 20.0 * math.log10(abs(h))) < 0.05, f"magnitude off at {f} Hz"
        assert abs(phase_deg[idx_out][k] - math.degrees(math.atan2(h.imag, h.real))) < 0.5, f"phase off at {f} Hz"

    # Spot-check the corner: ~-3.01 dB and ~-45 degrees
    k_fc = min(range(len(freqs)), key=lambda i: abs(freqs[i] - fc))
    assert abs(mag_db[idx_out][k_fc] + 3.01) < 0.2
    assert abs(phase_deg[idx_out][k_fc] + 45.0) < 2.0

    assert solver.last_solve_stats["analysis"] == "ac"
    assert solver.last_solve_stats["points"] == len(freqs)


def test_ac_implicit_drive_uses_first_voltage_source():
    # Without any ac_mag annotation, the first voltage source drives at 1V
    nodes = rc_lowpass_nodes()
    del nodes[0]["params"]["ac_mag"]
    freqs, mag_db, _, cmap = ContinuousSolver(nodes, []).solve_ac(1.0, 100.0, 10)
    # Input net follows the 1V drive: 0 dB across the sweep
    assert abs(mag_db[cmap["n1"]][0]) < 0.01


def test_ac_inverting_opamp_flat_gain():
    # Inverting amp, gain -2: |H| = 6.02 dB, phase ~180 degrees in-band
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 1.0, "ac_mag": 1.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "Rin", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "Rf", "type": "resistor", "params": {"R": 2000.0}, "pins": {"a": "n2", "b": "n3"}},
        {"id": "U1", "type": "opamp", "params": {"gain": 1e5, "Rin": 1e6, "Rout": 50.0},
         "pins": {"non_inverting": "n0", "inverting": "n2", "out": "n3"}}
    ]
    freqs, mag_db, phase_deg, cmap = ContinuousSolver(nodes, []).solve_ac(1.0, 1e4, 10)

    idx_out = cmap["n3"]
    assert abs(mag_db[idx_out][0] - 6.02) < 0.05
    assert abs(abs(phase_deg[idx_out][0]) - 180.0) < 1.0
    # The ideal-opamp response is flat across the sweep
    assert abs(mag_db[idx_out][-1] - mag_db[idx_out][0]) < 0.05


def test_ac_rejects_invalid_sweep_ranges():
    solver = ContinuousSolver(rc_lowpass_nodes(), [])
    with pytest.raises(ValueError, match="f_start"):
        solver.solve_ac(0.0, 1e3)
    with pytest.raises(ValueError, match="f_stop"):
        solver.solve_ac(1e3, 1e3)
    with pytest.raises(ValueError, match="points_per_decade"):
        solver.solve_ac(1.0, 1e3, points_per_decade=0)


def test_waveform_shapes():
    # DC level when no frequency is given (backward compatible)
    assert source_voltage_at({"V": 3.3}, 0.123) == 3.3

    # Sine matches the original formula
    p_sine = {"V": 5.0, "freq": 100.0, "phase": 0.5}
    for t in (0.0, 1e-3, 7e-3):
        assert source_voltage_at(p_sine, t) == pytest.approx(5.0 * math.sin(2.0 * math.pi * 100.0 * t + 0.5))

    # Square: +V for the first half cycle, -V for the second (50% duty)
    p_sq = {"V": 2.0, "freq": 1000.0, "wave": "square"}
    assert source_voltage_at(p_sq, 0.1e-3) == 2.0
    assert source_voltage_at(p_sq, 0.6e-3) == -2.0
    # 25% duty
    p_sq_d = {"V": 2.0, "freq": 1000.0, "wave": "square", "duty": 0.25}
    assert source_voltage_at(p_sq_d, 0.2e-3) == 2.0
    assert source_voltage_at(p_sq_d, 0.3e-3) == -2.0

    # Triangle: -V at cycle start, +V at half cycle, 0 at quarter cycle
    p_tri = {"V": 4.0, "freq": 1000.0, "wave": "triangle"}
    assert source_voltage_at(p_tri, 0.0) == pytest.approx(-4.0)
    assert source_voltage_at(p_tri, 0.25e-3) == pytest.approx(0.0)
    assert source_voltage_at(p_tri, 0.5e-3) == pytest.approx(4.0)

    # Sawtooth: linear ramp -V .. +V
    p_saw = {"V": 1.0, "freq": 1000.0, "wave": "sawtooth"}
    assert source_voltage_at(p_saw, 0.0) == pytest.approx(-1.0)
    assert source_voltage_at(p_saw, 0.5e-3) == pytest.approx(0.0)
    assert source_voltage_at(p_saw, 0.75e-3) == pytest.approx(0.5)

    # Offset shifts periodic waves
    p_off = {"V": 2.0, "freq": 1000.0, "wave": "square", "offset": 2.5}
    assert source_voltage_at(p_off, 0.1e-3) == 4.5
    assert source_voltage_at(p_off, 0.6e-3) == 0.5

    with pytest.raises(ValueError, match="waveform"):
        source_voltage_at({"V": 1.0, "freq": 10.0, "wave": "chirp"}, 0.0)


def test_transient_square_wave_drives_rc():
    # 100 Hz square wave into an RC with tau much shorter than the half
    # period: the output must settle near both rails within each half cycle
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0, "freq": 100.0, "wave": "square"},
         "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 100.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "C1", "type": "capacitor", "params": {"C": 1e-6}, "pins": {"a": "n2", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    results, times, cmap = solver.solve_transient(0.0, 0.02, 1e-5, uic=True)

    out = results[cmap["n2"]]
    assert np.max(out) > 4.9
    assert np.min(out) < -4.9

    stats = solver.last_solve_stats
    assert stats["analysis"] == "transient"
    assert stats["timesteps"] == 2000
    assert stats["newton_iterations"] >= stats["timesteps"]


def test_unwired_voltage_source_reports_component_name():
    # A source with both terminals on ground is unsolvable; the error must
    # name the offending component instead of numpy's bare 'Singular matrix'
    nodes = [
        {"id": "V7", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n0", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n0"}}
    ]
    with pytest.raises(ValueError, match="V7.*wire its pins"):
        ContinuousSolver(nodes, []).solve_dc()


def test_dc_operating_point_report():
    # 10V across 1k + 1k divider: 5 mA everywhere, 25 mW per resistor
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "R2", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n2", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    report = solver.dc_operating_report(x)

    assert report["R1"]["i"] == pytest.approx(0.005, abs=1e-6)
    assert report["R1"]["p"] == pytest.approx(0.025, abs=1e-5)
    assert report["R2"]["v"] == pytest.approx(5.0, abs=1e-3)
    # MNA branch convention: a delivering source carries negative current
    assert report["V1"]["i"] == pytest.approx(-0.005, abs=1e-6)
    assert report["V1"]["p"] == pytest.approx(-0.05, abs=1e-4)

    stats = solver.last_solve_stats
    assert stats["analysis"] == "dc"
    assert stats["converged"] is True
    assert stats["residual"] < 1e-6
    assert stats["matrix_size"] == 3
    assert stats["condition_estimate"] > 1.0


def test_api_update_params():
    path = os.path.join(SCHEMATICS_DIR, AC_TEST_FILE)
    r = client.post("/api/agent/action", json={"action": "load_schematic", "params": {"path": path}})
    assert r.json()["status"] == "ok"
    r = client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "resistor", "params": {"R": 1000.0}}
    })
    node_id = r.json()["data"]["node_id"]

    r = client.post("/api/agent/action", json={
        "action": "update_params",
        "params": {"path": path, "id": node_id, "params": {"R": 4700.0, "tol": 0.01}}
    })
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["params"]["R"] == 4700.0
    assert body["data"]["params"]["tol"] == 0.01

    live = active_schematics[path]
    node = next(n for n in live["nodes"] if n["id"] == node_id)
    assert node["params"]["R"] == 4700.0

    # Unknown component is rejected by name
    r = client.post("/api/agent/action", json={
        "action": "update_params",
        "params": {"path": path, "id": "ZZ9", "params": {"R": 1.0}}
    })
    assert r.status_code == 400
    assert "ZZ9" in r.json()["error"]["message"]


def test_transistor_placement_defaults():
    path = os.path.join(SCHEMATICS_DIR, AC_TEST_FILE)
    r = client.post("/api/agent/action", json={"action": "load_schematic", "params": {"path": path}})
    assert r.json()["status"] == "ok"

    r = client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "nmos", "params": {"K": 2e-3, "Vth": 1.0}}
    })
    assert r.json()["data"]["node_id"] == "M1"
    r = client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "bjt_npn", "params": {}}
    })
    assert r.json()["data"]["node_id"] == "Q1"

    live = active_schematics[path]
    nodes = {n["id"]: n for n in live["nodes"]}
    assert set(nodes["M1"]["pins"].keys()) == {"gate", "drain", "source"}
    assert set(nodes["Q1"]["pins"].keys()) == {"base", "collector", "emitter"}


def test_api_dc_returns_stats_and_operating_point():
    path = os.path.join(SCHEMATICS_DIR, AC_TEST_FILE)
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "AC Feature Test", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": rc_lowpass_nodes(),
        "edges": [],
        "nets": ["n0", "n1", "n2"]
    }
    for node in graph["nodes"]:
        node["pos"] = {"x": 100, "y": 100}
        node["rot"] = 0

    r = client.post("/api/agent/action", json={"action": "save_schematic", "params": {"path": path, "graph": graph}})
    assert r.json()["status"] == "ok"
    r = client.post("/api/agent/action", json={"action": "load_schematic", "params": {"path": path}})
    assert r.json()["status"] == "ok"

    r = client.post("/api/agent/action", json={
        "action": "run_simulation", "params": {"path": path, "mode": "dc", "params": {}}
    })
    body = r.json()
    assert body["status"] == "ok"
    assert body["data"]["stats"]["analysis"] == "dc"
    assert body["data"]["stats"]["converged"] is True
    assert body["data"]["operating_point"]["R1"]["i"] == pytest.approx(0.0, abs=1e-6)  # C blocks DC

    # AC sweep through the API
    r = client.post("/api/agent/action", json={
        "action": "run_simulation",
        "params": {"path": path, "mode": "ac",
                   "params": {"f_start": 1.0, "f_stop": 1e5, "points_per_decade": 20}}
    })
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert len(data["freqs"]) == len(data["magnitude_db"][0]) == len(data["phase_deg"][0])
    assert data["stats"]["analysis"] == "ac"

    idx_out = data["cmap"]["n2"]
    fc = 1.0 / (2.0 * math.pi * 1000.0 * 1e-6)
    k_fc = min(range(len(data["freqs"])), key=lambda i: abs(data["freqs"][i] - fc))
    assert abs(data["magnitude_db"][idx_out][k_fc] + 3.01) < 0.3
