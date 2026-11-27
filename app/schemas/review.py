# 作者：AK2573422641
from pydantic import BaseModel


class ApproveRequest(BaseModel):
    approved_answer: str
