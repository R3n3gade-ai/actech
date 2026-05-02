"""Quick probe: verify CryptoCompare BTC perp OI history depth + schema."""
from urllib.request import urlopen, Request
import json
import pandas as pd

url = ('https://data-api.cryptocompare.com/futures/v1/historical/open-interest/days'
       '?market=binance&instrument=BTC-USDT-VANILLA-PERPETUAL&limit=2000')
req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
r = urlopen(req, timeout=30)
p = json.loads(r.read())
data = p.get('Data', [])
print(f'rows={len(data)}')
print(f'oldest: {pd.Timestamp(data[0]["TIMESTAMP"], unit="s").date()}')
print(f'newest: {pd.Timestamp(data[-1]["TIMESTAMP"], unit="s").date()}')
print('SAMPLE ROW KEYS:')
print(list(data[-1].keys()))
print()
print('Last row:')
for k, v in data[-1].items():
    print(f'  {k} = {v}')
