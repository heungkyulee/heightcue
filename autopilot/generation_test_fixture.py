#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin:
    req=json.loads(line)
    task=req["task"]
    log=os.environ.get("HEIGHTCUE_TEST_FIXTURE_LOG")
    if log:
        with open(log,"a") as f: f.write(json.dumps(req,ensure_ascii=False)+"\n")
    mode=os.environ.get("HEIGHTCUE_TEST_FIXTURE_MODE")
    if req["phase"]=="critic":
        out={"scores":[
            {"id":"c1","score":1,"disqualified":False,"reason":"grounded"},
            {"id":"c2","score":2,"disqualified":False,"reason":"grounded"}]}
        if mode=="bad-critic": out={"scores":[{"id":"wrong","score":"nan"}]}
    elif mode=="bad-writer": out={"unexpected":True}
    elif task=="sales_master": out={"hooks":["h1"],"verified_points":["fact"]}
    elif task=="sales_hooks": out={"hooks":[f"h{i}" for i in range(1,7)]}
    elif task=="value_thread": out={"parts":["1편","2편"]}
    elif task=="comment_reply": out={"category":"empathy","action":"reply","text":"답글","reason":"근거"}
    elif task in ("sales_post", "value_post"):
        if mode == "us-sales":
            out={"candidates":[{"id":"c1","text":"#ad\n600 IU vitamin D3 per labeled drop.\n\nSkip if: the exact label or fractionated coconut oil does not fit.\n\nFull breakdown and current listing: https://heightcue.lifoli.co.kr/us/vitamin-d-drops.html (paid link)"}, {"id":"c2","text":"#ad\nA label-sized fact: 600 IU vitamin D3 per labeled drop.\n\nSkip if: the exact label or fractionated coconut oil does not fit.\n\nFull breakdown and current listing: https://heightcue.lifoli.co.kr/us/vitamin-d-drops.html (paid link)"}]}
        else:
            out={"candidates":[{"id":"c1","text":"후보 하나"},{"id":"c2","text":"후보 둘"}]}
    print(json.dumps(out,ensure_ascii=False),flush=True)
