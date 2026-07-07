import xml.etree.ElementTree as ET
import math
from typing import Dict, Any, List, Tuple

class DrawioCodec:
    """
    Bi-directional serializer converting uncompressed draw.io mxGraphModel XML
    into native VCG JSON graph format and vice versa.
    """
    @staticmethod
    def _parse_style(style_str: str) -> Dict[str, str]:
        """Parses a semicolon-separated style string into a dictionary."""
        if not style_str:
            return {}
        style_dict = {}
        for item in style_str.split(";"):
            if not item.strip():
                continue
            if "=" in item:
                k, v = item.split("=", 1)
                style_dict[k.strip()] = v.strip()
            else:
                style_dict[item.strip()] = "true"
        return style_dict

    @staticmethod
    def _build_style(style_dict: Dict[str, str]) -> str:
        """Converts a style dictionary back to a semicolon-separated string."""
        return ";".join(f"{k}={v}" if v != "true" else k for k, v in style_dict.items())

    @staticmethod
    def _get_default_pins(node_type: str) -> List[str]:
        """Returns standard pins for each component type."""
        mapping = {
            "resistor": ["a", "b"],
            "capacitor": ["a", "b"],
            "inductor": ["a", "b"],
            "diode": ["anode", "cathode"],
            "opamp": ["non_inverting", "inverting", "out"],
            "voltage_source": ["a", "b"],
            "current_source": ["a", "b"],
            "nmos": ["gate", "drain", "source"],
            "pmos": ["gate", "drain", "source"],
            "bjt_npn": ["base", "collector", "emitter"],
            "bjt_pnp": ["base", "collector", "emitter"],
            "vcvs": ["p", "n", "cp", "cn"],
            "vccs": ["p", "n", "cp", "cn"],
            "cccs": ["p", "n"],
            "ccvs": ["p", "n"]
        }
        return mapping.get(node_type, ["a", "b"])

    @staticmethod
    def _infer_cell_type(cell) -> str:
        """
        Resolves a cell's component type from its style 'symbol' entry,
        falling back to a heuristic on the cell's value label.
        """
        style_dict = DrawioCodec._parse_style(cell.attrib.get("style", ""))
        node_type = style_dict.get("symbol")
        if node_type:
            return node_type

        val = cell.attrib.get("value", "").lower()
        if "resistor" in val or "r" in val:
            return "resistor"
        elif "capacitor" in val or "c" in val:
            return "capacitor"
        elif "inductor" in val or "l" in val:
            return "inductor"
        elif "diode" in val or "d" in val:
            return "diode"
        elif "opamp" in val or "u" in val:
            return "opamp"
        return "resistor"  # Default

    @staticmethod
    def _map_point_to_pin(node_type: str, rel_x: float, rel_y: float) -> str:
        """Maps an entry/exit point on a draw.io cell to a specific pin name."""
        pins = DrawioCodec._get_default_pins(node_type)
        if node_type == "opamp":
            if rel_x <= 0.1:
                return "inverting" if rel_y >= 0.5 else "non_inverting"
            return "out"
        elif node_type in ("nmos", "pmos", "bjt_npn", "bjt_pnp"):
            # Control pin on the left; drain/collector upper right,
            # source/emitter lower right
            if rel_x <= 0.1:
                return pins[0]
            return pins[1] if rel_y < 0.5 else pins[2]
        elif node_type in ("vcvs", "vccs"):
            # Control pair on the left, output pair on the right
            if rel_x <= 0.1:
                return "cp" if rel_y < 0.5 else "cn"
            return "p" if rel_y < 0.5 else "n"
        elif len(pins) == 2:
            # Typically terminal 'a' is left/top, 'b' is right/bottom
            if rel_x <= 0.3 or rel_y <= 0.3:
                return pins[0]
            return pins[1]
        return pins[0]

    @staticmethod
    def xml_to_json(xml_content: str) -> Dict[str, Any]:
        """
        Parses Draw.io XML cells and returns a valid native VCG JSON graph document.
        Uses Disjoint Set Union (DSU) to construct nets from edge connections.
        """
        root_el = ET.fromstring(xml_content)
        
        # We find <mxCell> tags
        cells = root_el.findall(".//mxCell")
        
        nodes_list = []
        edges_list = []
        
        # Temporary structures
        cell_by_id = {}
        for cell in cells:
            cell_id = cell.attrib.get("id")
            if cell_id:
                cell_by_id[cell_id] = cell

        # Disjoint Set Union for Net grouping
        # Each element in DSU is a tuple: (node_id, pin_name)
        parent_pin: Dict[Tuple[str, str], Tuple[str, str]] = {}

        def find_pin(p: Tuple[str, str]) -> Tuple[str, str]:
            if p not in parent_pin:
                parent_pin[p] = p
                return p
            path_pins = []
            while parent_pin[p] != p:
                path_pins.append(p)
                p = parent_pin[p]
            for gp in path_pins:
                parent_pin[gp] = p
            return p

        def union_pins(p1: Tuple[str, str], p2: Tuple[str, str]):
            root1 = find_pin(p1)
            root2 = find_pin(p2)
            if root1 != root2:
                parent_pin[root1] = root2

        # 1. Parse all vertices (Nodes)
        for cell in cells:
            cell_id = cell.attrib.get("id")
            if cell_id in ("0", "1"):
                continue  # Skip root layers
            
            is_vertex = cell.attrib.get("vertex") == "1"
            if not is_vertex:
                continue

            style_str = cell.attrib.get("style", "")
            style_dict = DrawioCodec._parse_style(style_str)

            # Identify node type
            node_type = DrawioCodec._infer_cell_type(cell)

            # Extract params from style or defaults
            params = {}
            for k, v in style_dict.items():
                if k not in ("symbol", "rot", "rotation", "edgeStyle"):
                    try:
                        params[k] = float(v)
                    except ValueError:
                        params[k] = v

            # Fill missing defaults
            if node_type == "resistor" and "R" not in params:
                params["R"] = 1000.0
            elif node_type == "capacitor" and "C" not in params:
                params["C"] = 1e-6
            elif node_type == "inductor" and "L" not in params:
                params["L"] = 1e-3
            elif node_type == "diode":
                if "Is" not in params:
                    params["Is"] = 1e-14
                if "N" not in params:
                    params["N"] = 1.0
                if "Vt" not in params:
                    params["Vt"] = 0.02585

            # Geometry
            geom = cell.find("mxGeometry")
            pos_x = 0.0
            pos_y = 0.0
            if geom is not None:
                pos_x = float(geom.attrib.get("x", 0.0))
                pos_y = float(geom.attrib.get("y", 0.0))

            rot = float(style_dict.get("rot", style_dict.get("rotation", 0.0)))

            node_obj = {
                "id": cell_id,
                "type": node_type,
                "params": params,
                "pos": {"x": pos_x, "y": pos_y},
                "rot": rot,
                "pins": {},  # Will populate after DSU
                "refs": {
                    "i_arrow": "a->b" if node_type in ("resistor", "capacitor", "inductor") else "anode->cathode",
                    "v_polarity": "a+,b-" if node_type in ("resistor", "capacitor", "inductor") else "anode+,cathode-"
                }
            }
            nodes_list.append(node_obj)

            # Initialize DSU pins for this node
            for pin in DrawioCodec._get_default_pins(node_type):
                find_pin((cell_id, pin))

        # 2. Parse all edges (Connections)
        for cell in cells:
            cell_id = cell.attrib.get("id")
            is_edge = cell.attrib.get("edge") == "1"
            if not is_edge:
                continue

            source_id = cell.attrib.get("source")
            target_id = cell.attrib.get("target")
            style_str = cell.attrib.get("style", "")
            style_dict = DrawioCodec._parse_style(style_str)

            # Geometry points
            geom = cell.find("mxGeometry")
            path_points = []
            
            # Start point
            source_cell = cell_by_id.get(source_id) if source_id else None
            source_type = DrawioCodec._infer_cell_type(source_cell) if source_cell is not None else "resistor"

            target_cell = cell_by_id.get(target_id) if target_id else None
            target_type = DrawioCodec._infer_cell_type(target_cell) if target_cell is not None else "resistor"

            # Parse entry/exit parameters
            exit_x = float(style_dict.get("exitX", 0.0))
            exit_y = float(style_dict.get("exitY", 0.5))
            entry_x = float(style_dict.get("entryX", 1.0))
            entry_y = float(style_dict.get("entryY", 0.5))

            source_pin = DrawioCodec._map_point_to_pin(source_type, exit_x, exit_y)
            target_pin = DrawioCodec._map_point_to_pin(target_type, entry_x, entry_y)

            # Union connected pins
            if source_id and target_id:
                union_pins((source_id, source_pin), (target_id, target_pin))

            # Reconstruct edge path coordinate list
            source_x, source_y = 0.0, 0.0
            if source_cell is not None:
                sg = source_cell.find("mxGeometry")
                if sg is not None:
                    source_x = float(sg.attrib.get("x", 0.0)) + float(sg.attrib.get("width", 80.0)) * exit_x
                    source_y = float(sg.attrib.get("y", 0.0)) + float(sg.attrib.get("height", 40.0)) * exit_y
            
            target_x, target_y = 100.0, 100.0
            if target_cell is not None:
                tg = target_cell.find("mxGeometry")
                if tg is not None:
                    target_x = float(tg.attrib.get("x", 0.0)) + float(tg.attrib.get("width", 80.0)) * entry_x
                    target_y = float(tg.attrib.get("y", 0.0)) + float(tg.attrib.get("height", 40.0)) * entry_y

            path_points.append([source_x, source_y])

            # Intermediate points
            if geom is not None:
                array = geom.find("Array")
                if array is not None:
                    for pt in array.findall("mxPoint"):
                        px = float(pt.attrib.get("x", 0.0))
                        py = float(pt.attrib.get("y", 0.0))
                        path_points.append([px, py])

            path_points.append([target_x, target_y])

            edges_list.append({
                "id": cell_id,
                "source_id": source_id,
                "source_pin": source_pin,
                "target_id": target_id,
                "target_pin": target_pin,
                "path": path_points
            })

        # 3. Formulate Net groups
        # Group pins by their DSU roots
        net_groups: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
        for (node_id, pin_name) in parent_pin:
            root_pin = find_pin((node_id, pin_name))
            if root_pin not in net_groups:
                net_groups[root_pin] = []
            net_groups[root_pin].append((node_id, pin_name))

        # Assign Net names: n0, n1, n2 ...
        nets_list = []
        pin_to_net_map: Dict[Tuple[str, str], str] = {}
        
        # Ground detection: if a net contains an ID starting with "GND" or "n0", name it "n0"
        # Otherwise name sequentially
        net_idx = 1
        has_gnd = False
        
        # Sort keys to ensure deterministic net indexing
        sorted_roots = sorted(net_groups.keys())
        
        # First pass to find GND
        gnd_root = None
        for root in sorted_roots:
            members = net_groups[root]
            is_gnd_net = any("gnd" in m[0].lower() or m[1] == "gnd" for m in members)
            if is_gnd_net:
                gnd_root = root
                break

        if gnd_root:
            nets_list.append("n0")
            for m in net_groups[gnd_root]:
                pin_to_net_map[m] = "n0"
            has_gnd = True

        for root in sorted_roots:
            if root == gnd_root:
                continue
            net_name = f"n{net_idx}"
            nets_list.append(net_name)
            for m in net_groups[root]:
                pin_to_net_map[m] = net_name
            net_idx += 1

        if not has_gnd:
            nets_list.insert(0, "n0")  # Always guarantee ground presence

        # 4. Map nets back to Node pins dictionary and Edge nets
        for node in nodes_list:
            n_id = node["id"]
            node_type = node["type"]
            for pin in DrawioCodec._get_default_pins(node_type):
                net_assigned = pin_to_net_map.get((n_id, pin), "n0")
                node["pins"][pin] = net_assigned

        # Map edges to nets
        final_edges = []
        for edge in edges_list:
            source_net = pin_to_net_map.get((edge["source_id"], edge["source_pin"]), "n0")
            final_edges.append({
                "id": edge["id"],
                "net": source_net,
                "path": edge["path"]
            })

        return {
            "schema_version": "1.0.0",
            "metadata": {
                "name": "Drawio Exported Schematic",
                "created_utc": "2026-05-23T20:38:00Z",
                "author_agent": "VoltCraft-Orchestrator"
            },
            "nodes": nodes_list,
            "edges": final_edges,
            "nets": nets_list
        }

    @staticmethod
    def json_to_xml(json_graph: Dict[str, Any]) -> str:
        """
        Converts VoltCraft native VCG JSON graph into an uncompressed Draw.io XML string.
        """
        # Enforce exact format XML
        mx_graph_model = ET.Element("mxGraphModel")
        root = ET.SubElement(mx_graph_model, "root")

        # Layer definition cells
        ET.SubElement(root, "mxCell", attrib={"id": "0"})
        ET.SubElement(root, "mxCell", attrib={"id": "1", "parent": "0"})

        # Map each node to a vertex cell
        for node in json_graph["nodes"]:
            node_id = node["id"]
            node_type = node["type"]
            pos = node.get("pos", {"x": 100.0, "y": 100.0})
            rot = node.get("rot", 0.0)

            # Build style string
            style_dict = {"symbol": node_type, "rot": str(int(rot))}
            for k, v in node["params"].items():
                style_dict[k] = str(v)
            style_str = DrawioCodec._build_style(style_dict)

            cell_attribs = {
                "id": node_id,
                "value": node_id,
                "style": style_str,
                "vertex": "1",
                "parent": "1"
            }
            cell = ET.SubElement(root, "mxCell", attrib=cell_attribs)
            
            # Geometry
            ET.SubElement(cell, "mxGeometry", attrib={
                "x": str(pos["x"]),
                "y": str(pos["y"]),
                "width": "80",
                "height": "40",
                "as": "geometry"
            })

        # Map edges to edge cells
        for edge in json_graph["edges"]:
            edge_id = edge["id"]
            path = edge.get("path", [])
            
            # Try to resolve source/target cells by proximity to start/end path points
            source_id = None
            target_id = None
            
            if len(path) >= 2:
                start_pt = path[0]
                end_pt = path[-1]
                
                min_start_dist = 999999.0
                min_end_dist = 999999.0
                
                for node in json_graph["nodes"]:
                    nx = node["pos"]["x"] + 40.0
                    ny = node["pos"]["y"] + 20.0
                    
                    dist_start = math.hypot(start_pt[0] - nx, start_pt[1] - ny)
                    dist_end = math.hypot(end_pt[0] - nx, end_pt[1] - ny)
                    
                    if dist_start < min_start_dist:
                        min_start_dist = dist_start
                        source_id = node["id"]
                    if dist_end < min_end_dist:
                        min_end_dist = dist_end
                        target_id = node["id"]

            style_attribs = {
                "edgeStyle": "orthogonalEdgeStyle",
                "rounded": "0",
                "orthogonalLoop": "1",
                "jettySize": "auto",
                "html": "1"
            }
            style_str = DrawioCodec._build_style(style_attribs)

            cell_attribs = {
                "id": edge_id,
                "value": "",
                "style": style_str,
                "edge": "1",
                "parent": "1"
            }
            if source_id:
                cell_attribs["source"] = source_id
            if target_id:
                cell_attribs["target"] = target_id

            cell = ET.SubElement(root, "mxCell", attrib=cell_attribs)
            
            # Geometry
            geom = ET.SubElement(cell, "mxGeometry", attrib={
                "relative": "1",
                "as": "geometry"
            })

            # Intermediate coordinates
            if len(path) > 2:
                arr = ET.SubElement(geom, "Array", attrib={"as": "points"})
                for pt in path[1:-1]:
                    ET.SubElement(arr, "mxPoint", attrib={
                        "x": str(pt[0]),
                        "y": str(pt[1])
                    })

        # Generate pretty string
        raw_xml = ET.tostring(mx_graph_model, encoding="utf-8")
        return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n{raw_xml.decode('utf-8')}"
