# rSpeaker

이 프로젝트는 macOS에서 동작하는 음성 인식 비서 애플리케이션입니다. Python을 사용하여 `SFSpeechRecognizer` 프레임워크와 연동하여 음성을 인식하고, 사용자의 명령어에 따라 다양한 작업을 수행합니다.

## 주요 기능

- **음성 인식 기반 제어**: "여보게"라는 호출 명령어를 통해 활성화됩니다.
- **듀얼 TTS 엔진 지원**: **Edge TTS**와 **Typecast TTS** 두 가지 음성 합성 엔진을 선택하여 사용할 수 있습니다.
  - **Edge TTS**: Microsoft Edge의 무료 TTS (기본값, `ko-KR-SunHiNeural`)
  - **Typecast TTS**: [Typecast AI](https://typecast.ai/developers/api)의 고품질 AI 음성 (API 키 필요, 무료 사용량 제공)
- **뉴스 제공**: "오늘 뉴스"라고 말하면 Google 뉴스의 주요 기사를 [Gemini AI](https://aistudio.google.com/apikey)로 요약하고, 서울의 현재 날씨 정보와 함께 음성으로 읽어줍니다.
- **시간 및 날짜 안내**: "몇 시야" 또는 "몇일이야"와 같은 명령어로 현재 시간과 날짜를 음성으로 안내합니다.

## 요구 사항

- **운영체제**: macOS 10.15 Catalina 이상
- **Python**: 3.13 이상
- **`uv`**: 프로젝트 의존성 관리를 위해 `uv`가 필요합니다.
  ```sh
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Gemini API 키**: 뉴스 요약 기능을 사용하려면 Google Gemini API 키가 필요합니다. 프로그램 첫 실행 시 API 키를 입력하라는 메시지가 표시되며, [여기](https://aistudio.google.com/apikey)에서 발급받을 수 있습니다.
- **Typecast API 키** (선택): Typecast TTS를 사용하려면 [Typecast Developer](https://typecast.ai/developers/api)에서 API 키를 발급받아야 합니다.

## 설치

프로젝트에 필요한 의존성을 설치하려면 아래 명령어를 실행하세요.

```sh
make sync
```

## 실행

### 기본 실행 (이전 설정 유지)

```sh
make run
```

### Edge TTS로 실행

```sh
make run-edge
```

### Typecast TTS로 실행

```sh
make run-typecast
```

최초 Typecast TTS 실행 시 API 키와 Voice ID를 입력하는 과정이 진행됩니다. 한번 입력하면 `~/.listener.json`에 저장되어 다음부터 자동으로 사용됩니다.

### Typecast 보이스 목록 조회

```sh
make list-voices
```

### CLI 옵션

```sh
# Edge TTS로 실행
uv run python listener.py --tts edge

# Typecast TTS로 실행
uv run python listener.py --tts typecast

# 특정 Typecast 보이스 및 모델 지정
uv run python listener.py --tts typecast --typecast-voice tc_672c5f5ce59fac2a48faeaee --typecast-model ssfm-v30

# 보이스 목록 보기
uv run python listener.py --list-voices
```

## TTS 엔진 설정

TTS 엔진 설정은 아래 우선순위로 적용됩니다:

1. **CLI 인자**: `--tts edge` 또는 `--tts typecast`
2. **환경변수**: `TTS_ENGINE=edge` 또는 `TTS_ENGINE=typecast`
3. **설정 파일**: `~/.listener.json`의 `tts_engine` 필드
4. **기본값**: `edge`

### 환경변수

| 변수                | 설명                                       | 기본값     |
| ------------------- | ------------------------------------------ | ---------- |
| `TTS_ENGINE`        | TTS 엔진 선택 (`edge` 또는 `typecast`)     | `edge`     |
| `TYPECAST_API_KEY`  | Typecast API 키                            | -          |
| `TYPECAST_VOICE_ID` | Typecast Voice ID                          | -          |
| `TYPECAST_MODEL`    | Typecast 모델 (`ssfm-v21` 또는 `ssfm-v30`) | `ssfm-v30` |
| `GEMINI_API_KEY`    | Gemini API 키 (뉴스 요약용)                | -          |

### 설정 파일 (`~/.listener.json`)

```json
{
  "gemini_api_key": "your-gemini-api-key",
  "tts_engine": "typecast",
  "typecast_api_key": "your-typecast-api-key",
  "typecast_voice_id": "tc_672c5f5ce59fac2a48faeaee",
  "typecast_model": "ssfm-v30"
}
```

## 명령어 목록

| 명령어                     | 설명                                           |
| -------------------------- | ---------------------------------------------- |
| "여보게"                   | 기본 12초인 듣기 시간을 30초로 연장합니다.     |
| "오늘 뉴스"                | 최신 뉴스를 요약하고 날씨를 알려줍니다.        |
| "몇 시야"                  | 현재 시간을 알려줍니다.                        |
| "몇일이야"                 | 오늘 날짜를 알려줍니다.                        |
| "오늘 날씨" / "몇 도야"    | 서울의 현재 날씨와 6시간 후 예보를 알려줍니다. |
| "...해줘" (예: "종료해줘") | 프로그램 실행을 종료합니다.                    |
| `Ctrl + C`                 | 터미널에서 직접 프로그램을 종료합니다.         |
