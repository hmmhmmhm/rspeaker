#!/usr/bin/env python3

import sys
import os
import time
import json
import re
import asyncio
import tempfile
import threading
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod

import edge_tts
import objc
from Foundation import NSLocale, NSRunLoop, NSDate, NSDefaultRunLoopMode
from AVFoundation import AVAudioEngine

try:
    objc.loadBundle(
        "Speech",
        module_globals=globals(),
        bundle_path="/System/Library/Frameworks/Speech.framework",
    )
except Exception as e:
    print(f"Speech 프레임워크를 로드할 수 없습니다: {e}")
    print("macOS 10.15 이상이 필요합니다.")
    sys.exit(1)

objc.registerMetaDataForSelector(
    b"SFSpeechRecognizer",
    b"requestAuthorization:",
    {
        "arguments": {
            2: {
                "callable": {
                    "retval": {"type": b"v"},
                    "arguments": {
                        0: {"type": b"^v"},
                        1: {"type": b"q"},
                    },
                }
            }
        }
    },
)

objc.registerMetaDataForSelector(
    b"SFSpeechRecognizer",
    b"recognitionTaskWithRequest:resultHandler:",
    {
        "arguments": {
            3: {
                "callable": {
                    "retval": {"type": b"v"},
                    "arguments": {
                        0: {"type": b"^v"},
                        1: {"type": b"@"},
                        2: {"type": b"@"},
                    },
                }
            }
        }
    },
)

DEFAULT_TIMEOUT = 12  # 기본 듣기 시간 (초)
EXTENDED_TIMEOUT = 30  # "이봐" 감지 후 듣기 시간 (초)
WAKE_WORDS = ["여보게", "여보께", "여보 게"]
STOP_SUFFIX = "줘"
NEWS_TRIGGERS = ["오늘 뉴스", "오늘의 뉴스"]
TIME_TRIGGERS = ["몇 시야", "몇시야", "몇 시 야", "몇시 야"]
DATE_TRIGGERS = ["몇 일이야", "몇일이야", "며칠이야", "며 칠이야"]
WEATHER_TRIGGERS = ["오늘 날씨", "날씨 알려 줘", "날씨", "몇 도야"]

NEWS_URL = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
NEWS_COUNT = 5
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
GEMINI_KEY_PAGE = "https://aistudio.google.com/apikey"
CONFIG_PATH = os.path.expanduser("~/.listener.json")

# ─── TTS 엔진 설정 ───────────────────────────────────────────────
# TTS_ENGINE: "edge" 또는 "typecast"
#   - 환경변수 TTS_ENGINE 으로 설정하거나
#   - ~/.listener.json 의 "tts_engine" 필드로 설정
#   - 기본값: "edge"
EDGE_TTS_VOICE = "ko-KR-SunHiNeural"

# Typecast TTS 설정
#   - TYPECAST_API_KEY: 환경변수 또는 ~/.listener.json 의 "typecast_api_key"
#   - TYPECAST_VOICE_ID: 환경변수 또는 ~/.listener.json 의 "typecast_voice_id"
#   - TYPECAST_MODEL: "ssfm-v30" (기본) 또는 "ssfm-v21"
TYPECAST_API_KEY_PAGE = "https://typecast.ai/developers/api"
TYPECAST_DEFAULT_MODEL = "ssfm-v30"


# ─── TTS 엔진 추상화 ─────────────────────────────────────────────

