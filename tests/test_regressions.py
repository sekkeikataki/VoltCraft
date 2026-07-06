"""Regression tests for fixed bugs across the API, solver, codec, and scraper."""
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, active_schematics, SCHEMATICS_DIR
from voltcraft.engine.solver import ContinuousSolver
from voltcraft.engine.parser_drawio import DrawioCodec
from voltcraft.engine.parser_native import NativeGraphValidator
from voltcraft.engine.scraper import DatasheetScraper

client = TestClient(app)

REGR_FILES = [
    "test_regr_collision.vcg.json",
    "test_regr_collision.drawio.xml",
    "test_regr_delete.vcg.json",
    "test_regr_merge.vcg.json",
]


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in REGR_FILES:
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


def post_action(action, params, agent_id="Designer"):
    response = client.post("/api/agent/action", json={
        "action": action,
        "agent_id": agent_id,
        "params": params
    })
    return response


def test_load_schematic_rejects_sibling_directory_path():
    # A sibling directory sharing the workspace name as a string prefix must
    # be rejected (a naive startswith() check used to accept it)
    response = post_action("load_schematic", {"path": "voltcraft_evil/x.vcg.json"})
    assert response.status_code == 400
    assert "Unauthorized" in response.json()["error"]["message"]


def test_load_schematic_rejects_parent_traversal():
    response = post_action("load_schematic", {"path": "voltcraft/../outside.vcg.json"})
    assert response.status_code == 400
    assert "Unauthorized" in response.json()["error"]["message"]


def test_save_schematic_rejects_outside_path():
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "x", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [],
        "edges": [],
        "nets": ["n0"]
    }
    response = post_action("save_schematic", {"path": "/tmp/evil.vcg.json", "graph": graph})
    assert response.status_code == 400
    assert "Unauthorized" in response.json()["error"]["message"]


def test_query_wiki_rejects_traversal_and_unknown_terms():
    response = post_action("query_wiki", {"term": "../circuits/voltage_divider"})
    assert response.status_code == 400

    response = post_action("query_wiki", {"term": "flux_capacitor"})
    assert response.status_code == 400
    assert "No wiki entry" in response.json()["error"]["message"]


def test_wire_pins_new_net_does_not_collide_with_existing_names():
    # A schematic whose net names are sparse ("n2" exists but "n1" does not):
    # naming a new net "n<len(nets)>" used to collide with "n2" and silently
    # merge unrelated nets
    path = os.path.join(SCHEMATICS_DIR, "test_regr_collision.vcg.json")
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "Sparse Nets", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [
            {"id": "R9", "type": "resistor", "params": {"R": 100.0},
             "pos": {"x": 10, "y": 10}, "rot": 0, "pins": {"a": "n2", "b": "n0"}}
        ],
        "edges": [],
        "nets": ["n0", "n2"]
    }
    assert post_action("save_schematic", {"path": path, "graph": graph}).json()["status"] == "ok"
    assert post_action("load_schematic", {"path": path}).json()["status"] == "ok"

    for _ in range(2):
        res = post_action("place_component", {"path": path, "type": "resistor", "params": {}})
        assert res.json()["status"] == "ok"

    res = post_action("wire_pins", {
        "path": path,
        "from": {"node_id": "R1", "pin": "a"},
        "to": {"node_id": "R2", "pin": "a"}
    })
    data = res.json()
    assert data["status"] == "ok"
    new_net = data["data"]["net"]
    assert new_net not in ("n0", "n2")

    live_graph = active_schematics[path]
    assert len(live_graph["nets"]) == len(set(live_graph["nets"]))
    # The pre-existing component must not have been merged onto the new net
    r9 = next(n for n in live_graph["nodes"] if n["id"] == "R9")
    assert r9["pins"]["a"] == "n2"


def test_delete_node_cleans_up_dangling_edges_and_nets():
    path = os.path.join(SCHEMATICS_DIR, "test_regr_delete.vcg.json")
    assert post_action("load_schematic", {"path": path}).json()["status"] == "ok"

    for _ in range(2):
        assert post_action("place_component", {"path": path, "type": "resistor", "params": {}}).json()["status"] == "ok"
    assert post_action("wire_pins", {
        "path": path,
        "from": {"node_id": "R1", "pin": "a"},
        "to": {"node_id": "R2", "pin": "a"}
    }).json()["status"] == "ok"

    live_graph = active_schematics[path]
    assert len(live_graph["edges"]) == 1

    # Deleting one endpoint keeps the net alive through the other pin
    assert post_action("delete_node", {"path": path, "id": "R2"}).json()["status"] == "ok"
    assert [n["id"] for n in live_graph["nodes"]] == ["R1"]

    # Deleting the second endpoint must drop the dangling edge and net
    assert post_action("delete_node", {"path": path, "id": "R1"}).json()["status"] == "ok"
    assert live_graph["nodes"] == []
    assert live_graph["edges"] == []
    assert live_graph["nets"] == ["n0"]

    is_valid, msg = NativeGraphValidator.validate(live_graph)
    assert is_valid, msg


