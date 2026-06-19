#!/usr/bin/env python3
"""AIS App - Flask backend for vessel tracking on Cyberdeck."""

from __future__ import annotations

import json
import math
import os
import shlex
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime

from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_VERSION = '2026.06.19'

CONFIG_DIR = '/home/slofi/intercept/data/ais'
RECEIVER_CONFIG_FILE = os.path.join(CONFIG_DIR, 'receiver_config.json')
APP_CONFIG_FILE = os.path.join(CONFIG_DIR, 'app_config.json')
VESSEL_DB_FILE = os.path.join(CONFIG_DIR, 'vessel_db.json')
VESSEL_DB_META_FILE = os.path.join(CONFIG_DIR, 'vessel_db_meta.json')

AIS_UDP_HOST = os.environ.get('AIS_UDP_HOST', '0.0.0.0')
AIS_UDP_PORT = int(os.environ.get('AIS_UDP_PORT', '10110'))
AIS_JSON_TCP_PORT = int(os.environ.get('AIS_JSON_TCP_PORT', '10111'))
AIS_CMD = os.environ.get(
    'AIS_CMD',
    f'AIS-catcher -d:0 -S {AIS_JSON_TCP_PORT} JSON_FULL on -q -gr TUNER 40'
)
GPS_POLL_INTERVAL = 5.0
GPS_OPSTOC_URL = 'http://localhost:8090/api/gps'
GPS_OM_URL = 'http://localhost:8082/api/settings/gps'

GONE_TIMEOUT = 180.0
MAX_TRACK_PTS = 300
MAX_HISTORY = 80

_lock = threading.Lock()
_vessels: dict[str, dict] = {}
_history: dict[str, dict] = {}
_receiver: dict = {}
_manual_receiver: dict | None = None
_gps_source = 'auto'
_gps_status: dict = {}
_message_count = 0
_last_message_time: float | None = None
_udp_status = {'running': False, 'error': '', 'port': AIS_UDP_PORT}
_decoder_proc: subprocess.Popen | None = None
_decoder_status = {'running': False, 'error': '', 'cmd': AIS_CMD, 'json_port': AIS_JSON_TCP_PORT}
_vessel_db: dict[str, dict] = {}
_vessel_db_meta: dict = {}
_db_loaded = False


def _asset_version() -> str:
    try:
        return subprocess.check_output(
            ['git', '-C', BASE_DIR, 'rev-parse', '--short', 'HEAD'],
            text=True,
            timeout=2,
        ).strip() or APP_VERSION
    except Exception:
        return APP_VERSION


# ---------------------------------------------------------------------------
# AIS NMEA decoding
# ---------------------------------------------------------------------------
_ARMOR = {chr(i + 48): i for i in range(40)}
_ARMOR.update({chr(i + 56): i for i in range(40, 64)})
_fragments: dict[tuple[str, str, str], dict] = {}

SHIP_TYPES = {
    30: 'Fishing', 31: 'Towing', 32: 'Towing long/wide', 33: 'Dredging',
    34: 'Diving', 35: 'Military', 36: 'Sailing', 37: 'Pleasure craft',
    50: 'Pilot', 51: 'Search and rescue', 52: 'Tug', 53: 'Port tender',
    54: 'Anti-pollution', 55: 'Law enforcement', 58: 'Medical',
    60: 'Passenger', 70: 'Cargo', 80: 'Tanker', 90: 'Other',
}
NAV_STATUS = {
    0: 'Under way', 1: 'At anchor', 2: 'Not under command',
    3: 'Restricted manoeuvrability', 4: 'Constrained by draught',
    5: 'Moored', 6: 'Aground', 7: 'Fishing', 8: 'Sailing',
    15: 'Undefined',
}

