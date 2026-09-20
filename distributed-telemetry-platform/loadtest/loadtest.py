"""Standard-library concurrent ingestion load test; measures HTTP acceptance only."""
import argparse
import concurrent.futures
import json
import math
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--url",default="http://localhost:8000/ingest")
    p.add_argument("--requests",type=int,default=1000)
    p.add_argument("--concurrency",type=int,default=20)
    p.add_argument("--output",default="loadtest/report.md")
    a=p.parse_args()
    if a.requests<1 or a.concurrency<1: p.error("requests and concurrency must be positive")
    run=str(uuid.uuid4())
    def send(i):
        data=json.dumps({"device_id":f"load-{run}-{i}","event_type":"sample","payload":{"value":i}}).encode()
        start=time.perf_counter()
        try:
            with urllib.request.urlopen(urllib.request.Request(a.url,data=data,headers={"Content-Type":"application/json"}),timeout=15) as res:
                ok=res.status==202
        except Exception:
            ok=False
        return (time.perf_counter()-start)*1000,ok
    start=time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        results=list(pool.map(send,range(a.requests)))
    elapsed=time.perf_counter()-start
    times=sorted(ms for ms,ok in results if ok)
    errors=sum(not ok for _,ok in results)
    lines=["# Ingestion load test", "", f"Requests: {a.requests}; concurrency: {a.concurrency}",f"Elapsed: {elapsed:.2f}s",f"Requests/s: {a.requests/elapsed:.2f}",f"Error rate: {errors/a.requests:.2%}","", "Successful request latency:"]
    for pct in [50,90,95,99]:
        value=f"{times[max(0,math.ceil(len(times)*pct/100)-1)]:.2f} ms" if times else "N/A"
        lines.append(f"- p{pct}: {value}")
    lines.extend(["", "Measures queue acceptance, not completed worker processing."])
    report="\n".join(lines)+"\n"
    target=Path(a.output);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(report);print(report)
    if errors: raise SystemExit(1)

if __name__=="__main__": main()
