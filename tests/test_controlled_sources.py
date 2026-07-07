"""Tests for dependent sources (E/G/F/H) and implicit device capacitances."""
import math

import numpy as np
import pytest

from voltcraft.engine.solver import ContinuousSolver


def test_vcvs_gain():
    # E1: v(out) = 5 * v(control); control at 2V -> out 10V
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 2.0}, "pins": {"a": "nc", "b": "n0"}},
        {"id": "E1", "type": "vcvs", "params": {"gain": 5.0}, "pins": {"p": "no", "n": "n0", "cp": "nc", "cn": "n0"}},
        {"id": "RL", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "no", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    assert x[cmap["no"]] == pytest.approx(10.0, abs=1e-6)

    # The VCVS carries a branch current: it drives 10 mA through the load
    report = solver.dc_operating_report(x)
    assert report["E1"]["i"] == pytest.approx(-0.01, abs=1e-6)

    # AC: flat gain of 5 (13.98 dB) across the sweep
    nodes[0]["params"]["ac_mag"] = 1.0
    freqs, mag_db, _, cmap = ContinuousSolver(nodes, []).solve_ac(1.0, 1e3, 10)
    assert mag_db[cmap["no"]][0] == pytest.approx(20.0 * math.log10(5.0), abs=0.01)
    assert mag_db[cmap["no"]][-1] == pytest.approx(mag_db[cmap["no"]][0], abs=0.01)


def test_vccs_transconductance():
    # G1: 1 mS * 2V = 2 mA drawn out of node no through the element -> -2V on 1k
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 2.0}, "pins": {"a": "nc", "b": "n0"}},
        {"id": "G1", "type": "vccs", "params": {"gm": 1e-3}, "pins": {"p": "no", "n": "n0", "cp": "nc", "cn": "n0"}},
        {"id": "RL", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "no", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    assert x[cmap["no"]] == pytest.approx(-2.0, abs=1e-6)

    report = solver.dc_operating_report(x)
    assert report["G1"]["i"] == pytest.approx(2e-3, abs=1e-9)


def divider_with(controlled_node):
    # 10V across 2k gives I(V1) = -5 mA in the MNA/SPICE branch convention
    return [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 2000.0}, "pins": {"a": "n1", "b": "n0"}},
        controlled_node,
        {"id": "RL", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "no", "b": "n0"}}
    ]


def test_cccs_current_gain():
    # F1: i = -2 * I(V1) = +10 mA flowing p->n; with RL=1k on p:
    # KCL at no: v/1k + 10mA = 0 -> v = -10V
    nodes = divider_with(
        {"id": "F1", "type": "cccs", "params": {"gain": -2.0, "control": "V1"}, "pins": {"p": "no", "n": "n0"}}
    )
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()

    assert x[cmap["branch_V1"]] == pytest.approx(-5e-3, abs=1e-8)
    assert x[cmap["no"]] == pytest.approx(-10.0, abs=1e-6)

    report = solver.dc_operating_report(x)
    assert report["F1"]["i"] == pytest.approx(0.01, abs=1e-8)


def test_ccvs_transresistance():
    # H1: v(out) = 1000 * I(V1) = -5V
    nodes = divider_with(
        {"id": "H1", "type": "ccvs", "params": {"r": 1000.0, "control": "V1"}, "pins": {"p": "no", "n": "n0"}}
    )
    x, cmap = ContinuousSolver(nodes, []).solve_dc()
    assert x[cmap["no"]] == pytest.approx(-5.0, abs=1e-6)


def test_current_controlled_source_requires_valid_control():
    nodes = divider_with(
        {"id": "F1", "type": "cccs", "params": {"gain": 2.0, "control": "R1"}, "pins": {"p": "no", "n": "n0"}}
    )
    with pytest.raises(ValueError, match="F1.*control"):
        ContinuousSolver(nodes, []).solve_dc()

    nodes = divider_with(
        {"id": "H1", "type": "ccvs", "params": {"r": 100.0}, "pins": {"p": "no", "n": "n0"}}
    )
    with pytest.raises(ValueError, match="H1.*control"):
        ContinuousSolver(nodes, []).solve_dc()