MID_COUNTRIES = {
    '201': 'Albania', '202': 'Andorra', '203': 'Austria', '204': 'Azores',
    '205': 'Belgium', '206': 'Belarus', '207': 'Bulgaria', '208': 'Vatican City',
    '209': 'Cyprus', '210': 'Cyprus', '211': 'Germany', '212': 'Cyprus',
    '213': 'Georgia', '214': 'Moldova', '215': 'Malta', '216': 'Armenia',
    '218': 'Germany', '219': 'Denmark', '220': 'Denmark', '224': 'Spain',
    '225': 'Spain', '226': 'France', '227': 'France', '228': 'France',
    '229': 'Malta', '230': 'Finland', '231': 'Faroe Islands', '232': 'United Kingdom',
    '233': 'United Kingdom', '234': 'United Kingdom', '235': 'United Kingdom',
    '236': 'Gibraltar', '237': 'Greece', '238': 'Croatia', '239': 'Greece',
    '240': 'Greece', '241': 'Greece', '242': 'Morocco', '243': 'Hungary',
    '244': 'Netherlands', '245': 'Netherlands', '246': 'Netherlands', '247': 'Italy',
    '248': 'Malta', '249': 'Malta', '250': 'Ireland', '251': 'Iceland',
    '252': 'Liechtenstein', '253': 'Luxembourg', '254': 'Monaco', '255': 'Madeira',
    '256': 'Malta', '257': 'Norway', '258': 'Norway', '259': 'Norway',
    '261': 'Poland', '262': 'Montenegro', '263': 'Portugal', '264': 'Romania',
    '265': 'Sweden', '266': 'Sweden', '267': 'Slovakia', '268': 'San Marino',
    '269': 'Switzerland', '270': 'Czech Republic', '271': 'Turkey', '272': 'Ukraine',
    '273': 'Russia', '274': 'North Macedonia', '275': 'Latvia', '276': 'Estonia',
    '277': 'Lithuania', '278': 'Slovenia', '279': 'Serbia',
    '301': 'Anguilla', '303': 'Alaska', '304': 'Antigua and Barbuda', '305': 'Antigua and Barbuda',
    '306': 'Netherlands Caribbean', '307': 'Aruba', '308': 'Bahamas', '309': 'Bahamas',
    '310': 'Bermuda', '311': 'Bahamas', '312': 'Belize', '316': 'Canada',
    '319': 'Cayman Islands', '338': 'United States', '366': 'United States',
    '367': 'United States', '368': 'United States', '369': 'United States',
    '370': 'Panama', '371': 'Panama', '372': 'Panama', '373': 'Panama',
    '374': 'Panama', '375': 'St Vincent and the Grenadines', '376': 'St Vincent and the Grenadines',
    '377': 'St Vincent and the Grenadines', '378': 'British Virgin Islands', '379': 'US Virgin Islands',
    '401': 'Afghanistan', '403': 'Saudi Arabia', '405': 'Bangladesh', '408': 'Bahrain',
    '410': 'Bhutan', '412': 'China', '413': 'China', '414': 'China',
    '416': 'Taiwan', '417': 'Sri Lanka', '419': 'India', '422': 'Iran',
    '423': 'Azerbaijan', '431': 'Japan', '432': 'Japan', '440': 'South Korea',
    '441': 'South Korea', '447': 'Kuwait', '450': 'Lebanon', '453': 'Macao',
    '455': 'Maldives', '457': 'Mongolia', '459': 'Nepal', '461': 'Oman',
    '463': 'Pakistan', '466': 'Qatar', '470': 'United Arab Emirates', '471': 'United Arab Emirates',
    '472': 'Tajikistan', '473': 'Yemen', '475': 'Yemen', '477': 'Hong Kong',
    '478': 'Bosnia and Herzegovina',
    '501': 'Adelie Land', '503': 'Australia', '506': 'Myanmar', '508': 'Brunei',
    '510': 'Micronesia', '511': 'Palau', '512': 'New Zealand', '514': 'Cambodia',
    '515': 'Cambodia', '516': 'Christmas Island', '518': 'Cook Islands', '520': 'Fiji',
    '523': 'Cocos Islands', '525': 'Indonesia', '529': 'Kiribati', '533': 'Malaysia',
    '536': 'Northern Mariana Islands', '538': 'Marshall Islands', '540': 'New Caledonia',
    '542': 'Niue', '544': 'Nauru', '546': 'French Polynesia', '548': 'Philippines',
    '553': 'Papua New Guinea', '555': 'Pitcairn Island', '557': 'Solomon Islands',
    '559': 'American Samoa', '561': 'Samoa', '563': 'Singapore', '564': 'Singapore',
    '565': 'Singapore', '566': 'Singapore', '567': 'Thailand', '570': 'Tonga',
    '572': 'Tuvalu', '574': 'Vietnam', '576': 'Vanuatu', '577': 'Vanuatu',
    '578': 'Wallis and Futuna',
    '601': 'South Africa', '603': 'Angola', '605': 'Algeria', '607': 'Saint Paul and Amsterdam Islands',
    '608': 'Ascension Island', '609': 'Burundi', '610': 'Benin', '611': 'Botswana',
    '612': 'Central African Republic', '613': 'Cameroon', '615': 'Congo', '616': 'Comoros',
    '617': 'Cape Verde', '618': 'Crozet Archipelago', '619': 'Ivory Coast', '620': 'Comoros',
    '621': 'Djibouti', '622': 'Egypt', '624': 'Ethiopia', '625': 'Eritrea',
    '626': 'Gabon', '627': 'Ghana', '629': 'Gambia', '630': 'Guinea-Bissau',
    '631': 'Equatorial Guinea', '632': 'Guinea', '633': 'Burkina Faso', '634': 'Kenya',
    '635': 'Kerguelen Islands', '636': 'Liberia', '637': 'Liberia', '638': 'South Sudan',
    '642': 'Libya', '644': 'Lesotho', '645': 'Mauritius', '647': 'Madagascar',
    '649': 'Mali', '650': 'Mozambique', '654': 'Mauritania', '655': 'Malawi',
    '656': 'Niger', '657': 'Nigeria', '659': 'Namibia', '660': 'Reunion and Mayotte',
    '661': 'Rwanda', '662': 'Sudan', '663': 'Senegal', '664': 'Seychelles',
    '665': 'Saint Helena', '666': 'Somalia', '667': 'Sierra Leone', '668': 'Sao Tome and Principe',
    '669': 'Eswatini', '670': 'Chad', '671': 'Togo', '672': 'Tunisia',
    '674': 'Tanzania', '675': 'Uganda', '676': 'Democratic Republic of the Congo',
    '677': 'Tanzania', '678': 'Zambia', '679': 'Zimbabwe',
    '701': 'Argentina', '710': 'Brazil', '720': 'Bolivia', '725': 'Chile',
    '730': 'Colombia', '735': 'Ecuador', '740': 'Falkland Islands', '745': 'French Guiana',
    '750': 'Guyana', '755': 'Paraguay', '760': 'Peru', '765': 'Suriname',
    '770': 'Uruguay', '775': 'Venezuela',
}

