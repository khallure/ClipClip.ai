"""Turn spoken numbers into single caption tokens across English, Hindi, Spanish, and Indonesian."""
from __future__ import annotations

import re

ONES = {w: i for i, w in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen '
    'eighteen nineteen'.split())}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fourty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
SMALL_MULT = {'hundred': 100, 'thousand': 1000, 'hazaar': 1000, 'hazar': 1000, 'sau': 100}
BIG_MULT = {
    'million': 'million', 'billion': 'billion', 'trillion': 'trillion',
    'lakh': 'lakh', 'lakhs': 'lakh', 'lac': 'lakh', 'lacs': 'lakh',
    'crore': 'crore', 'crores': 'crore', 'karod': 'crore', 'cr': 'crore',
}
BIG_VALUE = {'million': 10 ** 6, 'billion': 10 ** 9, 'trillion': 10 ** 12, 'lakh': 10 ** 5, 'crore': 10 ** 7}
CURRENCY_WORD = {
    'dollar': '$', 'dollars': '$', 'bucks': '$', 'usd': '$', 'dolar': '$',
    'rupee': '₹', 'rupees': '₹', 'rupaye': '₹', 'rupay': '₹', 'rupiya': '₹', 'rupaiye': '₹', 'inr': '₹', 'rs': '₹',
    'euro': '€', 'euros': '€', 'pound': '£', 'pounds': '£',
    'rupiah': 'Rp', 'idr': 'Rp',
}
CURRENCY_PREFIX = {'rs': '₹', 'rs.': '₹', 'inr': '₹', 'usd': '$', 'rupees': '₹', 'rp': 'Rp'}
PERCENT_WORD = {'percent', 'percentage', 'pct', 'porciento', 'persen'}
PERCENT_PAIR = {('per', 'cent'), ('por', 'ciento')}
SIGNS = '$₹€£'

HINDI_ONES = {
    'ek': 1, 'do': 2, 'teen': 3, 'char': 4, 'chaar': 4, 'paanch': 5, 'panch': 5, 'chhe': 6,
    'cheh': 6, 'saat': 7, 'aath': 8, 'nau': 9, 'das': 10, 'bees': 20, 'pachas': 50, 'pachaas': 50,
}

# Spanish
ES_UNITS = {w: i for i, w in enumerate(
    'cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete '
    'dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco veintiseis veintisiete '
    'veintiocho veintinueve'.split())}
ES_UNITS.update({'un': 1, 'una': 1, 'veintiun': 21, 'veintiuna': 21})
ES_TENS = {'treinta': 30, 'cuarenta': 40, 'cincuenta': 50, 'sesenta': 60, 'setenta': 70, 'ochenta': 80, 'noventa': 90}
ES_HUNDREDS = {
    'cien': 100, 'ciento': 100, 'doscientos': 200, 'trescientos': 300, 'cuatrocientos': 400,
    'quinientos': 500, 'seiscientos': 600, 'setecientos': 700, 'ochocientos': 800, 'novecientos': 900,
}
ES_HUNDREDS.update({k[:-2] + 'as': v for k, v in list(ES_HUNDREDS.items()) if k.endswith('os')})
ES_BIG = {'millon': 'millón', 'millones': 'millón', 'billon': 'billón', 'billones': 'billón'}
ES_CURRENCY = {'dolar': '$', 'dolares': '$', 'euro': '€', 'euros': '€'}
ES_FOLD = str.maketrans('áéíóúü', 'aeiouu')
ES_DOTTED = re.compile(r'\d{1,3}(?:\.\d{3})+')
ES_DECIMAL_COMMA = re.compile(r'(\d{1,3}(?:\.\d{3})+|\d+),(\d{1,2})')
EURO_SWAP = str.maketrans(',.', '.,')

# Indonesian
ID_UNITS = {
    'nol': 0, 'kosong': 0, 'satu': 1, 'se': 1, 'dua': 2, 'tiga': 3, 'empat': 4,
    'lima': 5, 'enam': 6, 'tujuh': 7, 'delapan': 8, 'sembilan': 9,
}
ID_BIG = {'juta': 'juta', 'miliar': 'miliar', 'milyar': 'miliar', 'triliun': 'triliun'}
ID_PREFIX_NUMS = {'seratus': 100, 'seribu': 1000, 'sejuta': (1, 'juta'), 'semiliar': (1, 'miliar'), 'setriliun': (1, 'triliun')}

