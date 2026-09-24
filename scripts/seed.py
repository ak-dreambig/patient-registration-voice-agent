"""Insert two clearly fictional demo patients if the patients table is empty.

Run with: python -m scripts.seed
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models import Patient
from app.schemas import PatientCreate
from app.services import patient_service

logger = logging.getLogger(__name__)

DEMO_PATIENTS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "04/12/1988", "sex": "Female",
        "phone_number": "2125550143", "email": "jane.doe@example.com", "address_line_1": "123 Demo Street",
        "address_line_2": "Apt 4B", "city": "New York", "state": "NY", "zip_code": "10001",
        "insurance_provider": "Example Health", "insurance_member_id": "EXH123456",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "2125550199",
    },
    {
        "first_name": "John", "last_name": "Smith", "date_of_birth": "09/30/1975", "sex": "Male",
        "phone_number": "3125550188", "address_line_1": "456 Sample Avenue", "city": "Chicago",
        "state": "IL", "zip_code": "60601", "preferred_language": "Spanish",
    },
]


def seed_if_empty(db: Session) -> int:
    """Insert demo patients only when the table has no rows. Returns the number inserted."""
    if db.scalar(select(func.count()).select_from(Patient)):
        return 0
    for data in DEMO_PATIENTS:
        patient_service.create_patient(db, PatientCreate.model_validate(data))
    logger.info("demo_data_seeded", extra={"count": len(DEMO_PATIENTS)})
    return len(DEMO_PATIENTS)


def main() -> None:
    """CLI entry point."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        print(f"Inserted {seed_if_empty(db)} demo patient(s).")


if __name__ == "__main__":
    main()