COUNTRY_CODES = {
    'Albania': 'AL', 'Andorra': 'AD', 'Austria': 'AT', 'Azores': 'PT',
    'Belgium': 'BE', 'Belarus': 'BY', 'Bulgaria': 'BG', 'Vatican City': 'VA',
    'Cyprus': 'CY', 'Germany': 'DE', 'Georgia': 'GE', 'Moldova': 'MD',
    'Malta': 'MT', 'Armenia': 'AM', 'Denmark': 'DK', 'Spain': 'ES',
    'France': 'FR', 'Finland': 'FI', 'Faroe Islands': 'FO', 'United Kingdom': 'GB',
    'Gibraltar': 'GI', 'Greece': 'GR', 'Croatia': 'HR', 'Morocco': 'MA',
    'Hungary': 'HU', 'Netherlands': 'NL', 'Italy': 'IT', 'Ireland': 'IE',
    'Iceland': 'IS', 'Liechtenstein': 'LI', 'Luxembourg': 'LU', 'Monaco': 'MC',
    'Madeira': 'PT', 'Norway': 'NO', 'Poland': 'PL', 'Montenegro': 'ME',
    'Portugal': 'PT', 'Romania': 'RO', 'Sweden': 'SE', 'Slovakia': 'SK',
    'San Marino': 'SM', 'Switzerland': 'CH', 'Czech Republic': 'CZ', 'Turkey': 'TR',
    'Ukraine': 'UA', 'Russia': 'RU', 'North Macedonia': 'MK', 'Latvia': 'LV',
    'Estonia': 'EE', 'Lithuania': 'LT', 'Slovenia': 'SI', 'Serbia': 'RS',
    'Anguilla': 'AI', 'Alaska': 'US', 'Antigua and Barbuda': 'AG',
    'Netherlands Caribbean': 'BQ', 'Aruba': 'AW', 'Bahamas': 'BS', 'Belize': 'BZ',
    'Canada': 'CA', 'Cayman Islands': 'KY', 'United States': 'US', 'Panama': 'PA',
    'St Vincent and the Grenadines': 'VC', 'British Virgin Islands': 'VG',
    'US Virgin Islands': 'VI', 'Afghanistan': 'AF', 'Saudi Arabia': 'SA',
    'Bangladesh': 'BD', 'Bahrain': 'BH', 'Bhutan': 'BT', 'China': 'CN',
    'Taiwan': 'TW', 'Sri Lanka': 'LK', 'India': 'IN', 'Iran': 'IR',
    'Azerbaijan': 'AZ', 'Japan': 'JP', 'South Korea': 'KR', 'Kuwait': 'KW',
    'Lebanon': 'LB', 'Macao': 'MO', 'Maldives': 'MV', 'Mongolia': 'MN',
    'Nepal': 'NP', 'Oman': 'OM', 'Pakistan': 'PK', 'Qatar': 'QA',
    'United Arab Emirates': 'AE', 'Tajikistan': 'TJ', 'Yemen': 'YE',
    'Hong Kong': 'HK', 'Bosnia and Herzegovina': 'BA', 'Adelie Land': 'FR',
    'Australia': 'AU', 'Myanmar': 'MM', 'Brunei': 'BN', 'Micronesia': 'FM',
    'Palau': 'PW', 'New Zealand': 'NZ', 'Cambodia': 'KH', 'Christmas Island': 'CX',
    'Cook Islands': 'CK', 'Fiji': 'FJ', 'Cocos Islands': 'CC', 'Indonesia': 'ID',
    'Kiribati': 'KI', 'Malaysia': 'MY', 'Northern Mariana Islands': 'MP',
    'Marshall Islands': 'MH', 'New Caledonia': 'NC', 'Niue': 'NU', 'Nauru': 'NR',
    'French Polynesia': 'PF', 'Philippines': 'PH', 'Papua New Guinea': 'PG',
    'Pitcairn Island': 'PN', 'Solomon Islands': 'SB', 'American Samoa': 'AS',
    'Samoa': 'WS', 'Singapore': 'SG', 'Thailand': 'TH', 'Tonga': 'TO',
    'Tuvalu': 'TV', 'Vietnam': 'VN', 'Vanuatu': 'VU', 'Wallis and Futuna': 'WF',
    'South Africa': 'ZA', 'Angola': 'AO', 'Algeria': 'DZ',
    'Saint Paul and Amsterdam Islands': 'FR', 'Ascension Island': 'SH', 'Burundi': 'BI',
    'Benin': 'BJ', 'Botswana': 'BW', 'Central African Republic': 'CF', 'Cameroon': 'CM',
    'Congo': 'CG', 'Comoros': 'KM', 'Cape Verde': 'CV', 'Crozet Archipelago': 'FR',
    'Ivory Coast': 'CI', 'Djibouti': 'DJ', 'Egypt': 'EG', 'Ethiopia': 'ET',
    'Eritrea': 'ER', 'Gabon': 'GA', 'Ghana': 'GH', 'Gambia': 'GM',
    'Guinea-Bissau': 'GW', 'Equatorial Guinea': 'GQ', 'Guinea': 'GN',
    'Burkina Faso': 'BF', 'Kenya': 'KE', 'Kerguelen Islands': 'FR', 'Liberia': 'LR',
    'South Sudan': 'SS', 'Libya': 'LY', 'Lesotho': 'LS', 'Mauritius': 'MU',
    'Madagascar': 'MG', 'Mali': 'ML', 'Mozambique': 'MZ', 'Mauritania': 'MR',
    'Malawi': 'MW', 'Niger': 'NE', 'Nigeria': 'NG', 'Namibia': 'NA',
    'Reunion and Mayotte': 'RE', 'Rwanda': 'RW', 'Sudan': 'SD', 'Senegal': 'SN',
    'Seychelles': 'SC', 'Saint Helena': 'SH', 'Somalia': 'SO', 'Sierra Leone': 'SL',
    'Sao Tome and Principe': 'ST', 'Eswatini': 'SZ', 'Chad': 'TD', 'Togo': 'TG',
    'Tunisia': 'TN', 'Tanzania': 'TZ', 'Uganda': 'UG',
    'Democratic Republic of the Congo': 'CD', 'Zambia': 'ZM', 'Zimbabwe': 'ZW',
    'Argentina': 'AR', 'Brazil': 'BR', 'Bolivia': 'BO', 'Chile': 'CL',
    'Colombia': 'CO', 'Ecuador': 'EC', 'Falkland Islands': 'FK', 'French Guiana': 'GF',
    'Guyana': 'GY', 'Paraguay': 'PY', 'Peru': 'PE', 'Suriname': 'SR',
    'Uruguay': 'UY', 'Venezuela': 'VE',
}


def _flag_emoji(country_code: str) -> str:
    if not country_code or len(country_code) != 2:
        return ''
    base = 0x1F1E6
    code = country_code.upper()
    if not all('A' <= ch <= 'Z' for ch in code):
        return ''
    return ''.join(chr(base + ord(ch) - ord('A')) for ch in code)


def _country_flag(country: str) -> str:
    return _flag_emoji(COUNTRY_CODES.get(country, ''))


def _mmsi_kind(mmsi: str) -> str:
    if not mmsi:
        return ''
    if mmsi.startswith('00'):
        return 'Coast station'
    if mmsi.startswith('0'):
        return 'Group ship station'
    if mmsi.startswith('111'):
        return 'SAR aircraft'
    if mmsi.startswith('970'):
        return 'AIS-SART'
    if mmsi.startswith('972'):
        return 'MOB device'
    if mmsi.startswith('974'):
        return 'EPIRB-AIS'
    if mmsi.startswith('98'):
        return 'Associated craft'
    if mmsi.startswith('99'):
        return 'Aid to navigation'
    if mmsi[0:1] in '234567':
        return 'Ship station'
    return 'Maritime station'


def _mmsi_mid(mmsi: str) -> str:
    if not mmsi:
        return ''
    if mmsi.startswith('00') and len(mmsi) >= 5:
        return mmsi[2:5]
    if mmsi.startswith('0') and len(mmsi) >= 4:
        return mmsi[1:4]
    if mmsi.startswith('111') and len(mmsi) >= 6:
        return mmsi[3:6]
    if mmsi.startswith('98') and len(mmsi) >= 5:
        return mmsi[2:5]
    if mmsi.startswith('99') and len(mmsi) >= 5:
        return mmsi[2:5]
    if len(mmsi) >= 3 and mmsi[0] in '234567':
        return mmsi[:3]
    return ''


def _mmsi_country(mmsi: str) -> str:
    return MID_COUNTRIES.get(_mmsi_mid(mmsi), '')


def _mmsi_country_flag(mmsi: str) -> str:
    return _country_flag(_mmsi_country(mmsi))



def _sixbit(payload: str) -> str:
    bits = []
    for ch in payload:
        val = _ARMOR.get(ch)
        if val is None:
            return ''
        bits.append(format(val, '06b'))
    return ''.join(bits)