LEAD = re.compile(r'^[^\w$₹€£]+', re.UNICODE)
TRAIL = re.compile(r'[^\w%]+$', re.UNICODE)
DIGITS = re.compile(r'^\d[\d,]*(\.\d+)?$')


def _core(text: str) -> tuple[str, str, str]:
    lead_m = LEAD.match(text)
    lead = lead_m.group(0) if lead_m else ''
    rest = text[len(lead):]
    trail_m = TRAIL.search(rest)
    trail = trail_m.group(0) if trail_m else ''
    return lead, rest[:len(rest) - len(trail)] if trail else rest, trail


def group_digits(n: int | float, indian: bool = False) -> str:
    s = str(int(n))
    if not indian or len(s) <= 3:
        return f'{int(n):,}'
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ','.join(parts + [tail])


def _fmt(value: int | float, indian: bool = False, year_ok: bool = False, money: bool = False) -> str:
    if isinstance(value, float) and not value.is_integer():
        whole, frac = (f'{value:.2f}' if money else f'{value:.4f}'.rstrip('0')).split('.')
        return group_digits(int(whole), indian) + '.' + frac
    v = int(value)
    if year_ok and 1900 <= v <= 2100:
        return str(v)
    return group_digits(v, indian)


def _parse_digits(core: str, dotted_thousands: bool = False):
    if dotted_thousands:
        if ES_DOTTED.fullmatch(core):
            return int(core.replace('.', ''))
        m = ES_DECIMAL_COMMA.fullmatch(core)
        if m:
            return float(m.group(1).replace('.', '') + '.' + m.group(2))
    if not DIGITS.match(core):
        return None
    s = core.replace(',', '')
    return float(s) if '.' in s else int(s)


def _read_number(cores: list[str], i: int, hindi: bool = False):
    n = len(cores)
    j = i
    total, cur, used, spelled, any_num = 0, None, 0, False, False
    big = None
    year_pair = None
    while j < n:
        c = cores[j].lower()
        d = _parse_digits(cores[j])
        if d is not None and cur is None and not any_num:
            cur, any_num = d, True
            used += 1
        elif c in ONES and not (c == 'zero' and any_num):
            v = ONES[c]
            if cur is None:
                cur = v
            elif isinstance(cur, int) and cur >= 20 and cur % 10 == 0 and cur < 100 and 0 < v < 10:
                cur += v
            elif isinstance(cur, int) and cur % 100 == 0 and cur >= 100:
                cur += v
            elif isinstance(cur, int) and 10 <= cur <= 99 and v >= 10 and year_pair is None and total == 0:
                year_pair, cur = cur, v
            else:
                break
            any_num = spelled = True
            used += 1
        elif c in TENS:
            v = TENS[c]
            if cur is None:
                cur = v
            elif isinstance(cur, int) and cur % 100 == 0 and cur >= 100:
                cur += v
            elif isinstance(cur, int) and 10 <= cur <= 99 and year_pair is None and total == 0:
                year_pair, cur = cur, v
            else:
                break
            any_num = spelled = True
            used += 1
        elif hindi and c in HINDI_ONES and cur is None and j + 1 < n and (
                cores[j + 1].lower() in SMALL_MULT or cores[j + 1].lower() in BIG_MULT or cores[j + 1].lower() in CURRENCY_WORD):
            cur, any_num, spelled = HINDI_ONES[c], True, True
            used += 1
        elif c in SMALL_MULT and cur is not None:
            m = SMALL_MULT[c]
            if m == 100:
                if year_pair is not None or isinstance(cur, float):
                    break
                cur = cur * 100
            else:
                total += cur * m
                cur = None
            any_num = spelled = True
            used += 1
        elif c in BIG_MULT and cur is not None and big is None:
            big = BIG_MULT[c]
            spelled = True
            used += 1
            j += 1
            break
        elif c == 'and' and any_num and j + 1 < n and (cores[j + 1].lower() in ONES or cores[j + 1].lower() in TENS) \
                and ((cur is not None and isinstance(cur, int) and cur % 100 == 0) or (cur is None and total)):
            pass
        elif c == 'point' and cur is not None and j + 1 < n and (cores[j + 1].lower() in ONES or re.fullmatch(r'\d+', cores[j + 1])):
            k, frac = j + 1, ''
            while k < n and (cores[k].lower() in ONES and ONES[cores[k].lower()] < 10 or re.fullmatch(r'\d+', cores[k])):
                frac += str(ONES[cores[k].lower()]) if cores[k].lower() in ONES else cores[k]
                k += 1
            cur = float(f'{int(cur)}.{frac}')
            spelled = True
            used += k - j
            j = k
            continue
        else:
            break
        j += 1
    if not any_num:
        return None
    if year_pair is not None:
        if cur is None or total:
            return None
        value = year_pair * 100 + cur
        if not 1100 <= value <= 2099:
            return None
    else:
        value = total + (cur or 0)
    return value, big, j - i, spelled, used, year_pair is not None


