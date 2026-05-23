import pytest
import os
import json
from fastapi.testclient import TestClient
from voltcraft.app import app, SCHEMATICS_DIR, JOURNAL_DIR

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup_files():
    # Run before each test
    yield
    # Cleanup generated schematics or journals if needed
    test_file = os.path.join(SCHEMATICS_DIR, "test_api_circuit.vcg.json")
    if os.path.exists(test_file):
        try:
            os.remove(test_file)
        except Exception:
            pass

def test_api_load_and_save_schematic():
    # 1. Test load schematic (will auto-create blank)
    path = os.path.join(SCHEMATICS_DIR, "test_api_circuit.vcg.json")
    payload = {
        "action": "load_schematic",
        "agent_id": "Designer",
        "params": {"path": path}
    }
    
    response = client.post("/api/agent/action", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "ok"
    assert res_data["data"]["schema_version"] == "1.0.0"
    
    # 2. Place component
    payload_place = {
        "action": "place_component",
        "agent_id": "Designer",
        "params": {
            "path": path,
            "type": "resistor",
            "params": {"R": 2200.0},
            "pos": {"x": 150.0, "y": 150.0},
            "rot": 0.0
        }
    }
    response_place = client.post("/api/agent/action", json=payload_place)
    assert response_place.status_code == 200
    res_place = response_place.json()
    assert res_place["status"] == "ok"
    node_id = res_place["data"]["node_id"]
    assert node_id.startswith("R")
    
    # 3. Save schematic
    # Retrieve current active graph state
    graph_state = res_data["data"]
    if "n1" not in graph_state["nets"]:
        graph_state["nets"].append("n1")
    graph_state["nodes"].append({
        "id": node_id,
        "type": "resistor",
        "params": {"R": 2200.0},
        "pos": {"x": 150.0, "y": 150.0},
        "rot": 0.0,
        "pins": {"a": "n1", "b": "n0"}
    })
    
    payload_save = {
        "action": "save_schematic",
        "agent_id": "Designer",
        "params": {
            "path": path,
            "graph": graph_state
        }
    }
    response_save = client.post("/api/agent/action", json=payload_save)
    assert response_save.status_code == 200
    res_save = response_save.json()
    assert res_save["status"] == "ok"
    assert res_save["data"]["ok"] is True
    
    # 4. Check that mutations NDJSON was appended successfully
    journal_path = os.path.join(JOURNAL_DIR, "mutations.ndjson")
    assert os.path.exists(journal_path)
    with open(journal_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) > 0
        last_entry = json.loads(lines[-1].strip())
        assert last_entry["agent_id"] == "Designer"

def test_api_query_wiki():
    payload = {
        "action": "query_wiki",
        "params": {"term": "resistor"}
    }
    response = client.post("/api/agent/action", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "ok"
    assert "frontmatter" in res_data["data"]
    assert "markdown" in res_data["data"]
    assert res_data["data"]["frontmatter"]["family"] == "resistor"

def test_api_web_search():
    payload = {
        "action": "web_search",
        "params": {"query": "1N4148"}
    }
    response = client.post("/api/agent/action", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "ok"
    assert res_data["data"]["part_number"] == "1N4148"
    assert res_data["data"]["params"]["Is"] == 2.68e-9

def test_api_simulation():
    # Load divider reference circuit
    path = os.path.join(SCHEMATICS_DIR, "test_api_circuit.vcg.json")
    
    # Load Divider blank schematic first
    client.post("/api/agent/action", json={
        "action": "load_schematic",
        "params": {"path": path}
    })
    
    # Place elements for a divider
    client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "voltage_source", "params": {"V": 10.0}}
    })
    client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "resistor", "params": {"R": 1000.0}}
    })
    client.post("/api/agent/action", json={
        "action": "place_component",
        "params": {"path": path, "type": "resistor", "params": {"R": 2000.0}}
    })
    
    # Setup correct netlist wiring (Vin=10V, R1=1k, R2=2k)
    # nodes are V1, R1, R2
    # Connect V1.a=n1, V1.b=n0; R1.a=n1, R1.b=n2; R2.a=n2, R2.b=n0
    payload_wire1 = {
        "action": "wire_pins",
        "params": {
            "path": path,
            "from": {"node_id": "V1", "pin": "a"},
            "to": {"node_id": "R1", "pin": "a"}
        }
    }
    client.post("/api/agent/action", json=payload_wire1)
    
    payload_wire2 = {
        "action": "wire_pins",
        "params": {
            "path": path,
            "from": {"node_id": "R1", "pin": "b"},
            "to": {"node_id": "R2", "pin": "a"}
        }
    }
    client.post("/api/agent/action", json=payload_wire2)
    
    # Run DC simulation
    payload_sim = {
        "action": "run_simulation",
        "params": {
            "path": path,
            "mode": "dc",
            "params": {}
        }
    }
    response_sim = client.post("/api/agent/action", json=payload_sim)
    assert response_sim.status_code == 200
    res_sim = response_sim.json()
    assert res_sim["status"] == "ok"
    
    cmap = res_sim["data"]["cmap"]
    x = res_sim["data"]["x"]
    
    idx_n1 = cmap["n1"]
    idx_n2 = cmap["n2"]
    
    assert abs(x[idx_n1] - 10.0) < 1e-3
    # Expected mid node: 10 * (2000 / 3000) = 6.666V
    assert abs(x[idx_n2] - 6.666) < 1e-2