def _uint(bits: str, start: int, length: int) -> int:
    part = bits[start:start + length]
    return int(part, 2) if part else 0


def _sint(bits: str, start: int, length: int) -> int:
    val = _uint(bits, start, length)
    if val & (1 << (length - 1)):
        val -= 1 << length
    return val


def _text(bits: str, start: int, length: int) -> str:
    chars = []
    table = '@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !"#$%&\'()*+,-./0123456789:;<=>?'
    for i in range(start, start + length, 6):
        idx = _uint(bits, i, 6)
        chars.append(table[idx] if idx < len(table) else ' ')
    return ''.join(chars).replace('@', ' ').strip()


def _decode_lat(raw: int) -> float | None:
    if raw == 0x3412140:
        return None
    lat = raw / 600000.0
    return lat if -90 <= lat <= 90 else None


def _decode_lon(raw: int) -> float | None:
    if raw == 0x6791AC0:
        return None
    lon = raw / 600000.0
    return lon if -180 <= lon <= 180 else None


def _ship_type_text(code: int | None) -> str:
    if code is None:
        return ''
    if code in SHIP_TYPES:
        return SHIP_TYPES[code]
    decade = (code // 10) * 10
    return SHIP_TYPES.get(decade, f'Type {code}')


def _parse_sentence(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith(('!AIVDM', '!AIVDO')):
        return None
    if '*' in line:
        body, checksum = line[1:].split('*', 1)
        calc = 0
        for ch in body:
            calc ^= ord(ch)
        try:
            if calc != int(checksum[:2], 16):
                return None
        except ValueError:
            return None
        parts = body.split(',')
    else:
        parts = line[1:].split(',')
    if len(parts) < 7:
        return None
    total = int(parts[1] or '1')
    num = int(parts[2] or '1')
    seq = parts[3] or ''
    channel = parts[4] or ''
    payload = parts[5]
    fill = int(parts[6] or '0')
    if total > 1:
        key = (seq, channel, parts[0])
        frag = _fragments.setdefault(key, {'total': total, 'payloads': {}})
        frag['payloads'][num] = payload
        if len(frag['payloads']) < total:
            return None
        payload = ''.join(frag['payloads'][i] for i in range(1, total + 1))
        _fragments.pop(key, None)
        fill = int(parts[6] or '0')
    bits = _sixbit(payload)
    if fill:
        bits = bits[:-fill]
    if len(bits) < 38:
        return None

    msg_type = _uint(bits, 0, 6)
    mmsi = str(_uint(bits, 8, 30))
    out = {'mmsi': mmsi, 'msg_type': msg_type}

    if msg_type in (1, 2, 3):
        out.update({
            'nav_status': _uint(bits, 38, 4),
            'speed': None if _uint(bits, 50, 10) >= 1023 else round(_uint(bits, 50, 10) / 10.0, 1),
            'accuracy': bool(_uint(bits, 60, 1)),
            'lon': _decode_lon(_sint(bits, 61, 28)),
            'lat': _decode_lat(_sint(bits, 89, 27)),
            'course': None if _uint(bits, 116, 12) >= 3600 else round(_uint(bits, 116, 12) / 10.0, 1),
            'heading': None if _uint(bits, 128, 9) >= 511 else _uint(bits, 128, 9),
            'timestamp': _uint(bits, 137, 6),
        })
    elif msg_type == 5 and len(bits) >= 424:
        out.update({
            'imo': _uint(bits, 40, 30),
            'callsign': _text(bits, 70, 42),
            'name': _text(bits, 112, 120),
            'ship_type': _uint(bits, 232, 8),
            'destination': _text(bits, 302, 120),
        })
    elif msg_type == 18 and len(bits) >= 168:
        out.update({
            'speed': None if _uint(bits, 46, 10) >= 1023 else round(_uint(bits, 46, 10) / 10.0, 1),
            'accuracy': bool(_uint(bits, 56, 1)),
            'lon': _decode_lon(_sint(bits, 57, 28)),
            'lat': _decode_lat(_sint(bits, 85, 27)),
            'course': None if _uint(bits, 112, 12) >= 3600 else round(_uint(bits, 112, 12) / 10.0, 1),
            'heading': None if _uint(bits, 124, 9) >= 511 else _uint(bits, 124, 9),
            'timestamp': _uint(bits, 133, 6),
        })
    elif msg_type == 24 and len(bits) >= 160:
        part = _uint(bits, 38, 2)
        if part == 0:
            out['name'] = _text(bits, 40, 120)
        elif part == 1:
            out.update({
                'ship_type': _uint(bits, 40, 8),
                'callsign': _text(bits, 90, 42),
            })
    else:
        return None
    return out



def _clean_text(value) -> str:
    return str(value or '').strip().strip('@')


def _message_from_json(msg: dict) -> dict | None:
    mmsi = msg.get('mmsi')
    if not mmsi:
        return None
    out = {'mmsi': str(mmsi), 'msg_type': msg.get('type') or msg.get('msg_type')}
    lat_val = msg.get('latitude', msg.get('lat'))
    lon_val = msg.get('longitude', msg.get('lon'))
    try:
        if lat_val is not None and lon_val is not None:
            lat = float(lat_val)
            lon = float(lon_val)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                out['lat'] = lat
                out['lon'] = lon
    except (TypeError, ValueError):
        pass
    for src, dst, cast in (
        ('speed', 'speed', float), ('course', 'course', float), ('heading', 'heading', int),
        ('status', 'nav_status', int), ('shiptype', 'ship_type', int), ('imo', 'imo', int),
        ('to_bow', 'to_bow', int), ('to_stern', 'to_stern', int),
        ('to_port', 'to_port', int), ('to_starboard', 'to_starboard', int),
    ):
        if msg.get(src) is not None:
            try:
                out[dst] = cast(msg[src])
            except (TypeError, ValueError):
                pass
    for src, dst in (
        ('status_text', 'nav_status_text'), ('shiptype_text', 'ship_type_text'),
        ('shipname', 'name'), ('name', 'name'), ('callsign', 'callsign'), ('destination', 'destination'),
    ):
        val = _clean_text(msg.get(src))
        if val:
            out[dst] = val
    return out


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def _load_vessel_db() -> bool:
    global _vessel_db, _vessel_db_meta, _db_loaded
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(VESSEL_DB_FILE):
            with open(VESSEL_DB_FILE) as f:
                data = json.load(f)
            _vessel_db = data.get('vessels', data if isinstance(data, dict) else {})
        if os.path.exists(VESSEL_DB_META_FILE):
            with open(VESSEL_DB_META_FILE) as f:
                _vessel_db_meta = json.load(f)
        _db_loaded = True
        return True
    except Exception as exc:
        _vessel_db_meta = {'error': str(exc)}
        _db_loaded = False
        return False


def _save_vessel_db(source: str = 'received') -> bool:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        meta = {
            'updated': _now_iso(),
            'source': source,
            'vessel_count': len(_vessel_db),
            'mid_count': len(MID_COUNTRIES),
        }
        with open(VESSEL_DB_FILE, 'w') as f:
            json.dump({'vessels': _vessel_db}, f, indent=2, sort_keys=True)
        with open(VESSEL_DB_META_FILE, 'w') as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        _vessel_db_meta.clear()
        _vessel_db_meta.update(meta)
        return True
    except Exception as exc:
        _vessel_db_meta['error'] = str(exc)
        return False


def _db_entry_from_message(mmsi: str, msg: dict, existing: dict | None = None) -> dict:
    entry = dict(existing or {})
    entry['mmsi'] = mmsi
    entry['mid'] = _mmsi_mid(mmsi)
    entry['country'] = _mmsi_country(mmsi)
    entry['country_flag'] = _mmsi_country_flag(mmsi)
    entry['mmsi_kind'] = _mmsi_kind(mmsi)
    for fld in ('name', 'callsign', 'destination', 'ship_type_text', 'nav_status_text'):
        if msg.get(fld):
            entry[fld] = msg[fld]
    for fld in ('imo', 'ship_type', 'to_bow', 'to_stern', 'to_port', 'to_starboard'):
        if msg.get(fld) is not None:
            entry[fld] = msg[fld]
    if msg.get('ship_type') is not None and not entry.get('ship_type_text'):
        entry['ship_type_text'] = _ship_type_text(msg['ship_type'])
    if entry.get('to_bow') is not None and entry.get('to_stern') is not None:
        entry['length_m'] = int(entry.get('to_bow') or 0) + int(entry.get('to_stern') or 0)
    if entry.get('to_port') is not None and entry.get('to_starboard') is not None:
        entry['beam_m'] = int(entry.get('to_port') or 0) + int(entry.get('to_starboard') or 0)
    entry['last_updated'] = _now_iso()
    if 'first_seen' not in entry:
        entry['first_seen'] = entry['last_updated']
    return entry


def _learn_vessel(mmsi: str, msg: dict):
    static_fields = {
        'name', 'callsign', 'destination', 'ship_type', 'ship_type_text',
        'imo', 'to_bow', 'to_stern', 'to_port', 'to_starboard'
    }
    if not any(msg.get(f) is not None and msg.get(f) != '' for f in static_fields):
        return
    _vessel_db[mmsi] = _db_entry_from_message(mmsi, msg, _vessel_db.get(mmsi))
    _save_vessel_db('received')


def _enrich_vessel(vessel: dict):
    mmsi = str(vessel.get('mmsi') or '')
    entry = _vessel_db.get(mmsi, {})
    vessel['mid'] = entry.get('mid') or _mmsi_mid(mmsi)
    vessel['country'] = entry.get('country') or _mmsi_country(mmsi)
    vessel['country_flag'] = entry.get('country_flag') or _country_flag(vessel['country'])
    vessel['mmsi_kind'] = entry.get('mmsi_kind') or _mmsi_kind(mmsi)
    for fld in (
        'name', 'callsign', 'ship_type', 'ship_type_text', 'destination',
        'imo', 'to_bow', 'to_stern', 'to_port', 'to_starboard', 'length_m', 'beam_m'
    ):
        if not vessel.get(fld) and entry.get(fld):
            vessel[fld] = entry[fld]


def _update_db_from_session() -> int:
    count = 0
    for source in (_vessels, _history):
        for mmsi, vessel in source.items():
            before = dict(_vessel_db.get(mmsi, {}))
            entry = _db_entry_from_message(mmsi, vessel, before)
            has_identity = any(entry.get(f) for f in ('name', 'callsign', 'ship_type', 'imo', 'length_m', 'beam_m'))
            if has_identity and entry != before:
                _vessel_db[mmsi] = entry
                count += 1
    _save_vessel_db('session')
    return count


def _merge_imported_vessels(data: dict) -> int:
    vessels = data.get('vessels', data) if isinstance(data, dict) else {}
    if not isinstance(vessels, dict):
        raise ValueError('Expected a JSON object keyed by MMSI, or {"vessels": {...}}')
    count = 0
    for raw_mmsi, raw_entry in vessels.items():
        if not isinstance(raw_entry, dict):
            continue
        mmsi = str(raw_entry.get('mmsi') or raw_mmsi).strip()
        if not mmsi.isdigit():
            continue
        entry = _db_entry_from_message(mmsi, raw_entry, _vessel_db.get(mmsi))
        _vessel_db[mmsi] = entry
        count += 1
    _save_vessel_db('import')
    return count

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def _haversine_nm(lat1, lon1, lat2, lon2) -> float:
    r_nm = 3440.065
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return r_nm * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _bearing_distance_point(lat: float, lon: float, bearing_deg: float, distance_nm: float):
    radius_nm = 3440.065
    brng = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    d = distance_nm / radius_nm
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brng))
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180