def _read_number_es(cores: list[str], i: int, currency: dict[str, str]):
    n = len(cores)
    key = [c.lower().translate(ES_FOLD) for c in cores]
    j = i
    total, cur, used, spelled, any_num, digits = 0, None, 0, False, False, False
    big = None
    last = None
    while j < n:
        c = key[j]
        d = _parse_digits(cores[j], True)
        if d is not None and cur is None and not any_num:
            cur, any_num, digits, last = d, True, True, 'digits'
            used += 1
        elif c in ES_UNITS:
            v = ES_UNITS[c]
            if c in ('un', 'una') and not any_num:
                k = j + 2 if j + 1 < n and key[j + 1] in ES_BIG else j + 1
                if k > j + 1 and k < n and key[k] == 'de':
                    k += 1
                if not (k < n and key[k] in currency):
                    break
            if cur is None:
                cur = v
            elif last == 'hundreds' and v:
                cur += v
            else:
                break
            any_num = spelled = True
            last = 'unit'
            used += 1
        elif c in ES_TENS:
            if cur is None:
                cur = ES_TENS[c]
            elif last == 'hundreds':
                cur += ES_TENS[c]
            else:
                break
            any_num = spelled = True
            last = 'tens'
            used += 1
        elif c in ES_HUNDREDS and cur is None:
            cur, any_num, spelled, last = ES_HUNDREDS[c], True, True, 'hundreds'
            used += 1
        elif c == 'y' and last == 'tens' and j + 1 < n and 0 < ES_UNITS.get(key[j + 1], 0) < 10:
            cur += ES_UNITS[key[j + 1]]
            last = 'unit'
            used += 1
            j += 2
            continue
        elif c == 'mil' and last != 'mil' and not total:
            total = (1 if cur is None else cur) * 1000
            cur, any_num, spelled, last = None, True, True, 'mil'
            used += 1
        elif c in ES_BIG and (cur is not None or total):
            big = ES_BIG[c]
            spelled = True
            used += 1
            j += 1
            break
        elif c in ('coma', 'punto') and isinstance(cur, int) and last in ('digits', 'unit', 'tens') and j + 1 < n and (
                ES_UNITS.get(key[j + 1], 10) < 10 or re.fullmatch(r'\d+', cores[j + 1])):
            k, frac = j + 1, ''
            while k < n and (ES_UNITS.get(key[k], 10) < 10 or re.fullmatch(r'\d+', cores[k])):
                frac += str(ES_UNITS[key[k]]) if key[k] in ES_UNITS else cores[k]
                k += 1
            cur = float(f'{cur}.{frac}')
            spelled, last = True, 'fraction'
            used += k - j
            j = k
            continue
        else:
            break
        j += 1
    if not any_num:
        return None
    value = total + (cur or 0)
    is_year = not digits and big is None and isinstance(value, int) and 1900 <= value <= 2100 and value % 100 != 0
    return value, big, j - i, spelled, used, is_year


