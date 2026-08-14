"""ISABELLA temporary text entry point."""

from Isabella.Core.app import IsabellaApp
from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import Intent


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
            elif response.skill_request:
                request = response.skill_request
                print("\nDEBUG:")
                print(f"intent={response.response_type.value}")
                print(f"skill={request.skill}")
                print(f"arguments={request.arguments}")
            elif response.plan:
                print("\nDEBUG:")
                print(f"intent={response.response_type.value}")
                print(f"plan={response.plan.to_dict()}")
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
