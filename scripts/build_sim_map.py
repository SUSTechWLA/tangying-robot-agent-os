#!/usr/bin/env python3
"""Drive a registered mapping service and preserve the measured result.

The historical command assigned qpos in a second world. This version uses the
running robot's ordinary service API, motion admission, live RGB-D SLAM and
immutable map publication.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def run(base_url: str, output: Path, *, name="家庭地图", timeout=600., mode="survey",
        base_map_id="", max_travel_m=0., max_legs=0):
    parsed=urlsplit(base_url)
    if parsed.scheme!="http" or parsed.hostname not in {"localhost","127.0.0.1"} or parsed.path not in {"","/"} or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("use a local robot console HTTP origin")
    if not math.isfinite(timeout) or not 1<=timeout<=1800:
        raise ValueError("timeout must be 1..1800 seconds")
    base_url=base_url.rstrip("/")
    def api(path,body=None):
        request=Request(base_url+path,data=None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
        try:
            with urlopen(request,timeout=20) as response:return json.load(response)
        except HTTPError as error:
            raise RuntimeError(error.read().decode()) from error
    def service(method,parameters=None):
        result=api("/v1/robot/services",{"name":method,"requestId":uuid.uuid4().hex,"parameters":parameters or {}})
        if not result.get("ok"):raise RuntimeError(f"{result.get('code')}: {result.get('message')}")
        return result["result"]
    catalog=api("/v1/robot/services")
    if "mapping.start" not in {s["name"] for s in catalog["services"] if s.get("available")}:
        raise RuntimeError("robot has no registered mapping service")
    output.mkdir(parents=True,exist_ok=False)
    parameters={"mode":mode,"name":name}
    if base_map_id:parameters["baseMapId"]=base_map_id
    if max_travel_m:parameters["maxTravelM"]=max_travel_m
    if max_legs:parameters["maxLegs"]=max_legs
    status=service("mapping.start",parameters)
    session=status["sessionId"]
    deadline=time.monotonic()+timeout
    try:
        with (output/"progress.jsonl").open("w") as log:
            while status["state"] not in {"completed","failed","cancelled"}:
                if time.monotonic()>deadline:raise TimeoutError("mapping deadline exceeded")
                time.sleep(1.)
                status=service("mapping.status")
                if status.get("sessionId") and status["sessionId"]!=session:
                    # An automatic survey publishes a leg and continues from it
                    # under a new session id. That is the feature, not a hijack;
                    # the leg boundary is recorded so the run stays auditable.
                    log.write(json.dumps({"event":"session_changed","from":session,
                                          "to":status["sessionId"]},ensure_ascii=False)+"\n")
                    session=status["sessionId"]
                compact={k:v for k,v in status.items() if k not in {"preview","trajectory"}}
                log.write(json.dumps(compact,ensure_ascii=False)+"\n");log.flush()
                exploration=status.get("exploration") or {}
                detail=("" if not exploration else
                        f"  已探明 {1-exploration.get('unknownFraction',1):.0%}"
                        f"  {exploration.get('stopReason','')}")
                print(status["state"],status["frameCount"],"frames",
                      round(status["travelledM"],2),"m",detail,flush=True)
        if status["state"]!="completed":raise RuntimeError(status["message"])
        manifest=api("/v1/maps/"+status["mapId"])
        (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
        (output/"summary.json").write_text(json.dumps({k:v for k,v in status.items() if k!="preview"},ensure_ascii=False,indent=2)+"\n")
        return status
    except BaseException:
        service("mapping.cancel")
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url",default="http://127.0.0.1:8787")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--name",default="家庭地图")
    parser.add_argument("--timeout",type=float,default=600.)
    parser.add_argument("--mode",default="survey",choices=("survey","explore"),
        help="survey follows the commissioned route; explore drives at unknown space")
    parser.add_argument("--base-map-id",default="",
        help="extend this saved map instead of starting a new one")
    parser.add_argument("--max-travel-m",type=float,default=0.,
        help="explore only: travel budget for the whole run")
    parser.add_argument("--max-legs",type=int,default=0,
        help="explore only: sessions to chain before stopping")
    args=parser.parse_args()
    result=run(args.base_url,args.output,name=args.name,timeout=args.timeout,mode=args.mode,
        base_map_id=args.base_map_id,max_travel_m=args.max_travel_m,max_legs=args.max_legs)
    print(f"地图 {result['mapId']} 已保存并启用。")
