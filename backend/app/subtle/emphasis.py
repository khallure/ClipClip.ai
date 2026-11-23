"""Keyword emphasis selection, manual pick/drop overrides, page holds, and big-word generation."""
from __future__ import annotations

from collections import namedtuple
import re

SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")
WEAK_END = set(
    "a an the of to in on at for from with by and or but nor so as if than that this these those my your his "
    "her its our their is are was were be been am i'm it's we're you're they're i you we he she they not no very just "
    "ka ki ke ko se mein me par aur ya hai hain tha thi the ek yeh ye woh wo kya nahi "
    "yang dan atau di ke dari untuk dengan pada oleh dalam adalah ini itu tersebut saya aku kamu dia kita kami mereka tidak ya "
    "nya pun sih kok lah kah nih tuh deh dong".split()
)
STOP = WEAK_END | set(
    "it there here what when where who how why which do does did have has had will would can could should "
    "may might must also then now about into over under up down out off again more most some any all each every "
    "apa siapa kapan mana bagaimana mengapa kenapa berapa bisa dapat harus mau akan sedang sudah telah belum "
    "juga lagi masih sangat lebih paling banyak sedikit semua setiap".split()
)
MODES = ('auto', 'numbers', 'off')

NUMBER_WORDS = set(
    'two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty sixty seventy eighty '
    'ninety dozen dozens hundred hundreds thousand thousands million millions billion billions trillion lakh lakhs crore crores '
    'half double twice triple zero '
    'dua tiga empat lima enam tujuh delapan sembilan sepuluh sebelas dua_puluh seratus seribu sejuta semiliar nol setengah'.split()
)
CONTRAST = set(
    'never nothing nobody none free broke zero always worst best biggest first only wrong secret impossible '
    'banned illegal dead '
    'tidak pernah bukan nol gratis bangkrut selalu terburuk terbaik terbesar pertama satu-satunya salah rahasia mustahil dilarang ilegal mati'.split()
)
NOT_NAMES = set(
    "i i'm i'll i've i'd ok okay yes no hi hello hey so and but well now actually maybe monday tuesday wednesday "
    "thursday friday saturday sunday halo hai oke ya nggak tidak tapi terus nah senin selasa rabu kamis jumat sabtu minggu".split()
)
DULL = set(
    "that's there's what's actually really basically literally something anything everything someone anyone "
    "everyone thing things saying said going being having doing getting making because through before after between "
    "together another other others always maybe quite rather though although while where which their there these those "
    "people person today tomorrow yesterday sebenarnya sebenernya intinya pokoknya kayaknya mungkin sesuatu seseorang "
    "orang hari besok kemarin karena terus jadi kalau".split()
)
MAX_HOLD_MS = 700

_NO_ACCENT = str.maketrans('áéíóúü', 'aeiouu')


def _es(text: str) -> set[str]:
    ws = set(text.split())
    return ws | {w.translate(_NO_ACCENT) for w in ws}


ES_WEAK_END = _es(
    'el la los las un una unos unas lo al del a de en con por para sin sobre entre hasta desde hacia tras '
    'según contra ante durante y e o u ni pero sino que si como porque aunque cuando mientras pues mi mis tu tus su sus nuestro '
    'nuestra nuestros nuestras este esta estos estas ese esa esos esas aquel aquella es son era eran fue soy eres somos está '
    'están estaba estoy he ha han hay no muy más tan me te se nos le les yo tú él ella nosotros ellos ellas usted ustedes'
)
ES_STOP = ES_WEAK_END | _es(
    'eso esto aquello ello qué quién quiénes cómo cuándo dónde cuál cuáles cuánto cuánta cuántos '
    'cuántas hace hacen hizo hago tiene tienen tengo tenía tuvo tenemos había hemos puede pueden puedo podía podría debe deben '
    'debería va van voy vamos iba ser estar haber sido estado será sería sea fueron estamos estás estaban estuvo también '
    'entonces ahora luego después antes ya aquí ahí allí allá acá algún alguna algunos algunas todo toda todos todas cada '
    'mucho mucha muchos muchas poco así'
)
ES_NUMBER_WORDS = _es(
    'dos tres cuatro cinco seis siete ocho nueve diez once doce veinte treinta cuarenta cincuenta sesenta '
    'setenta ochenta noventa cien ciento cientos docena docenas mil miles millón millones billón billones mitad doble triple cero'
)
ES_CONTRAST = _es(
    'nunca jamás nada nadie ninguno ninguna ningún gratis cero siempre peor mejor primero primera primer '
    'único única imposible prohibido prohibida ilegal secreto muerto muerta equivocado equivocada'
)
ES_NOT_NAMES = _es(
    'sí no ok okay hola bueno pues entonces pero y yo ahora quizás lunes martes miércoles jueves viernes sábado domingo'
)
ES_DULL = _es(
    'realmente básicamente literalmente simplemente obviamente algo alguien cualquier cualquiera cosa cosas '
    'diciendo dijo dice decir haciendo hacer siendo teniendo tener porque través antes después entre juntos junto otro otra '
    'otros otras quizá quizás bastante aunque mientras donde cual gente persona personas hoy mañana ayer bueno claro verdad '
    'digamos también entonces todavía además tampoco ahora siempre'
)

