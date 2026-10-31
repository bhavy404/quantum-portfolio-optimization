#!/usr/bin/env python3
"""Exercise live BB84 with 10-15 throwaway accounts against a running QPortal.

Generated test accounts are used by default. For classmates, pass a CSV containing
email,password,name columns. Passwords must be throwaway demo passwords only.
The script never writes passwords, proof values, keys, or measurement bits to disk.
"""
import argparse, csv, hashlib, hmac, json, secrets, statistics, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

N = 1600  # informational; the real value is read from the server
INFO = b"qportal-bb84-live-v1"
PREFIX = "qportal-live-login-v1"

def request(base, path, method="GET", payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type":"application/json"}
    if token: headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base.rstrip("/")+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=60) as r:
            raw=r.read(); return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw=e.read()
        try: body=json.loads(raw)
        except Exception: body=raw.decode(errors="replace")
        return e.code, body

def download_audit(base, token):
    req=urllib.request.Request(base.rstrip("/")+"/api/bb84/audit.csv",headers={"Authorization":"Bearer "+token})
    with urllib.request.urlopen(req,timeout=30) as response:
        return list(csv.DictReader(response.read().decode("utf-8-sig").splitlines()))

def exchange(base, eve, account):
    group_id=secrets.token_hex(16); attempts=[]; clean_rejected=0
    for no in range(1,4):
        attack=bool(eve and no==1)
        code,start=request(base,"/api/bb84/start","POST",{"eve_on":attack,"attempt":no,"account":account,"group_id":group_id})
        if code!=200: raise RuntimeError(f"BB84 start failed: {start}")
        n=start["qubits"]; bases=[secrets.randbits(1) for _ in range(n)]
        code,sift=request(base,"/api/bb84/sift","POST",{"session_id":start["session_id"],"bob_bases":bases,"account":account})
        if code!=200: raise RuntimeError(f"BB84 sift failed: {sift}")
        bob=sift["bob_bits"]; demo_rows=sift["demo_rows"]
        kept=[i for i,(a,b) in enumerate(zip(sift["alice_bases"],bases)) if a==b]
        sample_set=set(sift["sample_positions"])
        code,result=request(base,"/api/bb84/sample","POST",{"session_id":start["session_id"],"bob_sample_bits":[bob[i] for i in sift["sample_positions"]],"account":account})
        if code!=200: raise RuntimeError(f"BB84 sample failed: {result}")
        attempts.append({"number":no,"eve":attack,"verdict":result["verdict"],"qber":result["error_rate"],
                         "upper":result["upper_bound"],"sifted":result["sifted_bits"],"sample":result["sample_size"],
                         "session_id":start["session_id"],"key":None})
        if not attack and result["verdict"]!="secure": clean_rejected+=1
        if result["verdict"]=="secure":
            secret=[bob[i] for i in kept if i not in sample_set and i>=demo_rows]
            raw=bytes(secret)
            key=HKDF(algorithm=hashes.SHA256(),length=32,salt=start["session_id"].encode(),info=INFO).derive(raw)
            fp=hashlib.sha256(key).hexdigest()[:20].upper()
            fp="-".join(fp[i:i+4] for i in range(0,len(fp),4))
            if fp!=result["fingerprint"]: raise AssertionError("Browser/server key fingerprints differ")
            attempts[-1]["key"]=key
            return {"session_id":start["session_id"],"key":key,"fingerprint":fp,"attempts":attempts,
                    "clean_rejected":clean_rejected,"group_id":group_id}
    raise RuntimeError("No clean BB84 key after three attempts")

def proof_for(key,sid,email,mode):
    msg=f"{PREFIX}|{mode}|{sid}|{email.strip().lower()}".encode()
    return hmac.new(key,msg,hashlib.sha256).hexdigest()

def auth_request(base, endpoint, email, password, name, eve=False):
    mode = "signup" if endpoint == "register" else "login"
    t0=time.perf_counter(); hs=exchange(base, eve, email); proof=proof_for(hs["key"],hs["session_id"],email,mode)
    payload={"email":email,"password":password,"name":name,"session_id":hs["session_id"],"proof":proof}
    code,out=request(base,"/api/auth/"+endpoint,"POST",payload)
    elapsed=(time.perf_counter()-t0)*1000
    if code!=200: raise RuntimeError(f"{endpoint} failed for {email}: {code} {out}")
    return out,hs,elapsed

def load_accounts(path,count):
    if path:
        with open(path,newline="",encoding="utf-8-sig") as f: rows=list(csv.DictReader(f))
        if not all(r.get("email") and r.get("password") for r in rows): raise ValueError("Accounts CSV needs email,password columns")
        return [(r["email"].strip(),r["password"],r.get("name",f"Demo {i+1}")) for i,r in enumerate(rows)]
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return [(f"qportal-live-{stamp}-{i+1}@example.test","Qp-"+secrets.token_urlsafe(18)+"aA1!",f"BB84 Demo {i+1}") for i in range(count)]

