import pytest
from voltcraft.engine.parser_native import NativeGraphValidator
from voltcraft.engine.parser_drawio import DrawioCodec

@pytest.fixture
def valid_vcg_graph():
    return {
        "schema_version": "1.0.0",
        "metadata": {
            "name": "Reference RLC Test Circuit",
            "created_utc": "2026-05-23T20:38:00Z",
            "author_agent": "VoltCraft-Orchestrator"
        },
        "nodes": [
            {
                "id": "R1",
                "type": "resistor",
                "params": {"R": 1000.0, "tol": 0.01},
                "pos": {"x": 120.0, "y": 80.0},
                "rot": 90,
                "pins": {"a": "n1", "b": "n2"},
                "refs": {"i_arrow": "a->b", "v_polarity": "a+,b-"}
            },
            {
                "id": "C1",
                "type": "capacitor",
                "params": {"C": 1e-6},
                "pos": {"x": 220.0, "y": 80.0},
                "rot": 0,
                "pins": {"a": "n2", "b": "n0"},
                "refs": {"i_arrow": "a->b", "v_polarity": "a+,b-"}
            }
        ],
        "edges": [
            {
                "id": "w1",
                "net": "n1",
                "path": [[120.0, 80.0], [200.0, 80.0]]
            },
            {
                "id": "w2",
                "net": "n2",
                "path": [[200.0, 80.0], [220.0, 80.0]]
            }
        ],
        "nets": ["n0", "n1", "n2"]
    }

def test_native_validator_valid(valid_vcg_graph):
    is_valid, msg = NativeGraphValidator.validate(valid_vcg_graph)
    assert is_valid, f"Validator failed on valid graph: {msg}"

def test_native_validator_missing_key(valid_vcg_graph):
    graph = valid_vcg_graph.copy()
    del graph["nets"]
    is_valid, msg = NativeGraphValidator.validate(graph)
    assert not is_valid
    assert "nets" in msg

def test_native_validator_invalid_types(valid_vcg_graph):
    graph = valid_vcg_graph.copy()
    graph["nets"] = "not-a-list"
    is_valid, msg = NativeGraphValidator.validate(graph)
    assert not is_valid
    assert "list" in msg

def test_native_validator_invalid_coordinate(valid_vcg_graph):
    graph = valid_vcg_graph.copy()
    graph["nodes"] = [
        {
            "id": "R1",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pos": {"x": "invalid-string", "y": 80.0},
            "rot": 0,
            "pins": {"a": "n1", "b": "n2"}
        }
    ]
    is_valid, msg = NativeGraphValidator.validate(graph)
    assert not is_valid
    assert "coordinates" in msg or "pos" in msg

def test_drawio_codec_xml_to_json():
    xml_str = """<mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="cell_r1" value="R1" style="symbol=resistor;rot=90;R=1500" vertex="1" parent="1">
          <mxGeometry x="120" y="80" width="80" height="40" as="geometry"/>
        </mxCell>
        <mxCell id="cell_c1" value="C1" style="symbol=capacitor;rot=0;C=0.000002" vertex="1" parent="1">
          <mxGeometry x="220" y="80" width="80" height="40" as="geometry"/>
        </mxCell>
        <mxCell id="edge_w1" value="" style="edgeStyle=orthogonalEdgeStyle;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="cell_r1" target="cell_c1">
          <mxGeometry relative="1" as="geometry">
            <Array as="points">
              <mxPoint x="200" y="100"/>
            </Array>
          </mxGeometry>
        </mxCell>
      </root>
    </mxGraphModel>"""
    
    graph_json = DrawioCodec.xml_to_json(xml_str)
    
    # Check validator
    is_valid, msg = NativeGraphValidator.validate(graph_json)
    assert is_valid, f"Codec produced invalid VCG JSON: {msg}"
    
    # Check node details
    nodes = {n["id"]: n for n in graph_json["nodes"]}
    assert "cell_r1" in nodes
    assert "cell_c1" in nodes
    assert nodes["cell_r1"]["type"] == "resistor"
    assert nodes["cell_r1"]["params"]["R"] == 1500.0
    assert nodes["cell_c1"]["params"]["C"] == 2e-6
    
    # Wires and nets mapping
    assert len(graph_json["nets"]) >= 2
    r1_pin_b = nodes["cell_r1"]["pins"]["b"]
    c1_pin_a = nodes["cell_c1"]["pins"]["a"]
    assert r1_pin_b == c1_pin_a

def test_drawio_codec_roundtrip(valid_vcg_graph):
    # JSON -> XML
    xml_str = DrawioCodec.json_to_xml(valid_vcg_graph)
    
    # XML -> JSON
    reconstructed_json = DrawioCodec.xml_to_json(xml_str)
    
    # Validate conservation
    is_valid, msg = NativeGraphValidator.validate(reconstructed_json)
    assert is_valid, f"Roundtrip yielded invalid VCG JSON: {msg}"
    
    orig_nodes = {n["id"]: n for n in valid_vcg_graph["nodes"]}
    recon_nodes = {n["id"]: n for n in reconstructed_json["nodes"]}
    
    assert set(orig_nodes.keys()) == set(recon_nodes.keys())
    for nid in orig_nodes:
        assert orig_nodes[nid]["type"] == recon_nodes[nid]["type"]
        assert orig_nodes[nid]["pos"]["x"] == recon_nodes[nid]["pos"]["x"]
        assert orig_nodes[nid]["pos"]["y"] == recon_nodes[nid]["pos"]["y"]