Lexicon = namedtuple('Lexicon', 'weak_end stop number_words contrast not_names dull')
DEFAULT_LEXICON = Lexicon(WEAK_END, STOP, NUMBER_WORDS, CONTRAST, NOT_NAMES, DULL)
SPANISH_LEXICON = Lexicon(
    WEAK_END | ES_WEAK_END,
    STOP | ES_STOP,
    NUMBER_WORDS | ES_NUMBER_WORDS,
    CONTRAST | ES_CONTRAST,
    NOT_NAMES | ES_NOT_NAMES,
    DULL | ES_DULL,
)
QUOTES = "\"'“‘(«"
OPENERS = QUOTES + '¿¡'
RULE_REASONS = ('name', 'name (acronym)', 'contrast word', 'count', 'long word (a guess)')


def lexicon(language: str | None = None) -> Lexicon:
    return SPANISH_LEXICON if (language or '').lower().startswith('es') else DEFAULT_LEXICON


def opens_sentence(text: str) -> bool:
    return text.lstrip(QUOTES)[:1] in ('¿', '¡')


def bare(text: str) -> str:
    return re.sub(r"[^\w']+", '', text, flags=re.UNICODE).lower()


def split_words(arg: str | list[str] | None) -> list[str]:
    if isinstance(arg, list):
        return [str(x).strip() for x in arg if str(x).strip()]
    return [x.strip() for x in re.split(r',(?!\d{3}(?!\d))\s*|;\s*', arg or '') if x.strip()]


def number_kind(text: str) -> str | None:
    core = re.sub(r'^[^\w$₹€£]+|[^\w%]+$', '', text, flags=re.UNICODE)
    if not core:
        return None
    if re.search(r'[$₹€£]', core) or core.lower().startswith('rp'):
        if re.search(r'\d', core) or core[:1] in '$₹€£':
            return 'price'
    if not re.search(r'\d', core):
        return None
    if core.endswith('%'):
        return 'percentage'
    if re.fullmatch(r'(1[5-9]|20)\d\d', core):
        return 'year'
    return 'number'


def classify(words: list[dict], i: int, names: set[str], starts: set[int] = frozenset(), lex: Lexicon = DEFAULT_LEXICON):
    text = words[i]['text']
    b = bare(text)
    if not b:
        return 0, None
    kind = number_kind(text)
    if kind:
        return {'price': 10, 'percentage': 9.6, 'year': 9.2, 'number': 9}[kind], kind
    if b in lex.stop and b not in lex.contrast:
        return 0, None
    letters = re.sub(r'[^A-Za-zÀ-ɏऀ-ॿ]', '', text)
    if len(letters) >= 2 and letters.isupper() and b not in lex.not_names:
        return 7.5, 'name (acronym)'
    if text.lstrip(OPENERS)[:1].isupper() and b not in lex.not_names and len(b) > 1:
        start = i == 0 or i in starts or opens_sentence(text) or bool(SENTENCE_END.search(words[i - 1]['text']))
        stem = b[:-2] if b.endswith("'s") else b
        if not start or stem in names:
            return 7, 'name'
    if b in lex.contrast:
        return 6, 'contrast word'
    if b in lex.number_words:
        return 5.5, 'count'
    return 0, None


