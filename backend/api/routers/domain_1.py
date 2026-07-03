from fastapi import APIRouter

from domains.domain_1.models import Domain1Response
from domains.domain_1.service import get_domain_1

router = APIRouter(prefix="/domain-1", tags=["domain_1"])


@router.get("/", response_model=Domain1Response)
def read_domain_1() -> Domain1Response:
    return get_domain_1()
