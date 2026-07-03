from .models import Domain1Response


def get_domain_1() -> Domain1Response:
    return Domain1Response(message="Hello from domain_1")
