from pydantic import BaseModel


class PostModel(BaseModel):
    name: str
    age: int
    is_active: bool
    order_name: str
    amount: int