class TTSEngine(ABC):
    """TTS 엔진의 공통 인터페이스"""

    @abstractmethod
    def speak(self, text: str) -> None:
        """텍스트를 음성으로 출력합니다."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """엔진 이름을 반환합니다."""
        ...


class EdgeTTSEngine(TTSEngine):
    """Microsoft Edge TTS 엔진"""

    def __init__(self, voice: str = EDGE_TTS_VOICE):
        self.voice = voice

    @property
    def name(self) -> str:
        return f"Edge TTS ({self.voice})"

    def speak(self, text: str) -> None:
        async def _synthesize():
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(tmp_path)
            subprocess.run(["afplay", tmp_path])
            os.unlink(tmp_path)

        asyncio.run(_synthesize())


class TypecastTTSEngine(TTSEngine):
    """Typecast TTS 엔진 (typecast-python SDK 사용)"""

    def __init__(self, api_key: str, voice_id: str, model: str = TYPECAST_DEFAULT_MODEL):
        from typecast import Typecast

        self.voice_id = voice_id
        self.model = model
        self.client = Typecast(api_key=api_key)

    @property
    def name(self) -> str:
        return f"Typecast TTS ({self.voice_id}, {self.model})"

    def speak(self, text: str) -> None:
        from typecast.models import TTSRequest, Output

        request = TTSRequest(
            text=text,
            voice_id=self.voice_id,
            model=self.model,
            language="kor",
            output=Output(audio_format="mp3"),
        )
        response = self.client.text_to_speech(request)

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(response.audio_data)
            tmp_path = f.name

        subprocess.run(["afplay", tmp_path])
        os.unlink(tmp_path)


# 전역 TTS 엔진 인스턴스 (main에서 초기화됨)
_tts_engine: TTSEngine | None = None


def _print_voices(voices, numbered=False):
    """Typecast 보이스 목록을 포맷팅하여 출력합니다."""
    print("  ┌─ Typecast 보이스 목록 ─────────────────────────")
    for i, v in enumerate(voices, 1):
        models = ", ".join(m.version for m in v.models) if v.models else "N/A"
        gender = v.gender.value if v.gender else "?"
        if numbered:
            print(f"  │ [{i:3d}] {v.voice_id}  {v.voice_name}  ({gender}, {models})")
        else:
            print(f"  │ {v.voice_id}  {v.voice_name}  ({gender}, {models})")
    print(f"  └─ 총 {len(voices)}개 보이스")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def ensure_typecast_key():
    """Typecast API 키와 Voice ID를 확보합니다."""
    config = load_config()
    api_key = os.environ.get("TYPECAST_API_KEY", "") or config.get("typecast_api_key", "")
    voice_id = os.environ.get("TYPECAST_VOICE_ID", "") or config.get("typecast_voice_id", "")

    if not api_key:
        print(f"\n  TYPECAST_API_KEY가 설정되지 않았습니다.")
        print(f"  API 키 발급 페이지: {TYPECAST_API_KEY_PAGE}")
        subprocess.run(["open", TYPECAST_API_KEY_PAGE])
        api_key = input("  Typecast API Key를 입력하세요: ").strip()
        if api_key:
            config["typecast_api_key"] = api_key
            save_config(config)
            print(f"  API 키가 {CONFIG_PATH} 에 저장되었습니다.")
        else:
            print("  API 키가 입력되지 않았습니다. Edge TTS로 전환합니다.")
            return None, None

    if not voice_id:
        print("\n  Typecast Voice ID가 설정되지 않았습니다.")
        print("  사용 가능한 보이스를 조회합니다...\n")
        try:
            from typecast import Typecast
            temp_client = Typecast(api_key=api_key)
            voices = temp_client.voices_v2()
            _print_voices(voices, numbered=True)
            print()
            choice = input("  사용할 보이스 번호를 입력하세요 (또는 Voice ID 직접 입력): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(voices):
                    voice_id = voices[idx].voice_id
                    print(f"  선택됨: {voices[idx].voice_name} ({voice_id})")
                else:
                    print("  잘못된 번호입니다. Edge TTS로 전환합니다.")
                    return None, None
            elif choice:
                voice_id = choice
            else:
                print("  Voice ID가 입력되지 않았습니다. Edge TTS로 전환합니다.")
                return None, None

            config["typecast_voice_id"] = voice_id
            save_config(config)
            print(f"  Voice ID가 {CONFIG_PATH} 에 저장되었습니다.")
        except Exception as e:
            print(f"  보이스 목록 조회 실패: {e}")
            print("  Edge TTS로 전환합니다.")
            return None, None

    return api_key, voice_id


def init_tts_engine(engine_override=None, voice_override=None, model_override=None):
    """설정에 따라 TTS 엔진을 초기화합니다.

    Args:
        engine_override: CLI 등에서 전달받은 일회성 엔진 선택 (설정 파일 변경 없음)
        voice_override: CLI 등에서 전달받은 일회성 Voice ID
        model_override: CLI 등에서 전달받은 일회성 모델명
    """
    global _tts_engine

    config = load_config()
    engine_name = engine_override or os.environ.get("TTS_ENGINE", "") or config.get("tts_engine", "edge")
    engine_name = engine_name.lower().strip()

    if engine_name == "typecast":
        api_key, voice_id = ensure_typecast_key()
        if voice_override:
            voice_id = voice_override
        if api_key and voice_id:
            model = model_override or os.environ.get("TYPECAST_MODEL", "") or config.get("typecast_model", TYPECAST_DEFAULT_MODEL)
            try:
                _tts_engine = TypecastTTSEngine(api_key=api_key, voice_id=voice_id, model=model)
                print(f"  TTS 엔진: {_tts_engine.name}")
                return
            except Exception as e:
                print(f"  Typecast 초기화 실패: {e}")
                print("  Edge TTS로 폴백합니다.")

    # 기본값: Edge TTS
    _tts_engine = EdgeTTSEngine()
    print(f"  TTS 엔진: {_tts_engine.name}")


def ensure_gemini_key():
    global GEMINI_API_KEY
    if GEMINI_API_KEY:
        return
    config = load_config()
    if config.get("gemini_api_key"):
        GEMINI_API_KEY = config["gemini_api_key"]
        print("  Gemini API 키 로드됨 (설정 파일)")
        return
    print("  GEMINI_API_KEY가 설정되지 않았습니다.")
    print(f"  API 키 발급 페이지를 엽니다: {GEMINI_KEY_PAGE}")
    subprocess.run(["open", GEMINI_KEY_PAGE])
    GEMINI_API_KEY = input("  Gemini API Key를 입력하세요: ").strip()
    if GEMINI_API_KEY:
        config["gemini_api_key"] = GEMINI_API_KEY
        save_config(config)
        print(f"  API 키가 {CONFIG_PATH} 에 저장되었습니다.")
    else:
        print("  API 키가 입력되지 않았습니다. 뉴스 요약 없이 제목만 읽겠습니다.")


def fetch_news():
    try:
        req = urllib.request.Request(NEWS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        items = []
        for item in root.iter("item"):
            title = item.find("title")
            link = item.find("link")
            if title is not None and title.text and link is not None and link.text:
                items.append((title.text, link.text))
            if len(items) >= NEWS_COUNT:
                break
        return items
    except Exception as e:
        print(f"  뉴스를 가져올 수 없습니다: {e}")
        return []


def fetch_article_text(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # script, style 태그 제거
        html = re.sub(
            r"<(script|style|noscript)[^>]*>.*?</\1>",
            "",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # HTML 태그 제거
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:4000]
    except Exception as e:
        print(f"    기사를 가져올 수 없습니다: {e}")
        return ""


def fetch_weather(latitude, longitude):
    try:
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitude}&longitude={longitude}&hourly=temperature_2m,weather_code&forecast_days=1"
        )
        req = urllib.request.Request(weather_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        current_time_index = 0  # 첫 번째 시간(현재)
        six_hours_later_index = 6  # 6시간 후 (0부터 시작하므로 인덱스 6)

        current_temp = data["hourly"]["temperature_2m"][current_time_index]
        current_weather_code = data["hourly"]["weather_code"][current_time_index]
        six_hour_temp = data["hourly"]["temperature_2m"][six_hours_later_index]
        six_hour_weather_code = data["hourly"]["weather_code"][six_hours_later_index]

        weather_codes = {
            0: "맑음",
            1: "대체로 맑음",
            2: "부분적으로 흐림",
            3: "흐림",
            45: "안개",
            48: "서리 안개",
            51: "이슬비",
            53: "약한 이슬비",
            55: "강한 이슬비",
            56: "진눈깨비 이슬비",
            57: "강한 진눈깨비 이슬비",
            61: "약한 비",
            63: "보통 비",
            65: "강한 비",
            66: "진눈깨비 비",
            67: "강한 진눈깨비 비",
            71: "약한 눈",
            73: "보통 눈",
            75: "강한 눈",
            77: "싸락눈",
            80: "약한 소나기",
            81: "보통 소나기",
            82: "강한 소나기",
            85: "약한 눈 소나기",
            86: "강한 눈 소나기",
            95: "뇌우",
            96: "약한 우박 뇌우",
            99: "강한 우박 뇌우",
        }

        current_weather_desc = weather_codes.get(current_weather_code, "알 수 없음")
        six_hour_weather_desc = weather_codes.get(six_hour_weather_code, "알 수 없음")

        return {
            "current": {"temp": current_temp, "condition": current_weather_desc},
            "six_hour": {"temp": six_hour_temp, "condition": six_hour_weather_desc},
        }
    except Exception as e:
        print(f"  날씨 정보를 가져올 수 없습니다: {e}")
        return None


def speak_weather_info(suffix_message=""):
    print("\n  날씨 정보를 가져오는 중...")
    # 서울의 위도 경도
    seoul_lat, seoul_lon = 37.5665, 126.9780
    weather_data = fetch_weather(seoul_lat, seoul_lon)

    if weather_data:
        current = weather_data["current"]
        six_hour = weather_data["six_hour"]
        current_temp_str = str(current['temp'])
        six_hour_temp_str = str(six_hour['temp'])

        if current["temp"] < 0:
            current_temp_str = "영하 " + str(current["temp"] * -1)
        if six_hour["temp"] < 0:
            six_hour_temp_str = "영하 " + str(six_hour["temp"] * -1)

        weather_intro = "날씨입니다. 서울의 현재 날씨는"
        if not suffix_message: # Direct weather query
            weather_intro = "네. 서울의 현재 날씨는"

        weather_msg = (
            f"{weather_intro} {current['condition']}이고, 기온은 {current_temp_str}도입니다. "
            f"6시간 뒤 날씨는 {six_hour['condition']}이고, 기온은 {six_hour_temp_str}도로 예상됩니다."
        )
        if suffix_message:
            weather_msg += f" {suffix_message}"
        print(f"  {weather_msg}")
        speak_korean(weather_msg)
    else:
        speak_korean(f"날씨 정보를 가져올 수 없습니다.{' ' + suffix_message if suffix_message else ''}")
    print()


def _clean_tts_text(text):
    text = re.sub(r"\*+", "", text)  # ** bold **
    text = re.sub(r"#+\s*", "", text)  # ## 헤딩
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)  # - 불릿
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)  # 1. 번호
    text = re.sub(r"\[기사\s*\d+\]", "", text)  # [기사 N] 잔여
    text = re.sub(r"\s+", " ", text).strip()
    return text


def summarize_news_bulk(news_items_with_text):
    if not GEMINI_API_KEY:
        return [title for title, _ in news_items_with_text]

    articles = ""
    for i, (title, text) in enumerate(news_items_with_text, 1):
        content = text if text else title
        articles += f"[기사 {i}]\n제목: {title}\n본문: {content}\n\n"

    prompt = (
        f"다음 {len(news_items_with_text)}개 뉴스 기사를 각각 **방송용 멘트**처럼 요약해줘.\n"
        "아래 규칙을 반드시 지켜야 해:\n"
        "1. 각 기사를 **4~6개의 짧은 문장**으로 요약해.\n"
        "2. **모든 문장에 주어**를 명시해야 해. (예: '정부가 발표했습니다.')\n"
        "3. '그', '그녀', '그들' 같은 **지시대명사는 절대 사용하지 마**.\n"
        "4. 문장은 **6하원칙**(누가, 언제, 어디서, 무엇을, 어떻게, 왜)에 맞게 완결되어야 해.\n"
        "5. **신문사 이름**은 절대 언급하지 마.\n"
        "6. 인사, 서론, 부연 설명 없이 **요약 본문만 즉시 시작**해야 해.\n"
        "7. 마크다운, 번호 매기기 등 **서식을 사용하지 마**.\n"
        "8. 각 기사 요약은 `[기사 1]`, `[기사 2]` 형식으로 구분해줘.\n"
        "9. 이 대통령은 이명박 전 대통령이 아니라 이재명 대통령으로 읽어.\n\n"
        + articles
    )
    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
        }
    )
    try:
        req = urllib.request.Request(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
        raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()

        # [기사 N] 또는 **기사 N** 등 다양한 구분자로 분리 시도
        parts = re.split(r"\[기사\s*\d+\]|\*\*기사\s*\d+\*\*|\d+\.\s*기사", raw)
        parts = [_clean_tts_text(p) for p in parts if p.strip()]

        if len(parts) == len(news_items_with_text):
            return parts
        return [_clean_tts_text(raw)]
    except Exception as e:
        print(f"    요약 실패: {e}")
        return [title for title, _ in news_items_with_text]


def speak_korean(text):
    global _tts_engine
    if _tts_engine is None:
        # 폴백: 엔진이 초기화되지 않았으면 Edge TTS 사용
        _tts_engine = EdgeTTSEngine()
    _tts_engine.speak(text)


def request_authorization():
    event = threading.Event()
    result = [None]

    def callback(status):
        result[0] = status
        event.set()

    SFSpeechRecognizer.requestAuthorization_(callback)

    deadline = time.time() + 10
    while not event.is_set() and time.time() < deadline:
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode,
            NSDate.dateWithTimeIntervalSinceNow_(0.1),
        )

    # SFSpeechRecognizerAuthorizationStatusAuthorized == 3
    if result[0] != 3:
        print("음성 인식 권한이 거부되었습니다.")
        print("시스템 설정 > 개인정보 보호 및 보안 > 음성 인식 에서 허용해주세요.")
        sys.exit(1)

    print("  음성 인식 권한 확인됨")


def run_listen_session(recognizer, timeout=DEFAULT_TIMEOUT):
    engine = AVAudioEngine.alloc().init()
    input_node = engine.inputNode()

    request = SFSpeechAudioBufferRecognitionRequest.alloc().init()
    request.setShouldReportPartialResults_(True)

    ctx = {
        "text": "",
        "activated": False,
        "stop": False,
        "reason": "timeout",
        "t_start": time.time(),
        "t_activate": None,
        "timeout": timeout,
    }

    def on_result(result, error):
        if error is not None:
            ctx["stop"] = True
            ctx["reason"] = "error"
            return

        if result is None:
            return

        text = result.bestTranscription().formattedString()
        if not text or text == ctx["text"]:
            return

        ctx["text"] = text

        sys.stdout.write(f"\r\033[K\033[94m  {text}\033[0m")
        sys.stdout.flush()

        if not ctx["activated"] and any(w in text for w in WAKE_WORDS):
            ctx["activated"] = True
            ctx["t_activate"] = time.time()
            ctx["timeout"] = EXTENDED_TIMEOUT
            print(f"\n  ['여보게' 감지 → {EXTENDED_TIMEOUT}초 연장]")

        if any(t in text for t in NEWS_TRIGGERS):
            print()
            ctx["stop"] = True
            ctx["reason"] = "news"
            return

        if any(t in text for t in TIME_TRIGGERS):
            print()
            ctx["stop"] = True
            ctx["reason"] = "time"
            return

        if any(t in text for t in DATE_TRIGGERS):
            print()
            ctx["stop"] = True
            ctx["reason"] = "date"
            return

        if any(t in text for t in WEATHER_TRIGGERS):
            print()
            ctx["stop"] = True
            ctx["reason"] = "weather"
            return

        if text.rstrip().endswith(STOP_SUFFIX):
            print()
            ctx["stop"] = True
            ctx["reason"] = "command"

    task = recognizer.recognitionTaskWithRequest_resultHandler_(request, on_result)

    fmt = input_node.outputFormatForBus_(0)
    input_node.installTapOnBus_bufferSize_format_block_(
        0, 1024, fmt, lambda buf, _when: request.appendAudioPCMBuffer_(buf)
    )

    engine.prepare()
    ok, err = engine.startAndReturnError_(None)
    if not ok:
        print(f"오디오 엔진을 시작할 수 없습니다: {err}")
        return ("", "error")

    try:
        while not ctx["stop"]:
            now = time.time()

            if ctx["activated"]:
                if now - ctx["t_activate"] >= EXTENDED_TIMEOUT:
                    print(f"\n  [{EXTENDED_TIMEOUT}초 경과]")
                    break
            else:
                if now - ctx["t_start"] >= ctx["timeout"]:
                    print(f"\n  [{ctx['timeout']}초 경과]")
                    break

            NSRunLoop.currentRunLoop().runMode_beforeDate_(
                NSDefaultRunLoopMode,
                NSDate.dateWithTimeIntervalSinceNow_(0.1),
            )
    except KeyboardInterrupt:
        ctx["reason"] = "interrupt"
        print()

    engine.stop()
    input_node.removeTapOnBus_(0)
    request.endAudio()
    task.cancel()

    return (ctx["text"], ctx["reason"])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="rSpeaker - macOS 음성 인식 비서")
    parser.add_argument(
        "--tts",
        choices=["edge", "typecast"],
        default=None,
        help="사용할 TTS 엔진 (edge 또는 typecast, 기본: 설정 파일 값 또는 edge)",
    )
    parser.add_argument(
        "--typecast-voice",
        default=None,
        help="Typecast Voice ID (예: tc_672c5f5ce59fac2a48faeaee)",
    )
    parser.add_argument(
        "--typecast-model",
        default=None,
        choices=["ssfm-v21", "ssfm-v30"],
        help="Typecast 모델 (기본: ssfm-v30)",
    )
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="Typecast 사용 가능한 보이스 목록을 출력하고 종료",
    )
    args = parser.parse_args()

    # --list-voices 옵션이면 보이스 목록만 출력하고 종료
    if args.list_voices:
        config = load_config()
        api_key = os.environ.get("TYPECAST_API_KEY", "") or config.get("typecast_api_key", "")
        if not api_key:
            print("  Typecast API 키가 설정되지 않았습니다.")
            print(f"  API 키 발급: {TYPECAST_API_KEY_PAGE}")
            return
        try:
            from typecast import Typecast
            client = Typecast(api_key=api_key)
            voices = client.voices_v2()
            print()
            _print_voices(voices)
        except Exception as e:
            print(f"  보이스 목록 조회 실패: {e}")
        return

    print("=" * 55)
    print(f"  '여보게'          → {EXTENDED_TIMEOUT}초 연장")
    print(f"  '~{STOP_SUFFIX}'             → 종료")
    print(f"  '오늘 뉴스'       → 뉴스 읽기")
    print(f"  '몇시야'          → 현재 시간")
    print(f"  '몇일이야'        → 오늘 날짜")
    print(f"  '오늘 날씨'       → 날씨 정보")
    print("=" * 55)
    print()

    # TTS 엔진 초기화 (CLI 인자는 일회성 오버라이드, 설정 파일 변경 없음)
    init_tts_engine(
        engine_override=args.tts,
        voice_override=args.typecast_voice,
        model_override=args.typecast_model,
    )

    ensure_gemini_key()
    request_authorization()

    locale = NSLocale.alloc().initWithLocaleIdentifier_("ko-KR")
    recognizer = SFSpeechRecognizer.alloc().initWithLocale_(locale)

    if not recognizer or not recognizer.isAvailable():
        print("한국어 음성 인식을 사용할 수 없습니다.")
        sys.exit(1)

    print("\n  듣고 있습니다...\n")

    while True:
        text, reason = run_listen_session(recognizer)

        if reason == "time":
            from datetime import datetime

            now = datetime.now()
            hour = now.hour
            minute = now.minute
            period = "오전" if hour < 12 else "오후"
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
            msg = f"지금은 {period} {display_hour}시 {minute}분입니다."
            print(f"  {msg}")
            speak_korean(msg)
            print("\n  듣고 있습니다...\n")
            continue
        elif reason == "date":
            from datetime import datetime

            now = datetime.now()
            weekdays = ["월", "화", "수", "목", "금", "토", "일"]
            wd = weekdays[now.weekday()]
            msg = f"오늘은 {now.year}년 {now.month}월 {now.day}일 {wd}요일입니다."
            print(f"  {msg}")
            speak_korean(msg)
            print("\n  듣고 있습니다...\n")
            continue
        elif reason == "news":
            print("  뉴스를 가져오는 중...")
            news_items = fetch_news()
            if news_items:
                speak_korean("뉴스를 살펴보고 있습니다.")
                print("  뉴스를 살펴보고 있습니다...\n")
                items_with_text = []
                for i, (title, url) in enumerate(news_items, 1):
                    article_text = fetch_article_text(url)
                    items_with_text.append((title, article_text))
                print("\n  요약 중...\n")
                summaries = summarize_news_bulk(items_with_text)
                if len(summaries) == 1 and len(news_items) > 1:
                    # 구분 파싱 실패 → 전체 요약을 한 번에 읽기
                    print(f"  {summaries[0]}")
                    speak_korean(summaries[0])
                else:
                    for i, summary in enumerate(summaries, 1):
                        print(f"  {i}. {summary}")
                        prefix = "첫 뉴스입니다." if i == 1 else "다음 뉴스입니다."
                        speak_korean(f"{prefix} {summary}")

                # 뉴스 읽기가 끝나면 날씨 정보 읽기
                speak_weather_info("뉴스를 마칩니다.")
            else:
                print("  뉴스를 가져오지 못했습니다.\n")
            print("  듣고 있습니다...\n")
            continue
        elif reason == "weather":
            speak_weather_info()
            print("  듣고 있습니다...\n")
            continue
        elif reason == "command":
            print(f"\n  최종 인식 결과: {text}")
            print("  프로그램을 종료합니다.")
            break
        elif reason == "interrupt":
            print("  중단됨")
            break
        elif reason == "error":
            print("  오류 발생, 1초 후 재시도...\n")
            time.sleep(1)
        else:
            if text:
                print(f"  인식됨: {text}")
            print("\n  듣고 있습니다...\n")
            continue


if __name__ == "__main__":
    main()
