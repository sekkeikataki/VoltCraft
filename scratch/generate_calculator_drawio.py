import json
import os
from voltcraft.engine.parser_native import NativeGraphValidator
from voltcraft.engine.parser_drawio import DrawioCodec

def main():
    json_path = "voltcraft/storage/schematics/calculator.vcg.json"
    xml_path = "voltcraft/storage/schematics/calculator.drawio.xml"
    
    print(f"[VOLTCRAFT] Loading calculator schematic: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
        
    print("[VOLTCRAFT] Running structural graph validations...")
    is_valid, msg = NativeGraphValidator.validate(graph)
    if not is_valid:
        print(f"[VOLTCRAFT] VALIDATION ERROR: {msg}")
        return
    print("[VOLTCRAFT] VALIDATION PASSED.")
    
    print("[VOLTCRAFT] Serializing graph to Drawio XML format...")
    xml_data = DrawioCodec.json_to_xml(graph)
    
    print(f"[VOLTCRAFT] Writing XML sheet companion: {xml_path}")
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_data)
        
    print("[VOLTCRAFT] COMPLETED SUCCESS.")

if __name__ == "__main__":
    main()
