from threading import Event

from Isabella.Voice.tts import TTSManager, prepare_for_speech
from Isabella.Voice.tts_base import SynthesizedAudio, VoiceInfo


CONFIG = {
    "cache_enabled": True,
    "cache_max_entries": 2,
    "queue_max_size": 2,
}


class FakeProvider:
    def __init__(self, name="fake", init_error=False, synth_error=False):
        self.name = name
        self.init_error = init_error
        self.synth_error = synth_error
        self.initialized = False
        self.synthesis_count = 0
        self.stop_count = 0
        self.shutdown_count = 0

    def initialize(self):
        if self.init_error:
            raise RuntimeError("init failed")
        self.initialized = True

    def list_voices(self):
        return [VoiceInfo("voice", "Voice", "pt-BR", "Female", self.name)]

    def synthesize(self, text):
        self.synthesis_count += 1
        if self.synth_error:
            raise RuntimeError("synthesis failed")
        return SynthesizedAudio(text.encode(), "fake", "voice", 10.0, 5.0)

    def speak(self, text):
        return self.synthesize(text)

    def stop(self):
        self.stop_count += 1

    def health_check(self):
        return self.initialized

    def supports_streaming(self):
        return False

    def shutdown(self):
        self.shutdown_count += 1
        self.initialized = False


class FakePlayer:
    def __init__(self):
        self.played = []
        self.stopped = False

    def play(self, audio):
        self.played.append(audio.data.decode())
        return 20.0

    def stop(self):
        self.stopped = True


def make_manager(primary=None, fallback=None, callback=None):
    player = FakePlayer()
    manager = TTSManager(
        CONFIG,
        primary=primary or FakeProvider("primary"),
        fallback=fallback or FakeProvider("fallback"),
        player=player,
        on_speaking_change=callback,
    )
    return manager, player


def test_manager_initialization_and_shutdown():
    manager, _ = make_manager()

    assert manager.initialize()
    assert manager.health_check()
    assert manager.shutdown()
    assert manager.state == "STOPPED"


def test_primary_failure_uses_fallback():
    primary = FakeProvider("primary", synth_error=True)
    fallback = FakeProvider("fallback")
    manager, player = make_manager(primary, fallback)
    manager.initialize()

    assert manager.speak("Olá")
    manager._queue.join()

    assert primary.synthesis_count == 1
    assert fallback.synthesis_count == 1
    assert player.played == ["Olá"]
    manager.shutdown()


def test_primary_initialization_failure_keeps_fallback():
    manager, player = make_manager(FakeProvider("primary", init_error=True), FakeProvider("fallback"))

    assert manager.initialize()
    assert manager.speak("Fallback")
    manager._queue.join()
    assert player.played == ["Fallback"]
    manager.shutdown()


def test_total_failure_enters_error_without_crashing():
    manager, _ = make_manager(FakeProvider("primary", synth_error=True), FakeProvider("fallback", synth_error=True))
    manager.initialize()
    manager.speak("Texto continua disponível")
    manager._queue.join()

    assert manager.state == "ERROR"
    manager.shutdown()


def test_empty_text_is_rejected():
    manager, _ = make_manager()
    manager.initialize()

    assert manager.speak("") is False
    assert manager.speak("   ") is False
    manager.shutdown()


def test_cache_avoids_second_synthesis():
    primary = FakeProvider("primary")
    manager, player = make_manager(primary, FakeProvider("fallback"))
    manager.initialize()

    manager.speak("Chrome aberto.")
    manager._queue.join()
    manager.speak("Chrome aberto.")
    manager._queue.join()

    assert primary.synthesis_count == 1
    assert player.played == ["Chrome aberto.", "Chrome aberto."]
    manager.shutdown()


def test_stop_interrupts_player_and_provider():
    primary = FakeProvider("primary")
    manager, player = make_manager(primary)
    manager.initialize()

    manager.stop()

    assert player.stopped
    assert primary.stop_count == 1
    manager.shutdown()


def test_speaking_callback_protects_listener():
    changes = []
    manager, _ = make_manager(callback=changes.append)
    manager.initialize()
    manager.speak("Teste")
    manager._queue.join()

    assert changes == [True, False]
    manager.shutdown()


def test_pronunciation_rules_do_not_change_visual_source():
    source = "ISABELLA usa CPU, GitHub e Faster Whisper."
    spoken = prepare_for_speech(source)

    assert source == "ISABELLA usa CPU, GitHub e Faster Whisper."
    assert spoken == "Isabela usa cê pê u, Guite Rãb e Fáster Uísper."