def attack_checks(base,email,password,name):
    # Missing proof is rejected before a session can be consumed.
    code,_=request(base,"/api/auth/login","POST",{"email":email,"password":password})
    assert code==401, "login without proof was not rejected"
    # Wrong proof on session A is rejected without consuming that session.
    a=exchange(base,False,email); proof_a=proof_for(a["key"],a["session_id"],email,"login")
    payload={"email":email,"password":password,"session_id":a["session_id"],"proof":"0"*64}
    code,_=request(base,"/api/auth/login","POST",payload); assert code==401,"wrong proof was not rejected"
    # A proof from valid session A must not authenticate distinct valid session B.
    b=exchange(base,False,email); proof_b=proof_for(b["key"],b["session_id"],email,"login")
    payload["session_id"]=b["session_id"]; payload["proof"]=proof_a
    code,_=request(base,"/api/auth/login","POST",payload); assert code==401,"proof from another session was not rejected"
    # The matching proof for B succeeds once; replaying B is rejected.
    payload["proof"]=proof_b
    code,out=request(base,"/api/auth/login","POST",payload); assert code==200,f"valid proof after failed attempts failed: {out}"
    token=out["token"]
    code,_=request(base,"/api/me",token=token); assert code==200
    code,_=request(base,"/api/auth/login","POST",payload); assert code==401,"session replay was not rejected"
    return token

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--base-url",default="http://127.0.0.1:8000")
    ap.add_argument("--accounts",type=int,default=12)
    ap.add_argument("--accounts-file",help="CSV with email,password,name; use throwaway passwords only")
    ap.add_argument("--output-dir",default="load_test_output")
    args=ap.parse_args()
    if not args.accounts_file and not 10<=args.accounts<=15: ap.error("--accounts must be 10 to 15")
    users=load_accounts(args.accounts_file,args.accounts)
    if not 10<=len(users)<=15: ap.error("The account list must contain 10 to 15 people/accounts")
    code,health=request(args.base_url,"/api/health")
    if code!=200 or health.get("login_mode")!="live": raise RuntimeError("Start QPortal with QPORTAL_LOGIN_MODE=live before running this test")
    outdir=Path(args.output_dir); outdir.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows=[]; audit_rows=[]; login_ms=[]; login_count=0
    attack_accounts=0
    for idx,(email,password,name) in enumerate(users):
        registration_out,registration_hs,reg_ms=auth_request(args.base_url,"register",email,password,name,False)
        account_tokens=[registration_out["token"]]
        for login_no in range(1,4):
            t0=time.perf_counter(); out,hs,elapsed=auth_request(args.base_url,"login",email,password,name,login_no==1)
            login_ms.append(elapsed); login_count+=1
            token=out["token"]; account_tokens.append(token); code,_=request(args.base_url,"/api/me",token=token)
            if code!=200: raise AssertionError("Successful login token did not work")
            rows.append({"account":email,"action":"login","login_number":login_no,"eve_runs":sum(int(a["eve"]) for a in hs["attempts"]),
                "eve_detected":sum(int(a["eve"] and a["verdict"]=="eavesdropper_detected") for a in hs["attempts"]),
                "clean_false_rejects":sum(int((not a["eve"]) and a["verdict"]!="secure") for a in hs["attempts"]),
                "attempts":len(hs["attempts"]),"sifted_bits":hs["attempts"][-1]["sifted"],
                "sample_size":hs["attempts"][-1]["sample"],"error_rate":hs["attempts"][-1]["qber"],
                "median_latency_ms":round(elapsed,2)})
        if idx<3:
            account_tokens.append(attack_checks(args.base_url,email,password,name)); attack_accounts+=1
        # Pull this account's rows from the persisted audit table, not from the
        # in-memory load-test counters. The endpoint scopes rows to this account.
        audit_rows.extend(download_audit(args.base_url, account_tokens[-1]))
        for used_token in account_tokens:
            request(args.base_url,"/api/auth/logout","POST",{},used_token)
    audit_path=outdir/f"bb84_live_audit_{stamp}.csv"
    if audit_rows:
        with audit_path.open("w",newline="",encoding="utf-8") as f:
            fields=list(audit_rows[0]); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(audit_rows)
    login_path=outdir/f"bb84_live_logins_{stamp}.csv"
    with login_path.open("w",newline="",encoding="utf-8") as f:
        fields=list(rows[0]); w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
    eve_rows=[r for r in audit_rows if str(r["eve_on"]) in ("1","True","true")]
    eve_detected=sum(r["verdict"]=="eavesdropper_detected" for r in eve_rows)
    clean_false=sum(str(r["eve_on"]) in ("0","False","false") and r["verdict"]!="secure" for r in audit_rows)
    median=statistics.median(login_ms) if login_ms else 0
    summary=outdir/f"bb84_live_summary_{stamp}.md"
    summary.write_text(
      "# QPortal live BB84 account test\n\n"
      f"- Run time (UTC): {datetime.now(timezone.utc).isoformat()}\n- Accounts registered: {len(users)}\n"
      f"- Successful logins: {login_count} (3 per account)\n- Eve runs (from audit table): {len(eve_rows)}\n- Eve detected: {eve_detected}/{len(eve_rows)}\n"
      f"- Clean login false rejects (from audit table): {clean_false}\n- Median login time: {median:.2f} ms\n"
      f"- Audit rows exported: {len(audit_rows)}\n- Attack checks completed on accounts: {attack_accounts} (no proof, wrong proof, wrong session, replay)\n"
      "- Browser/server key fingerprint matched for every accepted exchange.\n"
      "- Qubits are simulated in this demo; no quantum channel to the browser. No IBM hardware jobs used.\n\n"
      "The script uses synthetic example.test accounts unless `--accounts-file` is supplied. It does not establish that 10–15 human classmates participated.\n",
      encoding="utf-8")
    print(f"Accounts={len(users)} logins={login_count} Eve detected={eve_detected}/{len(eve_rows)} clean false rejects={clean_false} median login={median:.2f} ms")
    print(f"Audit CSV: {audit_path}\nLogin CSV: {login_path}\nSummary: {summary}")

if __name__=="__main__": main()
