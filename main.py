"""ISABELLA temporary text entry point."""

from Isabella.Core.app import IsabellaApp
from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import Intent, SkillRequest


def main() -> None:
    app = IsabellaApp()
    try:
        app.start()
        brain = Brain.from_config()
        print("Digite um comando ou 'sair'.")
        while True:
            try:
                user_text = input("\nVocê:\n").strip()
            except EOFError:
                break
            if user_text.lower() == "sair":
                break
            if not user_text:
                continue

            response = brain.process(user_text)
            if response.response_type == Intent.CONVERSATION:
                print(f"\nISABELLA:\n{response.message}")
            else:
                print(f"\n[ROUTER] {response.response_type.value}")
                for result in response.skill_results:
                    print(f"[SKILL] {result.skill_id}")
                    print(f"[STATUS] {result.status}")
                    print(f"\nISABELLA:\n{result.message}")
                    if result.status == "confirmation_required":
                        confirmation = input("Confirmar esta ação? (sim/não) ").strip().lower()
                        if confirmation == "sim":
                            request = response.skill_request or SkillRequest(
                                result.skill_id, result.data["arguments"]
                            )
                            confirmed = brain.confirm(request)
                            print(f"[STATUS] {confirmed.status}")
                            print(f"\nISABELLA:\n{confirmed.message}")
                        else:
                            print("[STATUS] cancelled")
                            print("\nISABELLA:\nAção cancelada.")
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
