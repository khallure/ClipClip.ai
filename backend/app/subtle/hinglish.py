"""Devanagari to Roman Hindi (Hinglish) transliteration (ported from 0xpratzyy/subtle MIT)."""
from __future__ import annotations

import re
import unicodedata

_CONS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'ng',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'n',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'ph', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'श': 'sh',
    'ष': 'sh', 'स': 's', 'ह': 'h', 'क़': 'q', 'ख़': 'kh',
    'ग़': 'g', 'ज़': 'z', 'ड़': 'r', 'ढ़': 'rh', 'फ़': 'f',
}
_VOW = {
    'अ': 'a', 'आ': 'aa', 'इ': 'i', 'ई': 'ee', 'उ': 'u',
    'ऊ': 'oo', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au',
    'ऋ': 'ri', 'ऑ': 'o',
}
_MATRA = {
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ृ': 'ri', 'ॉ': 'o',
}
_NUKTA, _VIRAMA = '़', '्'
_COMMON = {
    'में': 'mein', 'मैं': 'main', 'हैं': 'hain', 'है': 'hai', 'नहीं': 'nahi',
    'हम': 'hum', 'और': 'aur', 'यह': 'yeh', 'ये': 'yeh', 'वह': 'woh',
    'वो': 'woh', 'क्या': 'kya', 'क्यों': 'kyun', 'सिर्फ': 'sirf', 'लेकिन': 'lekin',
    'दोस्तों': 'doston', 'आप': 'aap', 'आपको': 'aapko', 'कि': 'ki', 'की': 'ki',
    'का': 'ka', 'के': 'ke', 'को': 'ko', 'से': 'se', 'पर': 'par',
    'हुआ': 'hua', 'हुई': 'hui', 'था': 'tha', 'थी': 'thi', 'थे': 'the',
    'भी': 'bhi', 'ही': 'hi', 'तो': 'toh', 'अब': 'ab', 'बहुत': 'bahut',
    'अच्छा': 'accha', 'कौन': 'kaun', 'कुछ': 'kuch', 'मुझे': 'mujhe',
    'इंडिया': 'India', 'इंडिय': 'India',
}
_LATIN_FIX = str.maketrans({
    'š': 'sh', 'č': 'ch', 'ñ': 'n', 'ā': 'aa', 'ī': 'ee',
    'ū': 'oo', 'ṭ': 't', 'ḍ': 'd', 'ṇ': 'n', 'ś': 'sh', 'ṣ': 'sh',
})

ROMAN_HINT = (
    'Yeh video Hinglish mein hai. Hindi ko Roman script mein likho aur English words ko English mein rakho: '
    'doston, aaj hum baat karenge, yeh sirf ek example nahi hai, kya aapko pata hai.'
)


def romanize_word(word: str) -> str:
    if word in _COMMON:
        return _COMMON[word]
    units = []
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        two = word[i:i + 2]
        if two in _CONS or ch in _CONS:
            base = _CONS[two] if two in _CONS else _CONS[ch]
            i += 2 if two in _CONS else 1
            if i < n and word[i] == _NUKTA:
                base = {'j': 'z', 'ph': 'f', 'k': 'q', 'd': 'r', 'dh': 'rh'}.get(base, base)
                i += 1
            if i < n and word[i] == _VIRAMA:
                units.append([base, False, True])
                i += 1
            elif i < n and word[i] in _MATRA:
                m = word[i]
                rom = _MATRA[m]
                if i + 1 < n and m in 'ाीू' and word[i + 1] not in 'ंँ':
                    rom = {'ा': 'aa', 'ी': 'ee', 'ू': 'oo'}[m]
                units.append([base + rom, False, True])
                i += 1
            else:
                units.append([base, True, True])
        elif ch in _VOW:
            units.append([_VOW[ch], False, False])
            i += 1
        elif ch in 'ंँ':
            units.append(['n', False, False])
            i += 1
        elif ch == 'ः':
            units.append(['h', False, False])
            i += 1
        else:
            units.append([ch, False, False])
            i += 1
    out = []
    for k, (rom, inherent, _cons) in enumerate(units):
        if inherent:
            last = k == len(units) - 1
            nxt = units[k + 1] if not last else None
            medial_drop = (k >= 1 and nxt is not None and nxt[2] and not nxt[1] and nxt[0][-1:] in 'aeiou')
            if (last and len(units) > 1) or medial_drop:
                pass
            else:
                rom += 'a'
        out.append(rom)
    return ''.join(out)


def romanize_text(text: str) -> str:
    text = re.sub(r'[ऀ-ॿ]+', lambda m: romanize_word(m.group(0)), text).translate(_LATIN_FIX)
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
