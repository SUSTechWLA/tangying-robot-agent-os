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


def run(base_url: str, output: Path, *, name="家庭地图", timeout=600.):
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
    status=service("mapping.start",{"mode":"survey","name":name})
    session=status["sessionId"]
    deadline=time.monotonic()+timeout
    try:
        with (output/"progress.jsonl").open("w") as log:
            while status["state"] not in {"completed","failed","cancelled"}:
                if time.monotonic()>deadline:raise TimeoutError("mapping deadline exceeded")
                time.sleep(1.)
                status=service("mapping.status")
                if status.get("sessionId")!=session:raise RuntimeError("mapping session identity changed")
                compact={k:v for k,v in status.items() if k not in {"preview","trajectory"}}
                log.write(json.dumps(compact,ensure_ascii=False)+"\n");log.flush()
                print(status["state"],status["frameCount"],"frames",round(status["travelledM"],2),"m",flush=True)
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
    args=parser.parse_args()
    result=run(args.base_url,args.output,name=args.name,timeout=args.timeout)
    print(f"地图 {result['mapId']} 已保存并启用。")
