"""Time the knowledge-base lookup the first-line-fix turn does.   python e2e/profile_rag.py"""
import asyncio, logging, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import harness
harness.configure_environment()
try:
    from dotenv import load_dotenv; load_dotenv(os.path.join(harness.BACKEND, ".env"))
except Exception: pass
logging.disable(logging.CRITICAL)

async def main():
    from app.rag_engine import RAGEngine
    r = RAGEngine()
    docs = ["printer_paper_jam_sop.md", "wifi_enterprise_wpa2_sop.md", "vpn_cisco_anyconnect_sop.md", "email_outlook_exchange_sop.md"]
    for q in ["Brother laser printer third floor paper jam error", "VPN connection timed out", "Outlook not syncing", "printer paper jam"]:
        for i in range(2):
            t = time.perf_counter()
            out = await r.search(q, node_id="w8_sym-agent-8", node_docs=docs)
            print(f"{int((time.perf_counter()-t)*1000):>5} ms  run{i}  {len(out or '')} chars  {q}")
asyncio.run(main())