def _estimate_receiver_cpa(vessel: dict, rlat, rlon):
    if rlat is None or rlon is None or vessel.get('lat') is None or vessel.get('lon') is None:
        return
    speed = vessel.get('speed') or 0
    course = vessel.get('course')
    if speed <= 0.2 or course is None:
        vessel['cpa_distance_nm'] = vessel.get('distance')
        vessel['tcpa_min'] = None
        vessel['approaching'] = False
        return
    lat = float(vessel['lat'])
    lon = float(vessel['lon'])
    now_dist = _haversine_nm(rlat, rlon, lat, lon)
    best_min = 0
    best_dist = now_dist
    # Scan six hours ahead in 3-minute steps. This is stable enough for UI alerts
    # without dragging in a navigation library.
    for minute in range(3, 361, 3):
        nlat, nlon = _bearing_distance_point(lat, lon, course, speed * minute / 60.0)
        dist = _haversine_nm(rlat, rlon, nlat, nlon)
        if dist < best_dist:
            best_dist = dist
            best_min = minute
    vessel['cpa_distance_nm'] = round(best_dist, 2)
    vessel['tcpa_min'] = best_min if best_min > 0 else None
    vessel['approaching'] = best_dist < now_dist - 0.05


def _new_vessel(mmsi: str, now: float) -> dict:
    vessel = {
        'mmsi': mmsi,
        'name': '',
        'callsign': '',
        'ship_type': None,
        'ship_type_text': '',
        'destination': '',
        'imo': None,
        'mid': _mmsi_mid(mmsi),
        'country': _mmsi_country(mmsi),
        'country_flag': _mmsi_country_flag(mmsi),
        'mmsi_kind': _mmsi_kind(mmsi),
        'length_m': None,
        'beam_m': None,
        'to_bow': None,
        'to_stern': None,
        'to_port': None,
        'to_starboard': None,
        'nav_status': None,
        'nav_status_text': '',
        'lat': None,
        'lon': None,
        'speed': None,
        'course': None,
        'heading': None,
        'accuracy': None,
        'messages': 0,
        'track_points': [],
        'first_seen': now,
        'last_seen': now,
        'gone': False,
        'gone_at': None,
        'distance': None,
        'cpa_distance_nm': None,
        'tcpa_min': None,
        'approaching': False,
    }
    _enrich_vessel(vessel)
    return vessel