def _read_number_id(cores: list[str], i: int):
    """Read Indonesian number phrases: 'lima juta', 'dua puluh lima ribu', 'empat puluh', 'dua ribu dua puluh empat'."""
    n = len(cores)
    j = i
    total, cur, used, spelled, any_num, digits = 0, None, 0, False, False, False
    big = None
    while j < n:
        c = cores[j].lower()
        d = _parse_digits(cores[j], True)
        if d is not None and cur is None and not any_num:
            cur, any_num, digits = d, True, True
            used += 1
        elif c in ID_PREFIX_NUMS and cur is None and not any_num:
            val = ID_PREFIX_NUMS[c]
            if isinstance(val, tuple):
                cur, big = val[0], val[1]
                any_num = spelled = True
                used += 1
                j += 1
                break
            cur = val
            any_num = spelled = True
            used += 1
        elif c in {'sepuluh', 'sebelas'} and cur is None:
            cur = 10 if c == 'sepuluh' else 11
            any_num = spelled = True
            used += 1
        elif c in ID_UNITS and c != 'se':
            v = ID_UNITS[c]
            nxt = cores[j + 1].lower() if j + 1 < n else ''
            if nxt == 'belas' and 2 <= v <= 9 and cur is None:
                cur = 10 + v
                any_num = spelled = True
                used += 2
                j += 2
                continue
            if nxt == 'puluh' and 2 <= v <= 9 and cur is None:
                cur = v * 10
                any_num = spelled = True
                used += 2
                j += 2
                continue
            if nxt == 'ratus' and 2 <= v <= 9 and cur is None:
                cur = v * 100
                any_num = spelled = True
                used += 2
                j += 2
                continue
            if cur is None:
                cur = v
            elif isinstance(cur, int) and cur % 10 == 0 and 10 <= cur <= 990:
                cur += v
            else:
                break
            any_num = spelled = True
            used += 1
        elif c == 'ribu' and (cur is not None or total == 0):
            qty = 1 if cur is None else cur
            total += qty * 1000
            cur = None
            any_num = spelled = True
            used += 1
        elif c in ID_BIG and cur is not None and big is None:
            big = ID_BIG[c]
            spelled = True
            used += 1
            j += 1
            break
        elif c in ('koma', 'titik') and cur is not None and isinstance(cur, int) and j + 1 < n and (
                cores[j + 1].lower() in ID_UNITS or re.fullmatch(r'\d+', cores[j + 1])):
            k, frac = j + 1, ''
            while k < n and (cores[k].lower() in ID_UNITS and ID_UNITS[cores[k].lower()] < 10 or re.fullmatch(r'\d+', cores[k])):
                frac += str(ID_UNITS[cores[k].lower()]) if cores[k].lower() in ID_UNITS else cores[k]
                k += 1
            cur = float(f'{int(cur)}.{frac}')
            spelled = True
            used += k - j
            j = k
            continue
        else:
            break
        j += 1
    if not any_num:
        return None
    value = total + (cur or 0)
    is_year = not digits and big is None and isinstance(value, int) and 1900 <= value <= 2100 and value % 100 != 0
    return value, big, j - i, spelled, used, is_year


