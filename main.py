"""ISABELLA temporary text entry point."""

import threading

from Isabella.Core.app import IsabellaApp
from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import BrainResponse, Intent, SkillRequest


OUTPUT_LOCK = threading.Lock()


def display_response(app: IsabellaApp, brain: Brain, response: BrainResponse, allow_confirmation: bool) -> None:
    with OUTPUT_LOCK:
        if response.response_type == Intent.CONVERSATION:
            print(f"\nISABELLA:\n{response.message}")
            app.speak(response.message)
            return
        print(f"\n[ROUTER] {response.response_type.value}")
        for result in response.skill_results:
            print(f"[SKILL] {result.skill_id}")
            print(f"[STATUS] {result.status}")
            print(f"\nISABELLA:\n{result.message}")
            if result.status != "confirmation_required":
                continue
            if not allow_confirmation:
                print("Confirme ações críticas pelo modo texto.")
                continue
            confirmation = input("Confirmar esta ação? (sim/não) ").strip().lower()
            if confirmation == "sim":
                request = response.skill_request or SkillRequest(
                    result.skill_id, result.data["arguments"]
                )
                confirmed = brain.confirm(request)
                print(f"[STATUS] {confirmed.status}")
                print(f"\nISABELLA:\n{confirmed.message}")
                app.speak(confirmed.message)
            else:
                print("[STATUS] cancelled")
                print("\nISABELLA:\nAção cancelada.")
                app.speak("Ação cancelada.")
        app.speak(response.message)


def main() -> None:
    app = IsabellaApp()
    try:
        app.start()
        brain = Brain.from_config()

        def handle_voice_command(command: str) -> None:
            display_response(app, brain, brain.process(command), allow_confirmation=False)

        voice_started = app.start_voice(handle_voice_command)
        tts_started = app.start_tts()
        voice_status = "ativa" if voice_started else "indisponível/desativada"
        tts_status = "ativa" if tts_started else "indisponível/desativada"
        print(f"Entrada por voz: {voice_status}. Saída por voz: {tts_status}. Digite um comando ou 'sair'.")
        while True:
            try:
                user_text = input("\nVocê:\n").strip()
            except EOFError:
                break
            if user_text.lower() == "sair":
                break
            if not user_text:
                continue

            display_response(app, brain, brain.process(user_text), allow_confirmation=True)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
