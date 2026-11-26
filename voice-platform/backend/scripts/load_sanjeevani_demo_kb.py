"""Upload the Sanjeevani demo knowledge bases into a company's workspace, under their fixed names.

    python backend/scripts/load_sanjeevani_demo_kb.py --api http://127.0.0.1:8055 --token aadi@stl.test --tenant 3
    python backend/scripts/load_sanjeevani_demo_kb.py --api https://<your console host> --token <firebase id token> --tenant 7

--tenant is the company's id; it is sent as X-Tenant-Id so a system administrator uploads INTO that company (the
documents then belong to it and no other company can see them). A company's own administrator can leave it out.
Files come from docs/sanjeevani_demo/knowledge/. Each is uploaded once; a file that is already there is skipped
(delete it in Knowledge first to replace it). Prints the exact names to put in each step's document list.
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request

# repo root is two levels up from backend/scripts/
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FOLDER = os.path.join(ROOT, "docs", "sanjeevani_demo", "knowledge")


def request(method, url, headers, body=None, content_type=None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def multipart(filename, data):
    boundary = "----sanjeevanidemo7e3b"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: text/markdown\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--token", required=True, help="sign-in token (the demo backend accepts an e-mail address)")
    ap.add_argument("--tenant", type=int, help="company id, for a system administrator")
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"}
    if args.tenant:
        headers["X-Tenant-Id"] = str(args.tenant)
    api = args.api.rstrip("/")

    status, listing = request("GET", f"{api}/api/knowledge/documents", headers)
    if status != 200:
        sys.exit(f"Could not read the knowledge list (HTTP {status}). Check --api, --token and --tenant.")
    have = {d.get("name") for d in listing.get("documents", [])}

    names = []
    for path in sorted(glob.glob(os.path.join(FOLDER, "SANJEEVANI-KB-*.md"))):
        name = os.path.basename(path)
        names.append(name)
        if name in have:
            print(f"  already there  {name}")
            continue
        with open(path, "rb") as f:
            body, ctype = multipart(name, f.read())
        status, out = request("POST", f"{api}/api/knowledge/upload", headers, body, ctype)
        print(f"  {'uploaded' if status == 200 else 'FAILED (HTTP %d)' % status:14} {name}  {out.get('detail', '') if status != 200 else ''}")

    print("\nDocument names for the agent steps (use exactly these):")
    for n in names:
        print("  ", n)


if __name__ == "__main__":
    main()