def normalise_numbers(words: list[dict], language: str | None = None, grouping: str = 'auto') -> list[dict]:
    """Rewrite number phrases into single unbreakable tokens ($5,000, 40%, Rp5 juta, 25.000)."""
    if grouping == 'off':
        return [dict(w) for w in words]
    lang = (language or '').lower()
    hindi = lang.startswith('hi')
    spanish = lang.startswith('es')
    indonesian = lang.startswith('id') or grouping == 'indonesian'
    parts = [_core(w['text']) for w in words]
    cores = [p[1] for p in parts]
    out: list[dict] = []
    i, n = 0, len(words)
    currency = {**CURRENCY_WORD, **ES_CURRENCY} if spanish else CURRENCY_WORD
    dotted = grouping in {'european', 'indonesian'} or (
        grouping == 'auto' and (
            (spanish or indonesian) and any(
                ES_DOTTED.fullmatch(c.strip('$€£%')) or ES_DECIMAL_COMMA.fullmatch(c.strip('$€£%'))
                for c in cores
            ) or indonesian
        )
    )

    def key(text: str) -> str:
        return text.lower().translate(ES_FOLD) if spanish else text.lower()

    def read(cs: list[str], k: int):
        if spanish:
            return _read_number_es(cs, k, currency)
        if indonesian:
            got_id = _read_number_id(cs, k)
            if got_id is not None:
                return got_id
        return _read_number(cs, k, hindi)

    def fmt(value: int | float, indian: bool, year_ok: bool, money: bool = False) -> str:
        s = _fmt(value, indian, year_ok, money)
        return s.translate(EURO_SWAP) if dotted else s

    def closed(a: int, b: int) -> bool:
        return all(not re.search(r'[.,!?;:…]', parts[k][2]) and not parts[k + 1][0] for k in range(a, b))

    while i < n:
        lead, core, trail = parts[i]
        low = core.lower()
        sign = ''
        start = i
        j = i
        # Rp 500, Rp5.000, Rs 500, etc.
        if low.rstrip('.') in CURRENCY_PREFIX and i + 1 < n and not re.search(r'[!?;:…,]', parts[i][2]) and (
                _parse_digits(cores[i + 1], dotted) is not None
                or cores[i + 1].lower() in ONES or cores[i + 1].lower() in TENS
                or cores[i + 1].lower() in ID_UNITS or cores[i + 1].lower() in ID_PREFIX_NUMS):
            sign = CURRENCY_PREFIX[low.rstrip('.')]
            j = i + 1
        elif low.startswith('rp') and len(low) > 2 and _parse_digits(core[2:], dotted) is not None:
            sign = 'Rp'
            cores[i] = core[2:]
        elif core[:1] in SIGNS and _parse_digits(core[1:], dotted) is not None:
            sign = core[0]
            cores[i] = core[1:]
        elif low in ('a', 'ek') and i + 1 < n and closed(i, i + 1) and (
                cores[i + 1].lower() in SMALL_MULT or cores[i + 1].lower() in BIG_MULT) and (
                    low == 'a' or hindi) and (
                        cores[i + 1].lower() in SMALL_MULT or (
                            i + 2 < n and closed(i + 1, i + 2) and cores[i + 2].lower() in CURRENCY_WORD)):
            cores[i] = 'one'
        got = read(cores, j)
        if got is None:
            pat = r'([$₹€£]?)(\d(?:[\d.,]*\d)?)(%|x|k|K|M|B)?' if dotted else r'([$₹€£]?)(\d[\d,]*(?:\.\d+)?)(%|x|k|K|M|B)?'
            m = re.fullmatch(pat, core)
            v = _parse_digits(m.group(2), dotted) if m else None
            if v is not None:
                indian = grouping == 'indian' or (grouping == 'auto' and (m.group(1) == '₹' or hindi))
                plain = m.group(2).isdigit() if dotted else ',' not in m.group(2)
                txt = (m.group(1) or '') + fmt(v, indian, year_ok=not m.group(1) and not m.group(3) and plain,
                                               money=bool(m.group(1)) and not m.group(3)) + (m.group(3) or '')
                w = dict(words[i])
                w['text'] = lead + txt + trail
                out.append(w)
            else:
                out.append(dict(words[i]))
            i += 1
            continue
        value, big, count, spelled, used, is_year = got
        end = j + count
        while end - 1 > j and not closed(j, end - 1):
            end -= 1
            got = None
        if got is None:
            sub = read(cores[:end], j)
            if sub is None:
                out.append(dict(words[i]))
                i += 1
                continue
            value, big, count, spelled, used, is_year = sub
            end = j + count
        suffix = ''
        nxt = key(cores[end]) if end < n and closed(end - 1, end) else ''
        nxt2 = key(cores[end + 1]) if nxt and end + 1 < n and closed(end, end + 1) else ''
        if nxt in currency and not sign:
            sign = currency[nxt]
            end += 1
        elif spanish and big and nxt == 'de' and nxt2 in currency and not sign:
            sign = currency[nxt2]
            end += 2
        elif nxt in PERCENT_WORD or (nxt, nxt2) in PERCENT_PAIR:
            suffix = '%'
            end += 1 if nxt in PERCENT_WORD else 2
        only_digit = count == 1 and not spelled
        if not (sign or suffix or big or used >= 2 or only_digit):
            out.append(dict(words[i]))
            i += 1
            continue
        indian = grouping == 'indian' or (grouping == 'auto' and (sign == '₹' or hindi or big in ('lakh', 'crore')))
        bare_year = only_digit and not sign and not suffix and (cores[j].isdigit() if dotted else ',' not in cores[j])
        txt = sign + fmt(value, indian, year_ok=(bare_year or is_year) and not sign and not suffix and not big,
                         money=bool(sign) and not suffix and not big)
        if big:
            txt += ' ' + (big[:-2] + 'ones' if spanish and value != 1 else big)
        txt += suffix
        w = dict(words[start])
        w['text'] = parts[start][0] + txt + parts[end - 1][2]
        w['endMs'] = max(x['endMs'] for x in words[start:end])
        if end - start > 1:
            w['prob'] = None
        out.append(w)
        i = end
    return out