def guess_score(text: str, lex: Lexicon = DEFAULT_LEXICON) -> float:
    b = bare(text)
    if not b or b in lex.stop or b in lex.dull or len(b) < 4:
        return 0.0
    s = min(len(b), 10) / 2.0
    if SENTENCE_END.search(text):
        s += 1.0
    return s


def rule_picks(pages: list[dict], mode: str = 'auto', forced=(), language: str | None = None) -> list[dict]:
    if mode not in MODES:
        raise ValueError(f"emphasis mode must be one of {', '.join(MODES)}")
    lex = lexicon(language)
    wanted = {bare(x) for x in forced if bare(x)}
    flat = [w for p in pages for w in p['words']]
    starts: set[int] = set()
    if not any(SENTENCE_END.search(w['text']) for w in flat):
        k = 0
        for p in pages:
            for j, w in enumerate(p['words']):
                if j == 0 or w.get('line') != p['words'][j - 1].get('line'):
                    starts.add(k)
                k += 1
        caps = [i for i, w in enumerate(flat) if w['text'].lstrip(OPENERS)[:1].isupper() and bare(w['text']) not in lex.not_names]
        at_start = sum(1 for i in caps if i in starts)
        if len(caps) < 3 or at_start < 0.6 * len(caps):
            starts = set()

    names: set[str] = set()
    for i, w in enumerate(flat):
        b = bare(w['text'])
        if (i and i not in starts and not opens_sentence(w['text'])
                and not SENTENCE_END.search(flat[i - 1]['text'])
                and w['text'].lstrip(OPENERS)[:1].isupper()
                and b not in lex.not_names and b not in lex.stop):
            names.add(b[:-2] if b.endswith("'s") else b)

    seen: dict[str, int] = {}
    at = 0
    quiet = 1

    def key(text: str) -> str:
        b = bare(text)
        return b[:-2] if b.endswith("'s") else b

    for p in pages:
        ws = p['words']
        for w in ws:
            w['emphasis'] = False
            w.pop('reason', None)
        slots = 2 if len(ws) >= 5 else 1
        used = 0
        for w in ws:
            if bare(w['text']) in wanted:
                w['emphasis'], w['reason'] = True, 'dipilih khusus (--emphasis-words)'
                used += 1
        if mode != 'off' and used < slots:
            cands = []
            for j, w in enumerate(ws):
                if w['emphasis']:
                    continue
                score, reason = classify(flat, at + j, names, starts, lex)
                if mode == 'numbers' and reason not in ('price', 'percentage', 'year', 'number', 'count'):
                    continue
                if reason and not number_kind(w['text']):
                    score -= 2.0 * seen.get(key(w['text']), 0)
                if score >= 5:
                    cands.append((score, -j, j, reason))
            cands.sort(reverse=True)
            for _score, _, j, reason in cands:
                if used >= slots:
                    break
                ws[j]['emphasis'], ws[j]['reason'] = True, reason
                used += 1
                if reason.startswith('name') and j + 1 < len(ws) and not ws[j + 1]['emphasis']:
                    _s2, r2 = classify(flat, at + j + 1, names, starts, lex)
                    if r2 and r2.startswith('name') and not SENTENCE_END.search(ws[j]['text']):
                        ws[j + 1]['emphasis'], ws[j + 1]['reason'] = True, r2
            if mode == 'auto' and used == 0 and quiet >= 2:
                best = max(((guess_score(w['text'], lex), -j, j) for j, w in enumerate(ws)), default=(0.0, 0, 0))
                if best[0] >= 4.0 and seen.get(key(ws[best[2]]['text']), 0) == 0:
                    ws[best[2]]['emphasis'], ws[best[2]]['reason'] = True, 'long word (a guess)'
        hit = False
        for w in ws:
            if w['emphasis']:
                seen[key(w['text'])] = seen.get(key(w['text']), 0) + 1
                hit = True
        quiet = 0 if hit else quiet + 1
        at += len(ws)
    return pages


