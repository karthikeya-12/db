from uuid import uuid7

from db import Base, get_db
from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from models import PostModel
from sqlalchemy.orm import Session

app = FastAPI()
users = Base.classes.users
orders = Base.classes.orders


@app.get("/users")
async def get_users(db: Session = Depends(get_db)):
    try:
        res = db.query(users).all()
        result = [
            {
                column.name: getattr(user, column.name)
                for column in users.__table__.columns
            }
            for user in res
        ]
        res = jsonable_encoder(result)
        return JSONResponse(content=res, status_code=200)
    except Exception as e:
        raise HTTPException(detail=str(e), status_code=404)


@app.post("/create_user")
async def create_user(req: PostModel, db: Session = Depends(get_db)):
    try:
        user = users(
            id=str(uuid7()), name=req.name, age=req.age, is_active=req.is_active
        )
        db.add(user)
        db.commit()
        db.flush()
        order = orders(
            id=str(uuid7()),
            user_id=user.id,
            order_name=req.order_name,
            amount=req.amount,
        )
        db.add(order)
        db.commit()
        db.flush()
        return JSONResponse(
            content={"users_id": user.id, "order_id": order.id}, status_code=201
        )
    except Exception as e:
        raise HTTPException(detail=str(e), status_code=409)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="localhost", port=8012)
