"""Gemini structured text adapter for the existing two-pass clipping reviewer.

Only the fixed Google endpoint receives the backend-held credential. Provider
errors are deliberately reduced to safe codes; raw bodies, requests and headers
must never become a job message or a repr. Full Pydantic validation remains local.
"""
from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError


_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/'
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_ERRORS = {
    'auth': 'Gemini belum dapat digunakan: API key tidak valid, dicabut, atau tidak memiliki akses. Periksa konfigurasi backend.',
    'quota': 'Kuota Gemini untuk proyek ini habis atau belum tersedia. Periksa kuota Free Tier di Google AI Studio, lalu coba lagi setelah kuota tersedia.',
    'rate_limit': 'Gemini sedang membatasi jumlah permintaan. Tunggu sebentar lalu coba lagi.',
    'model': 'Model Gemini yang dikonfigurasi tidak tersedia untuk akun ini. Periksa nama model dan akses di Google AI Studio.',
    'network': 'Koneksi ke Gemini belum berhasil. Periksa internet lalu coba lagi.',
    'invalid_request': 'Gemini menolak format permintaan analisis. Periksa model dan konfigurasi backend.',
    'server': 'Layanan Gemini belum dapat menyelesaikan analisis. Coba lagi nanti.',
    'blocked': 'Gemini tidak menyelesaikan analisis karena pembatasan respons. Gunakan hasil heuristik dengan pemeriksaan manual.',
    'invalid_response': 'Respons analisis Gemini tidak lengkap atau tidak sesuai format yang diperlukan. Hasil tersebut tidak dipakai sebagai penilaian AI.',
}


class GeminiProviderError(RuntimeError):
    """Safe failure metadata, suitable for the pipeline's existing fallback."""
    def __init__(self, code: str, *, status_code: int = 0, retryable: bool = False):
        self.code = code if code in _ERRORS else 'invalid_response'
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(_ERRORS[self.code])