def _apply_message(msg: dict):
    global _message_count, _last_message_time
    now = time.time()
    mmsi = msg.get('mmsi')
    if not mmsi:
        return
    eff_recv = _manual_receiver or _receiver
    rlat = eff_recv.get('lat') if eff_recv else None
    rlon = eff_recv.get('lon') if eff_recv else None
    with _lock:
        vessel = _vessels.get(mmsi)
        if vessel is None:
            vessel = _new_vessel(mmsi, now)
            _vessels[mmsi] = vessel
        _enrich_vessel(vessel)
        for fld in ('name', 'callsign', 'destination'):
            if msg.get(fld):
                vessel[fld] = msg[fld]
        if msg.get('ship_type') is not None:
            vessel['ship_type'] = msg['ship_type']
            vessel['ship_type_text'] = msg.get('ship_type_text') or _ship_type_text(msg['ship_type'])
        elif msg.get('ship_type_text'):
            vessel['ship_type_text'] = msg['ship_type_text']
        if msg.get('nav_status') is not None:
            vessel['nav_status'] = msg['nav_status']
            vessel['nav_status_text'] = msg.get('nav_status_text') or NAV_STATUS.get(msg['nav_status'], f'Status {msg["nav_status"]}')
        elif msg.get('nav_status_text'):
            vessel['nav_status_text'] = msg['nav_status_text']
        for fld in ('imo', 'to_bow', 'to_stern', 'to_port', 'to_starboard'):
            if msg.get(fld) is not None:
                vessel[fld] = msg[fld]
        if vessel.get('to_bow') is not None and vessel.get('to_stern') is not None:
            vessel['length_m'] = int(vessel.get('to_bow') or 0) + int(vessel.get('to_stern') or 0)
        if vessel.get('to_port') is not None and vessel.get('to_starboard') is not None:
            vessel['beam_m'] = int(vessel.get('to_port') or 0) + int(vessel.get('to_starboard') or 0)
        vessel['mid'] = _mmsi_mid(mmsi)
        vessel['country'] = vessel.get('country') or _mmsi_country(mmsi)
        vessel['country_flag'] = vessel.get('country_flag') or _country_flag(vessel['country'])
        vessel['mmsi_kind'] = vessel.get('mmsi_kind') or _mmsi_kind(mmsi)
        _learn_vessel(mmsi, msg)
        for fld in ('lat', 'lon', 'speed', 'course', 'heading', 'accuracy'):
            if msg.get(fld) is not None:
                vessel[fld] = msg[fld]
        vessel['messages'] += 1
        vessel['last_seen'] = now
        vessel['gone'] = False
        vessel['gone_at'] = None
        if rlat is not None and rlon is not None and vessel['lat'] is not None and vessel['lon'] is not None:
            vessel['distance'] = round(_haversine_nm(rlat, rlon, vessel['lat'], vessel['lon']), 1)
            _estimate_receiver_cpa(vessel, rlat, rlon)
        if vessel['lat'] is not None and vessel['lon'] is not None:
            pts = vessel['track_points']
            last = pts[-1] if pts else None
            point = [vessel['lat'], vessel['lon'], vessel.get('speed'), vessel.get('course')]
            if last is None or last[0] != point[0] or last[1] != point[1]:
                pts.append(point)
                if len(pts) > MAX_TRACK_PTS:
                    del pts[0]
        _message_count += 1
        _last_message_time = now


def _sweep_gone():
    now = time.time()
    with _lock:
        for mmsi in list(_vessels):
            vessel = _vessels[mmsi]
            if now - vessel['last_seen'] < GONE_TIMEOUT:
                continue
            if not vessel['gone']:
                vessel['gone'] = True
                vessel['gone_at'] = now
            elif now - vessel['gone_at'] >= GONE_TIMEOUT:
                _history[mmsi] = _vessels.pop(mmsi)
                if len(_history) > MAX_HISTORY:
                    oldest = min(_history, key=lambda key: _history[key].get('gone_at', 0))
                    del _history[oldest]


def _udp_listener():
    global _udp_status
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((AIS_UDP_HOST, AIS_UDP_PORT))
        sock.settimeout(1.0)
        _udp_status = {'running': True, 'error': '', 'port': AIS_UDP_PORT}
    except OSError as exc:
        _udp_status = {'running': False, 'error': str(exc), 'port': AIS_UDP_PORT}
        return
    while True:
        try:
            data, _addr = sock.recvfrom(4096)
            for line in data.decode('ascii', errors='ignore').splitlines():
                msg = _parse_sentence(line)
                if msg:
                    _apply_message(msg)
        except socket.timeout:
            _sweep_gone()
        except Exception as exc:
            _udp_status['error'] = str(exc)
            time.sleep(0.2)


def _json_tcp_reader(proc: subprocess.Popen):
    global _decoder_status
    deadline = time.time() + 8
    while proc.poll() is None and time.time() < deadline:
        try:
            sock = socket.create_connection(('127.0.0.1', AIS_JSON_TCP_PORT), timeout=1.0)
            break
        except OSError:
            time.sleep(0.4)
    else:
        if proc.poll() is None:
            _decoder_status['error'] = f'JSON TCP port {AIS_JSON_TCP_PORT} not ready'
        return
    with sock:
        sock.settimeout(1.0)
        buf = ''
        while proc.poll() is None:
            try:
                data = sock.recv(4096).decode('utf-8', errors='ignore')
                if not data:
                    break
                buf += data
                while '\n' in buf:
                    line, buf = buf.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = _message_from_json(json.loads(line))
                    except json.JSONDecodeError:
                        msg = None
                    if msg:
                        _apply_message(msg)
            except socket.timeout:
                continue
            except OSError as exc:
                _decoder_status['error'] = str(exc)
                break


