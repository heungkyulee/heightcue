import json
import os
import sys
sys.path.insert(0, os.path.join(os.getcwd(), "autopilot"))

from common import read_json, BASE
from generate import make_value_post

def run_test():
    cfg = read_json(os.path.join(BASE, "config.json"), {})
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/.hermes/.env"))
    
    real_key = os.getenv("OPENROUTER_API_KEY")
    if real_key:
        if "openrouter" not in cfg:
            cfg["openrouter"] = {}
        cfg["openrouter"]["api_key"] = real_key

    angles = ["rant", "shower_thought", "raw_memory", "myth_bust", "community_qa"]
    
    print("=== LIVE GENERATION TEST: 5 ANGLES (KR) ===\n")
    for angle in angles:
        try:
            print(f"--- 앵글: {angle} ---")
            val = make_value_post(cfg, kind="story", topic="영양제", dry_run=False, country="KR", angle_override=angle)
            print(val.get("text", val))
            print("-" * 40 + "\n")
        except Exception as e:
            print(f"Error {angle}:", e)

if __name__ == "__main__":
    run_test()
