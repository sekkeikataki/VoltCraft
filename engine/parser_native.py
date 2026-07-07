from typing import Dict, Any, Tuple

class NativeGraphValidator:
    """
    Hand-rolled JSON schema validator that checks VoltCraft VCG schematic graphs.
    Enforces structure, types, and referential integrity without external libraries.
    """
    @staticmethod
    def validate(graph: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates the structure of the schematic graph JSON.
        Returns:
            (is_valid, message)
        """
        if not isinstance(graph, dict):
            return False, "Schematic must be a JSON object"

        # Check required top-level keys
        required_keys = ["schema_version", "metadata", "nodes", "edges", "nets"]
        for key in required_keys:
            if key not in graph:
                return False, f"Missing required top-level key: {key}"

        if not isinstance(graph["schema_version"], str):
            return False, "schema_version must be a string"

        if not isinstance(graph["metadata"], dict):
            return False, "metadata must be a dictionary"

        if not isinstance(graph["nets"], list):
            return False, "nets must be a list"

        for net in graph["nets"]:
            if not isinstance(net, str):
                return False, f"Net name must be a string: {net}"

        known_nets = set(graph["nets"])

        # Optional subcircuit port declarations
        if "ports" in graph:
            if not isinstance(graph["ports"], list):
                return False, "ports must be a list of net names"
            for port in graph["ports"]:
                if not isinstance(port, str):
                    return False, f"Port name must be a string: {port}"
                if port not in known_nets:
                    return False, f"Port '{port}' must reference a declared net"

        # Validate nodes
        if not isinstance(graph["nodes"], list):
            return False, "nodes must be a list"

        node_ids = set()
        for i, node in enumerate(graph["nodes"]):
            if not isinstance(node, dict):
                return False, f"Node at index {i} must be a dictionary"

            # Check node required keys
            node_reqs = ["id", "type", "params", "pos", "rot", "pins"]
            for req in node_reqs:
                if req not in node:
                    return False, f"Node at index {i} missing key: {req}"

            node_id = node["id"]
            if not isinstance(node_id, str) or not node_id:
                return False, f"Node at index {i} must have a non-empty string ID"

            if node_id in node_ids:
                return False, f"Duplicate node ID detected: {node_id}"
            node_ids.add(node_id)

            if not isinstance(node["type"], str) or not node["type"]:
                return False, f"Node '{node_id}' must have a non-empty string type"

            if not isinstance(node["params"], dict):
                return False, f"Node '{node_id}' params must be a dictionary"

            # Validate pos (x, y)
            pos = node["pos"]
            if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
                return False, f"Node '{node_id}' pos must be a dictionary with 'x' and 'y' keys"
            if not isinstance(pos["x"], (int, float)) or not isinstance(pos["y"], (int, float)):
                return False, f"Node '{node_id}' position coordinates must be numbers"

            # Validate rot (integer-like rotation in degrees)
            rot = node["rot"]
            if not isinstance(rot, (int, float)):
                return False, f"Node '{node_id}' rotation must be a number"

            # Validate pins
            pins = node["pins"]
            if not isinstance(pins, dict):
                return False, f"Node '{node_id}' pins must be a dictionary mapping pins to nets"

            for pin_name, net_name in pins.items():
                if not isinstance(pin_name, str):
                    return False, f"Node '{node_id}' pin name must be a string: {pin_name}"
                if not isinstance(net_name, str):
                    return False, f"Node '{node_id}' pin '{pin_name}' must be mapped to a string net name"
                if net_name not in known_nets:
                    return False, f"Node '{node_id}' pin '{pin_name}' is mapped to unregistered net: '{net_name}'"

            # Validate refs if present
            if "refs" in node:
                refs = node["refs"]
                if not isinstance(refs, dict):
                    return False, f"Node '{node_id}' refs must be a dictionary"

        # Validate edges
        if not isinstance(graph["edges"], list):
            return False, "edges must be a list"

        edge_ids = set()
        for i, edge in enumerate(graph["edges"]):
            if not isinstance(edge, dict):
                return False, f"Edge at index {i} must be a dictionary"

            edge_reqs = ["id", "net", "path"]
            for req in edge_reqs:
                if req not in edge:
                    return False, f"Edge at index {i} missing key: {req}"

            edge_id = edge["id"]
            if not isinstance(edge_id, str) or not edge_id:
                return False, f"Edge at index {i} must have a non-empty string ID"

            if edge_id in edge_ids:
                return False, f"Duplicate edge ID detected: {edge_id}"
            edge_ids.add(edge_id)

            edge_net = edge["net"]
            if not isinstance(edge_net, str) or edge_net not in known_nets:
                return False, f"Edge '{edge_id}' net '{edge_net}' must be defined in 'nets'"

            # Validate path list of points
            path = edge["path"]
            if not isinstance(path, list):
                return False, f"Edge '{edge_id}' path must be a list of coordinates"

            for j, point in enumerate(path):
                if not isinstance(point, list) or len(point) != 2:
                    return False, f"Edge '{edge_id}' path point {j} must be a [x, y] list"
                if not isinstance(point[0], (int, float)) or not isinstance(point[1], (int, float)):
                    return False, f"Edge '{edge_id}' path point {j} coordinates must be numbers"

        return True, "OK"