def _decoder_reader(proc: subprocess.Popen):
    if proc.stdout is None:
        return
    for raw in proc.stdout:
        line = raw.strip()
        msg = _parse_sentence(line)
        if msg:
            _apply_message(msg)


def _is_decoder_running() -> bool:
    proc = _decoder_proc
    return bool(proc and proc.poll() is None)


def _save_receiver_config():
    try:
        os.makedirs(os.path.dirname(RECEIVER_CONFIG_FILE), exist_ok=True)
        with open(RECEIVER_CONFIG_FILE, 'w') as f:
            json.dump({
                'source': _gps_source,
                'lat': _manual_receiver.get('lat') if _manual_receiver else None,
                'lon': _manual_receiver.get('lon') if _manual_receiver else None,
            }, f, indent=2)
    except Exception:
        pass


def _load_receiver_config():
    global _gps_source, _manual_receiver
    if not os.path.exists(RECEIVER_CONFIG_FILE):
        return
    try:
        with open(RECEIVER_CONFIG_FILE) as f:
            d = json.load(f)
        if d.get('source') in ('auto', 'opstoc', 'om', 'manual'):
            _gps_source = d.get('source')
        if _gps_source == 'manual' and d.get('lat') is not None and d.get('lon') is not None:
            _manual_receiver = {'lat': float(d['lat']), 'lon': float(d['lon'])}
    except Exception:
        pass


def _fetch_json(url: str, timeout: float = 2.0):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _gps_poll():
    global _manual_receiver, _gps_status, _receiver
    while True:
        try:
            if _gps_source == 'opstoc':
                d = _fetch_json(GPS_OPSTOC_URL)
                if d.get('lat') is not None and d.get('lon') is not None:
                    with _lock:
                        _manual_receiver = {'lat': float(d['lat']), 'lon': float(d['lon'])}
                        _gps_status = {'sats': d.get('sats'), 'fix': d.get('fix'), 'alt': d.get('alt')}
            elif _gps_source == 'om':
                d = _fetch_json(GPS_OM_URL)
                gps = d.get('gps') if isinstance(d, dict) else None
                if gps and gps.get('lat') is not None and gps.get('lon') is not None:
                    with _lock:
                        _manual_receiver = {'lat': float(gps['lat']), 'lon': float(gps['lon'])}
                        _gps_status = {'sats': gps.get('sats'), 'fix': gps.get('fix'), 'alt': gps.get('alt')}
            elif _gps_source == 'auto':
                try:
                    d = _fetch_json(GPS_OPSTOC_URL, timeout=1.0)
                    if d.get('lat') is not None and d.get('lon') is not None:
                        with _lock:
                            _receiver = {'lat': float(d['lat']), 'lon': float(d['lon'])}
                            _gps_status = {'sats': d.get('sats'), 'fix': d.get('fix'), 'alt': d.get('alt')}
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(GPS_POLL_INTERVAL)




def _message_rate_per_min() -> float:
    cutoff = time.time() - 60
    total = 0
    for source in (_vessels, _history):
        for vessel in source.values():
            if vessel.get('last_seen', 0) >= cutoff:
                total += vessel.get('messages', 0)
    # This is a lightweight session approximation; exact per-minute buckets can be
    # added later if needed.
    return round(total / 60.0, 2)


def _get_vessel_for_export(mmsi: str) -> dict | None:
    return _vessels.get(mmsi) or _history.get(mmsi) or _vessel_db.get(mmsi)


def _track_geojson(vessel: dict) -> dict:
    coords = [[p[1], p[0]] for p in vessel.get('track_points', []) if len(p) >= 2]
    return {
        'type': 'Feature',
        'properties': {k: v for k, v in vessel.items() if k != 'track_points'},
        'geometry': {'type': 'LineString', 'coordinates': coords},
    }


def _track_gpx(vessel: dict) -> str:
    name = str(vessel.get('name') or vessel.get('callsign') or vessel.get('mmsi') or 'AIS vessel')
    pts = vessel.get('track_points') or []
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="AIS App" xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <trk><name>{name}</name><trkseg>',
    ]
    for pt in pts:
        if len(pt) >= 2:
            lines.append(f'    <trkpt lat="{pt[0]}" lon="{pt[1]}"></trkpt>')
    lines.extend(['  </trkseg></trk>', '</gpx>'])
    return '\n'.join(lines) + '\n'

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html', asset_version=_asset_version())


@app.route('/api/vessels')
def get_vessels():
    with _lock:
        active = list(_vessels.values())
        history = list(_history.values())
        receiver = dict(_receiver)
        effective_receiver = dict(_manual_receiver or _receiver)
        rlat = effective_receiver.get('lat')
        rlon = effective_receiver.get('lon')
        for vessel in active:
            _estimate_receiver_cpa(vessel, rlat, rlon)
    closest = None
    farthest = None
    fastest = None
    for vessel in active:
        d = vessel.get('distance')
        label = vessel.get('name') or vessel.get('callsign') or vessel.get('mmsi')
        if d is not None:
            if closest is None or d < closest['distance']:
                closest = {'mmsi': vessel['mmsi'], 'name': label, 'distance': d}
            if farthest is None or d > farthest['distance']:
                farthest = {'mmsi': vessel['mmsi'], 'name': label, 'distance': d}
        spd = vessel.get('speed')
        if spd is not None and (fastest is None or spd > fastest['speed']):
            fastest = {'mmsi': vessel['mmsi'], 'name': label, 'speed': spd}
    return jsonify({
        'active': active,
        'history': history,
        'receiver': receiver,
        'effective_receiver': effective_receiver,
        'ais_running': _is_decoder_running(),
        'udp': _udp_status,
        'decoder': {**_decoder_status, 'running': _is_decoder_running(), 'json_port': AIS_JSON_TCP_PORT},
        'stats': {
            'active_count': len(active),
            'history_count': len(history),
            'message_count': _message_count,
            'known_vessels': len(_vessel_db),
            'last_message_time': _last_message_time,
            'last_message_age': round(time.time() - _last_message_time, 1) if _last_message_time else None,
            'messages_per_min': _message_rate_per_min(),
            'closest': closest,
            'farthest': farthest,
            'fastest': fastest,
        },
        'timestamp': time.time(),
    })