def test_diode_junction_capacitance_ac_pole():
    # Reverse-biased diode (0V bias) with Cj0 acts as a capacitor:
    # R=1k, Cj0=1uF -> the classic 159.15 Hz low-pass corner
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 0.0, "ac_mag": 1.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "D1", "type": "diode", "params": {"Is": 1e-14, "N": 1.0, "Cj0": 1e-6},
         "pins": {"anode": "n2", "cathode": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    freqs, mag_db, phase_deg, cmap = solver.solve_ac(1.0, 1e5, 40)

    fc = 1.0 / (2.0 * math.pi * 1000.0 * 1e-6)
    k_fc = min(range(len(freqs)), key=lambda i: abs(freqs[i] - fc))
    assert mag_db[cmap["n2"]][k_fc] == pytest.approx(-3.01, abs=0.2)
    assert phase_deg[cmap["n2"]][k_fc] == pytest.approx(-45.0, abs=2.0)


def test_diode_junction_capacitance_transient():
    # Same reverse-biased junction driven by a step through R charges with
    # tau = R * Cj0 like an RC (junction conductance is negligible)
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": -5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "D1", "type": "diode", "params": {"Is": 1e-14, "N": 1.0, "Cj0": 1e-5},
         "pins": {"anode": "n2", "cathode": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    results, times, cmap = solver.solve_transient(0.0, 0.05, 1e-4, method="trapezoidal", uic=True)

    idx = cmap["n2"]
    tau = 1000.0 * 1e-5
    k = min(range(len(times)), key=lambda i: abs(times[i] - tau))
    expected = -5.0 * (1.0 - math.exp(-1.0))
    assert results[idx][k] == pytest.approx(expected, abs=0.05)


def test_mosfet_gate_capacitance_input_pole():
    # Common-source stage driven through Rg=10k with Cgs=10nF:
    # input pole at 1/(2*pi*Rg*Cgs) = 1.59 kHz; gain drops 3 dB there
    nodes = [
        {"id": "VDD", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vdd", "b": "n0"}},
        {"id": "VG", "type": "voltage_source", "params": {"V": 2.0, "ac_mag": 1.0}, "pins": {"a": "n_in", "b": "n0"}},
        {"id": "RG", "type": "resistor", "params": {"R": 10000.0}, "pins": {"a": "n_in", "b": "n_g"}},
        {"id": "RD", "type": "resistor", "params": {"R": 5000.0}, "pins": {"a": "n_vdd", "b": "n_d"}},
        {"id": "M1", "type": "nmos", "params": {"K": 2e-3, "Vth": 1.0, "Cgs": 1e-8},
         "pins": {"gate": "n_g", "drain": "n_d", "source": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    freqs, mag_db, _, cmap = solver.solve_ac(10.0, 1e5, 40)

    idx_d = cmap["n_d"]
    # In-band gain is still gm*Rd = 10 (Rg draws no DC gate current)
    assert mag_db[idx_d][0] == pytest.approx(20.0, abs=0.1)

    fc = 1.0 / (2.0 * math.pi * 10000.0 * 1e-8)
    k_fc = min(range(len(freqs)), key=lambda i: abs(freqs[i] - fc))
    assert mag_db[idx_d][k_fc] == pytest.approx(20.0 - 3.01, abs=0.2)


def test_mosfet_inverter_with_gate_caps_still_switches():
    # Gate capacitance slows but must not break the square-wave inverter
    nodes = [
        {"id": "VDD", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n_vdd", "b": "n0"}},
        {"id": "VIN", "type": "voltage_source",
         "params": {"V": 2.5, "freq": 1000.0, "wave": "square", "offset": 2.5}, "pins": {"a": "n_in", "b": "n0"}},
        {"id": "RG", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n_in", "b": "n_g"}},
        {"id": "RD", "type": "resistor", "params": {"R": 10000.0}, "pins": {"a": "n_vdd", "b": "n_d"}},
        {"id": "M1", "type": "nmos", "params": {"K": 2e-3, "Vth": 1.0, "Cgs": 1e-8, "Cgd": 2e-9},
         "pins": {"gate": "n_g", "drain": "n_d", "source": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    results, times, cmap = solver.solve_transient(0.0, 2e-3, 1e-6, uic=False)

    out = results[cmap["n_d"]]
    assert np.max(out) > 4.8
    assert np.min(out) < 0.5
