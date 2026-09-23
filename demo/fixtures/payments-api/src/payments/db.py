import sqlalchemy

DATABASE_URL = "postgres://payments:{{gen:password}}@prod-db.internal:5432/payments"
engine = sqlalchemy.create_engine(DATABASE_URL)
