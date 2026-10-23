import os

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


DATABASE_URL = os.environ["DATABASE_URL"]

app = FastAPI(title="Data Hygiene API")


class Customer(BaseModel):
    customer_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    postcode: str | None = None
    created_at: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/customers/{customer_id}", response_model=Customer)
def get_customer(customer_id: str) -> Customer:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            """
            SELECT customer_id, name, email, phone, postcode, created_at
            FROM customers
            WHERE customer_id = %s
            """,
            (customer_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    return Customer(
        customer_id=row[0],
        name=row[1],
        email=row[2],
        phone=row[3],
        postcode=row[4],
        created_at=row[5].isoformat() if row[5] is not None else None,
    )
