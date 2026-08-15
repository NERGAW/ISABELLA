"""Audible comparison of ISABELLA TTS candidates."""

import json
from pathlib import Path
import sys
from time import perf_counter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Isabella.Voice.models import load_voice_config
from Isabella.Voice.tts import EdgeTTSProvider, MemoryAudioPlayer, SAPIProvider


PHRASES = [
    "Olá. Eu sou Isabella. Todos os sistemas estão operacionais. Como posso ajudá-lo?",
    "Chrome aberto.",
    "Não consegui completar essa ação.",
    "O sistema está funcionando normalmente.",
]


def run_provider(provider, phrases):
    player = MemoryAudioPlayer()
    provider.initialize()
    results = []
    print(f"\nProvider: {provider.name}")
    for phrase in phrases:
        print(f"Voz: {getattr(provider, 'voice', getattr(provider, 'voice_name', ''))}")
        print(f"Frase: {phrase}")
        playback_started = perf_counter()
        if getattr(provider, "direct_playback", False):
            audio = provider.speak(phrase)
            speech_time = (perf_counter() - playback_started) * 1000
        else:
            audio = provider.synthesize(phrase)
            speech_time = player.play(audio)
        results.append(
            {
                "provider": provider.name,
                "voice": audio.voice,
                "phrase": phrase,
                "time_to_first_audio_ms": round(audio.time_to_first_audio_ms, 2),
                "synthesis_latency_ms": round(audio.synthesis_latency_ms, 2),
                "total_speech_time_ms": round(speech_time, 2),
                "playback_overhead_ms": round((perf_counter() - playback_started) * 1000, 2),
            }
        )
    provider.shutdown()
    return results


def main() -> None:
    config = load_voice_config()["tts"]
    providers = []
    for voice in ("pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural"):
        providers.append(EdgeTTSProvider(dict(config, voice=voice)))
    providers.append(SAPIProvider(config))
    print("Providers disponíveis: edge (online), sapi (local/offline)")
    print("Candidatas: Francisca Neural, Thalita Multilingual Neural, Microsoft Maria Desktop")
    results = []
    output = PROJECT_ROOT / "data" / "voice_test_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    for provider in providers:
        results.extend(run_provider(provider, PHRASES))
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nMétricas salvas em: {output}")


if __name__ == "__main__":
    main()
