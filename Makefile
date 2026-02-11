sync:
	uv sync

run:
	uv run python listener.py

run-edge:
	uv run python listener.py --tts edge

run-typecast:
	uv run python listener.py --tts typecast

list-voices:
	uv run python listener.py --tts typecast --list-voices
