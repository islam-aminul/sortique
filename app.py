"""Sortique application entry point."""

from sortique.factory import AppFactory


def main() -> None:
    factory = AppFactory()
    print("Sortique initialized successfully")
    print(f"Config: {factory.config.get_all()}")
    factory.close()
