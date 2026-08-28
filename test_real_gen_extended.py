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

    # 1. KR Diverse Topics
    kr_test_cases = [
        {"angle": "rant", "topic": "성장판 주사 마케팅"},
        {"angle": "shower_thought", "topic": "자세 교정 의자"},
        {"angle": "raw_memory", "topic": "우유 억지로 먹던 기억"},
        {"angle": "community_qa", "topic": "줄넘기하면 진짜 크는지"}
    ]
    
    print("=== LIVE GENERATION TEST: KR DIVERSE TOPICS ===\n")
    for case in kr_test_cases:
        try:
            print(f"--- [KR] 앵글: {case['angle']} / 주제: {case['topic']} ---")
            val = make_value_post(cfg, kind="story", topic=case["topic"], dry_run=False, country="KR", angle_override=case["angle"])
            print(val.get("text", val))
            print("-" * 50 + "\n")
        except Exception as e:
            print(f"Error {case['angle']}:", e)

    # 2. US Diverse Topics
    us_test_cases = [
        {"angle": "rant", "topic": "Picky eaters and expensive vitamin gummies"},
        {"angle": "myth_bust", "topic": "Posture correctors for kids"},
        {"angle": "raw_memory", "topic": "Being the shortest kid at school dances"},
        {"angle": "shower_thought", "topic": "Genetics vs Supplements"}
    ]

    print("=== LIVE GENERATION TEST: US DIVERSE TOPICS ===\n")
    for case in us_test_cases:
        try:
            print(f"--- [US] 앵글: {case['angle']} / 주제: {case['topic']} ---")
            val = make_value_post(cfg, kind="story", topic=case["topic"], dry_run=False, country="US", angle_override=case["angle"])
            print(val.get("text", val))
            print("-" * 50 + "\n")
        except Exception as e:
            print(f"Error {case['angle']}:", e)

if __name__ == "__main__":
    run_test()