@app.route('/api/ais/start', methods=['POST'])
def ais_start():
    global _decoder_proc, _decoder_status
    if _is_decoder_running():
        return jsonify({'ok': True, 'already_running': True})
    exe = shlex.split(AIS_CMD)[0] if AIS_CMD else ''
    if not exe or not shutil.which(exe):
        _decoder_status = {'running': False, 'error': f'{exe or "AIS decoder"} not found', 'cmd': AIS_CMD, 'json_port': AIS_JSON_TCP_PORT}
        return jsonify({'ok': False, 'error': _decoder_status['error']}), 500
    try:
        _decoder_proc = subprocess.Popen(
            shlex.split(AIS_CMD), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1
        )
        _decoder_status = {'running': True, 'error': '', 'cmd': AIS_CMD, 'json_port': AIS_JSON_TCP_PORT}
        threading.Thread(target=_json_tcp_reader, args=(_decoder_proc,), daemon=True).start()
        threading.Thread(target=_decoder_reader, args=(_decoder_proc,), daemon=True).start()
        return jsonify({'ok': True})
    except Exception as exc:
        _decoder_status = {'running': False, 'error': str(exc), 'cmd': AIS_CMD, 'json_port': AIS_JSON_TCP_PORT}
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/ais/stop', methods=['POST'])
def ais_stop():
    global _decoder_proc, _decoder_status
    proc = _decoder_proc
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    _decoder_proc = None
    _decoder_status = {'running': False, 'error': '', 'cmd': AIS_CMD, 'json_port': AIS_JSON_TCP_PORT}
    return jsonify({'ok': True})


@app.route('/api/ais/restart', methods=['POST'])
def ais_restart():
    ais_stop()
    return ais_start()


@app.route('/api/receiver', methods=['GET'])
def get_receiver():
    with _lock:
        eff = _manual_receiver or _receiver or {}
    return jsonify({
        'source': _gps_source,
        'lat': eff.get('lat'),
        'lon': eff.get('lon'),
        'gps_status': _gps_status,
    })


@app.route('/api/receiver', methods=['POST'])
def set_receiver():
    global _gps_source, _manual_receiver, _gps_status
    data = request.get_json(silent=True) or {}
    source = data.get('source', 'auto')
    if source not in ('auto', 'opstoc', 'om', 'manual'):
        return jsonify({'ok': False, 'error': 'invalid source'}), 400
    with _lock:
        _gps_source = source
        if source == 'manual':
            lat = data.get('lat')
            lon = data.get('lon')
            if lat is not None and lon is not None:
                _manual_receiver = {'lat': float(lat), 'lon': float(lon)}
        elif source == 'auto':
            _manual_receiver = None
            _gps_status = {}
    _save_receiver_config()
    return jsonify({'ok': True})




@app.route('/api/vessels/<mmsi>/geojson')
def export_vessel_geojson(mmsi):
    with _lock:
        vessel = _get_vessel_for_export(mmsi)
        if not vessel:
            return jsonify({'ok': False, 'error': 'unknown vessel'}), 404
        return jsonify(_track_geojson(vessel))


@app.route('/api/vessels/<mmsi>/gpx')
def export_vessel_gpx(mmsi):
    with _lock:
        vessel = _get_vessel_for_export(mmsi)
        if not vessel:
            return jsonify({'ok': False, 'error': 'unknown vessel'}), 404
        payload = _track_gpx(vessel)
    return Response(
        payload,
        mimetype='application/gpx+xml',
        headers={'Content-Disposition': f'attachment; filename=ais-{mmsi}.gpx'},
    )


@app.route('/api/db/status')
def db_status():
    with _lock:
        return jsonify({
            'loaded': _db_loaded,
            'vessel_count': len(_vessel_db),
            'mid_count': len(MID_COUNTRIES),
            'updated': _vessel_db_meta.get('updated'),
            'source': _vessel_db_meta.get('source'),
            'error': _vessel_db_meta.get('error'),
            'path': VESSEL_DB_FILE,
        })


@app.route('/api/db/update', methods=['POST'])
def db_update():
    with _lock:
        count = _update_db_from_session()
    return jsonify({'ok': True, 'updated': count, 'vessel_count': len(_vessel_db)})


@app.route('/api/db/export')
def db_export():
    payload = json.dumps({'vessels': _vessel_db, 'meta': _vessel_db_meta}, indent=2, sort_keys=True)
    return Response(
        payload,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=ais-vessel-db.json'},
    )


@app.route('/api/db/import', methods=['POST'])
def db_import():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({'ok': False, 'error': 'Expected JSON body'}), 400
    try:
        with _lock:
            count = _merge_imported_vessels(data)
        return jsonify({'ok': True, 'imported': count, 'vessel_count': len(_vessel_db)})
    except ValueError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@app.route('/api/db/clear', methods=['POST'])
def db_clear():
    with _lock:
        _vessel_db.clear()
        _save_vessel_db('clear')
    return jsonify({'ok': True, 'vessel_count': 0})


@app.route('/api/version')
def get_version():
    try:
        commit = subprocess.check_output(
            ['git', '-C', BASE_DIR, 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        commit = '?'
    return jsonify({'version': APP_VERSION, 'commit': commit})


@app.route('/api/system/check-update', methods=['POST'])
def check_update():
    try:
        subprocess.run(['git', '-C', BASE_DIR, 'fetch'], capture_output=True, timeout=10)
        behind = subprocess.check_output(
            ['git', '-C', BASE_DIR, 'rev-list', 'HEAD..origin/main', '--count'],
            text=True
        ).strip()
        return jsonify({'ok': True, 'behind': int(behind)})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/system/restart', methods=['POST'])
def system_restart():
    def _do():
        time.sleep(0.8)
        subprocess.run(['systemctl', '--user', 'restart', 'ais-app'])
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/system/shutdown', methods=['POST'])
def system_shutdown():
    def _do():
        time.sleep(0.8)
        subprocess.run(['systemctl', '--user', 'stop', 'ais-app'])
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/system/update', methods=['POST'])
def system_update():
    r = subprocess.run(['git', '-C', BASE_DIR, 'pull', 'origin', 'main'], capture_output=True, text=True)
    if r.returncode != 0:
        return jsonify({'ok': False, 'error': r.stderr.strip()}), 500
    def _restart():
        time.sleep(1.5)
        subprocess.run(['systemctl', '--user', 'restart', 'ais-app'])
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'ok': True, 'output': r.stdout.strip()})


@app.route('/api/debug/nmea', methods=['POST'])
def debug_nmea():
    data = request.get_json(silent=True) or {}
    line = data.get('line', '')
    msg = _parse_sentence(line)
    if msg:
        _apply_message(msg)
    return jsonify({'ok': bool(msg), 'msg': msg})


_load_vessel_db()
_load_receiver_config()
threading.Thread(target=_udp_listener, daemon=True).start()
threading.Thread(target=_gps_poll, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('AIS_APP_PORT', '5410')), debug=False)