def big_words(pages: list[dict], behind_cfg: dict, duration_ms: int) -> list[dict]:
    max_big = int(behind_cfg.get('maxBigWords', 2))
    min_hold = int(behind_cfg.get('minHoldMs', 700))
    min_gap = int(behind_cfg.get('minGapMs', 250))
    max_chars = int(behind_cfg.get('maxChars', 11))
    every_ms = int(behind_cfg.get('everyMs', 1400))
    flat = [w for p in pages for w in p['words']]

    def rank(w: dict) -> int:
        if number_kind(w['text']):
            return 3
        r = w.get('reason') or ''
        if r.endswith('(a guess)'):
            return 0
        return 1 if r in RULE_REASONS else 2

    kinds = behind_cfg.get('kinds')
    numbers_behind = bool(behind_cfg.get('numbersBehind'))
    min_words = int(behind_cfg.get('minWords', 1))

    def kind_of(group: list[dict]) -> str:
        if any(number_kind(g['text']) for g in group):
            return 'number'
        return 'name' if any((g.get('reason') or '').startswith('name') for g in group) else 'word'

    def clean(g: dict) -> str:
        return re.sub(r'^[^\w$₹€£]+|[^\w%]+$', '', g['text'])

    cands = []
    i = 0
    while i < len(flat):
        w = flat[i]
        if w.get('emphasis'):
            group = [w]
            while (len(group) < max_big and i + 1 < len(flat) and flat[i + 1].get('emphasis')
                   and len(' '.join(bare(g['text']) for g in group + [flat[i + 1]])) <= max_chars):
                group.append(flat[i + 1])
                i += 1
            text = ' '.join(clean(g) for g in group)
            if text and len(text) <= max_chars and len(group) >= min_words and (not kinds or kind_of(group) in kinds):
                start = group[0]['startMs']
                b = {'text': text, 'startMs': start, 'endMs': max(group[-1]['endMs'], start + min_hold)}
                if any(number_kind(g['text']) for g in group) and not numbers_behind:
                    b['front'] = True
                cands.append((max(rank(g) for g in group), b))
        i += 1
    out: list[dict] = []
    for _, b in sorted(cands, key=lambda c: (-c[0], c[1]['startMs'])):
        if all(abs(b['startMs'] - o['startMs']) >= every_ms for o in out):
            out.append(b)
    out.sort(key=lambda b: b['startMs'])
    for a, b in zip(out, out[1:]):
        a['endMs'] = min(a['endMs'], b['startMs'] - min_gap)
    if out:
        out[-1]['endMs'] = min(out[-1]['endMs'], duration_ms)
    return [b for b in out if b['endMs'] - b['startMs'] >= 300]


def apply_manual_overrides(
    pages: list[dict],
    *,
    clear_auto: bool = False,
    picks: list[dict] | None = None,
    drops: list[dict] | None = None,
) -> None:
    if clear_auto:
        for p in pages:
            for w in p['words']:
                w['emphasis'] = False
                w.pop('reason', None)
    for item in picks or []:
        pi = item.get('page')
        wi = item.get('word_index')
        if isinstance(pi, int) and isinstance(wi, int) and 0 <= pi < len(pages) and 0 <= wi < len(pages[pi]['words']):
            pages[pi]['words'][wi]['emphasis'] = True
            pages[pi]['words'][wi]['reason'] = (str(item.get('reason') or '').strip() or 'dipilih pengguna')[:160]
    for item in drops or []:
        pi = item.get('page')
        wi = item.get('word_index')
        if isinstance(pi, int) and isinstance(wi, int) and 0 <= pi < len(pages) and 0 <= wi < len(pages[pi]['words']):
            pages[pi]['words'][wi]['emphasis'] = False
            pages[pi]['words'][wi].pop('reason', None)


def finalise(cap: dict, style: dict | None) -> dict:
    pages = cap['pages']
    fps = float(cap['video'].get('fps') or 30)
    frame = 1000.0 / fps
    dur = cap['video']['durationMs']
    for i, p in enumerate(pages):
        if not any(w.get('emphasis') for w in p['words']):
            continue
        nxt = pages[i + 1]['startMs'] if i + 1 < len(pages) else dur
        want = min(p['words'][-1]['endMs'] + MAX_HOLD_MS, nxt)
        want = int(want // frame * frame + 1e-6)
        if want > p['endMs']:
            p['endMs'] = want
    behind = (style or {}).get('behind') or {}
    if behind.get('enabled'):
        cap['big'] = big_words(pages, behind, dur)
        from .shots import mark_bigs
        mark_bigs(cap)
    return cap
