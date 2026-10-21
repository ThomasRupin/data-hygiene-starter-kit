import os

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="Data Hygiene API")


class User(BaseModel):
    user_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    postcode: str | None = None
    created_at: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users/{user_id}", response_model=User)
def get_user(user_id: str) -> User:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            """
            SELECT customer_id, name, email, phone, postcode, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="User not found")

    return User(
        user_id=row[0],
        name=row[1],
        email=row[2],
        phone=row[3],
        postcode=row[4],
        created_at=row[5].isoformat() if row[5] is not None else None,
    )
