"""Probe FRED for which series IDs actually return data."""
import os
import json
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import load_dotenv
load_dotenv()

key = os.environ['FRED_API_KEY']


def probe(sid):
    params = {
        'series_id': sid,
        'api_key': key,
        'file_type': 'json',
        'observation_start': '2018-01-01',
        'observation_end': '2026-04-30',
        'sort_order': 'desc',
        'limit': 5,
    }
    url = f'https://api.stlouisfed.org/fred/series/observations?{urlencode(params)}'
    try:
        with urlopen(url, timeout=15) as r:
            obs = json.loads(r.read())['observations']
            usable = [o for o in obs if o['value'] not in ('.', '')]
            if usable:
                print(f'{sid:30s} OK   last={usable[0]["date"]} val={usable[0]["value"]:>10s}  ({len(usable)}/{len(obs)})')
            else:
                print(f'{sid:30s} EMPTY')
    except Exception as e:
        print(f'{sid:30s} FAIL: {str(e)[:70]}')


print('=== Manufacturing / PMI candidates ===')
for s in ['NAPM', 'NAPMPI', 'USMPMI', 'INDPRO', 'IPMAN', 'MANEMP',
          'GACDFSA066MSFRBPHI', 'GACDISA066MSFRBPHI',
          'CFNAI', 'PMI', 'NAPMNOI', 'NAPMEI',
          'PCEPI', 'AMTMNO', 'AMTMTI']:
    probe(s)

print('\n=== Regional Fed PMIs ===')
for s in [
    'GACDISA066MSFRBPHI',  # Philly Fed Mfg General Activity
    'GACDFSA066MSFRBPHI',  # Philly Fed seasonally adjusted
    'CESM01EFRBNY',         # Empire State (NY) - might not exist
    'GACDISA066MSFRBNY',
    'MNFCRA66MNYFRB',
    'MFCSC',                # Richmond Fed
    'CESM01EFRBKC',
    'DALLASFED',
]:
    probe(s)

print('\n=== Put/Call ratio candidates ===')
for s in ['EQUITYPC', 'PCR', 'CBOEPCR', 'INDXPC', 'TOTALPC']:
    probe(s)

print('\n=== Margin debt ===')
for s in ['BOGZ1FL663067003Q', 'BOGZ1FL153064476Q']:
    probe(s)
