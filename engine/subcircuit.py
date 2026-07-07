import json
from typing import Any, Callable, Dict, List


class SubcircuitError(ValueError):
    """Raised for structural problems in subcircuit definitions or instances."""


def _clone(obj: Any) -> Any:
    """Deep-copies plain JSON data."""
    return json.loads(json.dumps(obj))


def flatten_subcircuits(graph: Dict[str, Any],
                        load_definition: Callable[[str], Dict[str, Any]],
                        max_depth: int = 8) -> Dict[str, Any]:
    """
    Returns a new graph with every 'subcircuit' instance expanded inline.

    A subcircuit definition is an ordinary VCG graph carrying a top-level
    "ports" list naming the nets it exposes. An instance is a node of type
    "subcircuit" whose params.ref names the definition (resolved by the
    load_definition callable) and whose pins map each port onto a parent
    net. During expansion:

    - internal components, wires, and nets are namespaced '<instance>.<name>'
    - port nets are replaced by the parent nets wired to the instance pins
    - 'n0' is the global ground and is never namespaced

    Definitions may nest further subcircuits; recursion beyond max_depth
    (a definition cycle) raises SubcircuitError.
    """
    return _flatten(graph, load_definition, max_depth, 0)


def _flatten(graph: Dict[str, Any], load_definition: Callable[[str], Dict[str, Any]],
             max_depth: int, depth: int) -> Dict[str, Any]:
    if depth > max_depth:
        raise SubcircuitError(
            f"Subcircuit nesting exceeds max depth {max_depth} - definition cycle?"
        )

    out_nodes: List[Dict[str, Any]] = []
    out_edges: List[Dict[str, Any]] = [_clone(e) for e in graph.get("edges", [])]
    nets: List[str] = list(graph["nets"])
    net_set = set(nets)

    for node in graph["nodes"]:
        if node["type"] != "subcircuit":
            out_nodes.append(_clone(node))
            continue

        inst_id = node["id"]
        ref = node.get("params", {}).get("ref")
        if not ref:
            raise SubcircuitError(f"Subcircuit instance '{inst_id}' is missing params.ref")

        sub = load_definition(ref)
        ports = sub.get("ports", [])
        if not ports:
            raise SubcircuitError(f"Subcircuit definition '{ref}' declares no ports")

        undeclared = [p for p in ports if p not in sub["nets"]]
        if undeclared:
            raise SubcircuitError(
                f"Subcircuit definition '{ref}' ports missing from its nets: {', '.join(undeclared)}"
            )

        pins = node.get("pins", {})
        missing = [p for p in ports if p not in pins]
        if missing:
            raise SubcircuitError(
                f"Instance '{inst_id}' leaves ports unconnected: {', '.join(missing)}"
            )

        # Expand nested subcircuits inside the definition first
        sub = _flatten(sub, load_definition, max_depth, depth + 1)

        # Map definition nets into the parent namespace
        net_map: Dict[str, str] = {"n0": "n0"}
        for port in ports:
            net_map[port] = pins[port]
        for net in sub["nets"]:
            if net not in net_map:
                scoped = f"{inst_id}.{net}"
                net_map[net] = scoped
                if scoped not in net_set:
                    nets.append(scoped)
                    net_set.add(scoped)

        for sub_node in sub["nodes"]:
            clone = _clone(sub_node)
            clone["id"] = f"{inst_id}.{sub_node['id']}"
            clone["pins"] = {
                pin: net_map.get(net, "n0")
                for pin, net in sub_node.get("pins", {}).items()
            }
            out_nodes.append(clone)

        for sub_edge in sub.get("edges", []):
            clone = _clone(sub_edge)
            clone["id"] = f"{inst_id}.{sub_edge['id']}"
            clone["net"] = net_map.get(sub_edge["net"], "n0")
            out_edges.append(clone)

    flat = dict(graph)
    flat["nodes"] = out_nodes
    flat["edges"] = out_edges
    flat["nets"] = nets
    return flat