def _json_schema(schema: dict) -> dict:
    """Inline local finite refs and represent simple nullable types directly.

    The schemas used by the clipper have no recursion. Reject recursive/external
    refs rather than guessing a weaker response contract. Object property names
    are data, so a property named ``title`` must not be stripped as metadata.
    """
    root = copy.deepcopy(schema)

    def resolve(node: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(node, list):
            return [resolve(value, seen) for value in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            reference = node['$ref']
            if not isinstance(reference, str) or not reference.startswith('#/') or reference in seen:
                raise GeminiProviderError('invalid_request')
            target: Any = root
            try:
                for part in reference[2:].split('/'):
                    target = target[part.replace('~1', '/').replace('~0', '~')]
            except (KeyError, TypeError):
                raise GeminiProviderError('invalid_request') from None
            if not isinstance(target, dict):
                raise GeminiProviderError('invalid_request')
            merged = {**target, **{key: value for key, value in node.items() if key != '$ref'}}
            return resolve(merged, seen | {reference})
        result = {}
        for key, value in node.items():
            if key in {'$defs', '$schema', 'title', 'default'}:
                continue
            if key == 'properties':
                result[key] = {name: resolve(child, seen) for name, child in value.items()}
            else:
                result[key] = resolve(value, seen)
        alternatives = result.get('anyOf')
        if (isinstance(alternatives, list) and len(alternatives) == 2
                and all(isinstance(item, dict) and set(item) == {'type'}
                        and isinstance(item['type'], str) for item in alternatives)
                and any(item['type'] == 'null' for item in alternatives)):
            result.pop('anyOf')
            result['type'] = [item['type'] for item in alternatives]
        return result

    return resolve(root)


def _http_error(response: httpx.Response) -> GeminiProviderError:
    status = response.status_code
    if status in {401, 403}:
        return GeminiProviderError('auth', status_code=status)
    if status == 404:
        return GeminiProviderError('model', status_code=status)
    if status == 429:
        # Only inspect the response in memory for classification. Never retain it
        # or attach the HTTP response/request to the exception shown to the user.
        body = response.text[:100_000].casefold()
        hard_quota = any(marker in body for marker in (
            'perday', 'per_day', 'per day', 'daily', 'quota exhausted',
            'credit_balance', 'billing', 'limit: 0', 'limit:0', '"quotavalue":"0"',
            '"quotavalue": "0"', '"quotavalue":0', '"quotavalue": 0',
        ))
        return GeminiProviderError('quota' if hard_quota else 'rate_limit',
                                   status_code=status, retryable=not hard_quota)
    if status >= 500 or status == 408:
        return GeminiProviderError('server', status_code=status, retryable=True)
    return GeminiProviderError('invalid_request', status_code=status)


def _parsed_response(payload: Any, schema: type[BaseModel]) -> BaseModel:
    if not isinstance(payload, dict):
        raise GeminiProviderError('invalid_response')
    feedback = payload.get('promptFeedback')
    if feedback is not None:
        if not isinstance(feedback, dict):
            raise GeminiProviderError('invalid_response')
        if feedback.get('blockReason') not in (None, '', 'BLOCK_REASON_UNSPECIFIED'):
            raise GeminiProviderError('blocked')
        if _blocked_ratings(feedback):
            raise GeminiProviderError('blocked')
    candidates = payload.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise GeminiProviderError('invalid_response')
    candidate = candidates[0]
    if candidate.get('finishReason') != 'STOP':
        code = 'blocked' if candidate.get('finishReason') in {
            'SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII', 'LANGUAGE',
            'IMAGE_SAFETY', 'PUP_LIMITED_DISABLED', 'ESCALATION',
        } else 'invalid_response'
        raise GeminiProviderError(code)
    if _blocked_ratings(candidate):
        raise GeminiProviderError('blocked')
    content = candidate.get('content')
    parts = content.get('parts') if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise GeminiProviderError('invalid_response')
    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            raise GeminiProviderError('invalid_response')
        # Models can include separately marked thinking text. It is not the JSON
        # answer and must not be displayed or parsed as transcript evidence.
        if part.get('thought') is True:
            continue
        if (not isinstance(part.get('text'), str)
                or any(key not in {'text', 'thought', 'thoughtSignature'} for key in part)):
            raise GeminiProviderError('invalid_response')
        chunks.append(part['text'])
    if not chunks:
        raise GeminiProviderError('invalid_response')
    try:
        return schema.model_validate_json(''.join(chunks))
    except (ValidationError, ValueError, TypeError):
        raise GeminiProviderError('invalid_response') from None


def _blocked_ratings(container: dict) -> bool:
    ratings = container.get('safetyRatings', [])
    if not isinstance(ratings, list) or any(not isinstance(rating, dict) for rating in ratings):
        raise GeminiProviderError('invalid_response')
    return any(rating.get('blocked') is True for rating in ratings)


class GeminiClient:
    """Small stateless REST client with the response.parse protocol used by V1."""
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 90):
        if not isinstance(api_key, str) or not api_key.strip() or '\r' in api_key or '\n' in api_key:
            raise GeminiProviderError('auth')
        self.__api_key = api_key.strip()
        self.__transport = transport
        self.__timeout = timeout
        self.responses = self

    def __repr__(self):
        return 'GeminiClient(credential=<redacted>)'

    def parse(self, *, model: str, instructions: str, input: str, text_format: type[BaseModel],
              store: bool = False, max_output_tokens: int = 10000):
        if (not isinstance(model, str) or not _MODEL.fullmatch(model)
                or not isinstance(instructions, str) or not isinstance(input, str)
                or not isinstance(text_format, type) or not issubclass(text_format, BaseModel)
                or isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int)
                or not 1 <= max_output_tokens <= 65536 or store is not False):
            raise GeminiProviderError('invalid_request')
        request = {
            'systemInstruction': {'parts': [{'text': instructions}]},
            'contents': [{'role': 'user', 'parts': [{'text': input}]}],
            'generationConfig': {'responseMimeType': 'application/json',
                                 'responseJsonSchema': _json_schema(text_format.model_json_schema()),
                                 'maxOutputTokens': max_output_tokens, 'candidateCount': 1},
        }
        try:
            with httpx.Client(transport=self.__transport, timeout=self.__timeout,
                              follow_redirects=False, trust_env=False) as client:
                response = client.post(f'{_BASE}{model}:generateContent',
                                       headers={'x-goog-api-key': self.__api_key}, json=request)
            if response.status_code != 200:
                raise _http_error(response)
            try:
                data = response.json()
            except (ValueError, UnicodeError):
                raise GeminiProviderError('invalid_response') from None
            return SimpleNamespace(output_parsed=_parsed_response(data, text_format))
        except GeminiProviderError:
            raise
        except httpx.RequestError:
            raise GeminiProviderError('network', retryable=True) from None
        except (ValueError, TypeError, OverflowError):
            raise GeminiProviderError('invalid_request') from None
