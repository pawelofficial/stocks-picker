"""Quick diagnostic: is the proxy working? What does Yahoo return through it?"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import requests
from urllib.parse import quote_plus

user = os.environ.get("WEBSHARE_PROXY_USERNAME", "").rsplit("-", 1)[0]
pw = os.environ.get("WEBSHARE_PROXY_PASSWORD", "")
proxy = f"http://{quote_plus(user + '-1')}:{quote_plus(pw)}@p.webshare.io:80"
proxies = {"http": proxy, "https": proxy}
print(f"Proxy: {user}-1 @ p.webshare.io:80")

print("\n--- Test 1: exit IP via httpbin ---")
try:
    r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
    print(f"  Status: {r.status_code}")
    print(f"  Body:   {r.text.strip()}")
except Exception as e:
    print(f"  FAILED: {e}")

print("\n--- Test 2: Yahoo Finance chart API (XOM, 5d) ---")
try:
    url = "https://query2.finance.yahoo.com/v8/finance/chart/XOM?range=5d&interval=1d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, proxies=proxies, headers=headers, timeout=20)
    print(f"  Status: {r.status_code}")
    body = r.text[:600]
    print(f"  Body:   {body}")
except Exception as e:
    print(f"  FAILED: {e}")

print("\n--- Test 3: Yahoo Finance (no proxy, direct) ---")
try:
    url = "https://query2.finance.yahoo.com/v8/finance/chart/XOM?range=5d&interval=1d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=20)
    print(f"  Status: {r.status_code}")
    body = r.text[:600]
    print(f"  Body:   {body}")
except Exception as e:
    print(f"  FAILED: {e}")