def test_solve_dc_returns_branch_current_indices():
    # 10V across 2k total -> 5mA through the source branch
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n2"}},
        {"id": "R2", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n2", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()

    assert "branch_V1" in cmap
    assert np.isclose(abs(x[cmap["branch_V1"]]), 0.005, atol=1e-5)


def test_drawio_edge_pin_mapping_uses_style_symbol():
    # The opamp's entry point (left side, lower half) must map to the
    # 'inverting' pin; resolving the component type from the cell label
    # ("U1") instead of the style symbol used to break this
    xml_str = """<mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="cell_r1" value="R1" style="symbol=resistor;R=1000" vertex="1" parent="1">
          <mxGeometry x="100" y="80" width="80" height="40" as="geometry"/>
        </mxCell>
        <mxCell id="cell_u1" value="U1" style="symbol=opamp" vertex="1" parent="1">
          <mxGeometry x="300" y="80" width="80" height="40" as="geometry"/>
        </mxCell>
        <mxCell id="edge_w1" value="" style="exitX=1;exitY=0.5;entryX=0;entryY=0.75;" edge="1" parent="1" source="cell_r1" target="cell_u1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>"""

    graph = DrawioCodec.xml_to_json(xml_str)
    is_valid, msg = NativeGraphValidator.validate(graph)
    assert is_valid, msg

    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["cell_u1"]["type"] == "opamp"
    shared_net = nodes["cell_r1"]["pins"]["b"]
    assert nodes["cell_u1"]["pins"]["inverting"] == shared_net
    assert shared_net != "n0"


def test_wire_pins_merges_two_existing_nets():
    # Wiring two pins that already belong to distinct nets makes them one
    # electrical node: every pin and wire on the second net must be
    # relabeled onto the first, and the second net must disappear
    path = os.path.join(SCHEMATICS_DIR, "test_regr_merge.vcg.json")
    assert post_action("load_schematic", {"path": path}).json()["status"] == "ok"

    for _ in range(3):
        assert post_action("place_component", {"path": path, "type": "resistor", "params": {}}).json()["status"] == "ok"

    def wire(from_node, from_pin, to_node, to_pin):
        res = post_action("wire_pins", {
            "path": path,
            "from": {"node_id": from_node, "pin": from_pin},
            "to": {"node_id": to_node, "pin": to_pin}
        }).json()
        assert res["status"] == "ok"
        return res["data"]["net"]

    net_a = wire("R1", "a", "R2", "a")   # New net
    net_b = wire("R2", "b", "R3", "a")   # Second, distinct net
    assert net_a != net_b

    merged = wire("R1", "a", "R2", "b")  # Joins the two nets
    assert merged == net_a

    live_graph = active_schematics[path]
    pins = {n["id"]: n["pins"] for n in live_graph["nodes"]}
    assert pins["R1"]["a"] == net_a
    assert pins["R2"]["a"] == net_a
    assert pins["R2"]["b"] == net_a
    assert pins["R3"]["a"] == net_a  # Relabeled from the absorbed net

    assert net_b not in live_graph["nets"]
    assert all(e["net"] == net_a for e in live_graph["edges"])

    is_valid, msg = NativeGraphValidator.validate(live_graph)
    assert is_valid, msg


def test_solver_rejects_nonpositive_component_values():
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 0.0}, "pins": {"a": "n1", "b": "n0"}}
    ]
    with pytest.raises(ValueError, match="R1.*positive"):
        ContinuousSolver(nodes, []).solve_dc()


def test_solve_transient_rejects_invalid_time_grid():
    nodes = [
        {"id": "V1", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n1", "b": "n0"}},
        {"id": "R1", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n1", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    with pytest.raises(ValueError, match="dt"):
        solver.solve_transient(0.0, 0.01, 0.0)
    with pytest.raises(ValueError, match="t_stop"):
        solver.solve_transient(0.01, 0.01, 1e-4)


def test_robots_disallow_rules_parsed_per_agent():
    scraper = DatasheetScraper()
    content = """
    # comment
    User-agent: OtherBot
    Disallow: /forbidden-for-others

    User-agent: *
    Disallow: /private
    Disallow: /internal
    """
    rules = scraper._parse_robots_disallow_rules(content)
    assert rules == ["/private", "/internal"]
