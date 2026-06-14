"""
Payment Model
=============
All queries reference `students` (unified table) via `student_id`.
"""

from datetime import datetime
from database.db_manager import DatabaseManager
from utils.payment_constants import (
    SCHOOL_MONTHS, MONTH_CALENDAR_MAP, STATUS_PAYE,
)
from models.payment_student import PaymentStudent


class Payment:

    # ------------------------------------------------------------------
    # Receipt numbering
    # ------------------------------------------------------------------
    @staticmethod
    def generate_receipt_number() -> str:
        db = DatabaseManager()
        last_seq = int(db.get_setting("last_receipt_seq", "0") or "0")
        next_seq = last_seq + 1
        db.set_setting("last_receipt_seq", str(next_seq))
        year = datetime.now().year
        return f"REC-{year}-{next_seq:06d}"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def create(data: dict) -> int:
        db = DatabaseManager()
        data = dict(data)
        data.setdefault("date_creation", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if not data.get("receipt_number"):
            data["receipt_number"] = Payment.generate_receipt_number()

        columns = [
            "student_id", "annee_scolaire", "payment_type", "month",
            "amount", "payment_date", "notes", "receipt_number", "date_creation",
        ]
        columns = [c for c in columns if c in data]
        placeholders = ", ".join(["?"] * len(columns))
        values = [data[c] for c in columns]

        cursor = db.execute(
            f"INSERT INTO payments ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return cursor.lastrowid

    @staticmethod
    def get_by_id(payment_id: int):
        db = DatabaseManager()
        return db.fetchone("SELECT * FROM payments WHERE id = ?", (payment_id,))

    @staticmethod
    def get_history(student_id: int, annee_scolaire: str = None):
        db = DatabaseManager()
        if annee_scolaire:
            return db.fetchall(
                "SELECT * FROM payments WHERE student_id = ? AND annee_scolaire = ? "
                "ORDER BY payment_date DESC, id DESC",
                (student_id, annee_scolaire),
            )
        return db.fetchall(
            "SELECT * FROM payments WHERE student_id = ? ORDER BY payment_date DESC, id DESC",
            (student_id,),
        )

    # ------------------------------------------------------------------
    # Full save workflow
    # ------------------------------------------------------------------
    @staticmethod
    def register_payment(student_id: int, annee_scolaire: str, payment_type: str,
                          month: str, amount: float, payment_date: str,
                          notes: str = "") -> dict:
        payment_id = Payment.create({
            "student_id": student_id,
            "annee_scolaire": annee_scolaire,
            "payment_type": payment_type,
            "month": month or None,
            "amount": amount,
            "payment_date": payment_date,
            "notes": notes,
        })

        if month and payment_type == "Mensualité":
            PaymentStudent.set_month_status(student_id, annee_scolaire, month, STATUS_PAYE)

        return Payment.get_by_id(payment_id)

    # ------------------------------------------------------------------
    # Dashboard aggregations
    # ------------------------------------------------------------------
    @staticmethod
    def total_inscription_revenue(annee_scolaire: str, classe: str = None) -> float:
        db = DatabaseManager()
        query = (
            "SELECT COALESCE(SUM(p.amount), 0) as total FROM payments p "
            "JOIN students s ON p.student_id = s.id "
            "WHERE p.annee_scolaire = ? AND p.payment_type = 'Inscription'"
        )
        params = [annee_scolaire]
        if classe and classe not in ("Toutes", ""):
            query += " AND s.classe = ?"
            params.append(classe)
        row = db.fetchone(query, params)
        return row["total"] if row else 0.0

    @staticmethod
    def monthly_income(annee_scolaire: str, calendar_year: int,
                        calendar_month: int, classe: str = None) -> float:
        db = DatabaseManager()
        pattern = f"{calendar_year:04d}-{calendar_month:02d}%"
        query = (
            "SELECT COALESCE(SUM(p.amount), 0) as total FROM payments p "
            "JOIN students s ON p.student_id = s.id "
            "WHERE p.annee_scolaire = ? AND p.payment_date LIKE ?"
        )
        params = [annee_scolaire, pattern]
        if classe and classe not in ("Toutes", ""):
            query += " AND s.classe = ?"
            params.append(classe)
        row = db.fetchone(query, params)
        return row["total"] if row else 0.0

    @staticmethod
    def monthly_income_evolution(annee_scolaire: str, classe: str = None):
        db = DatabaseManager()
        start_year = int(annee_scolaire.split("/")[0])
        end_year   = int(annee_scolaire.split("/")[1])

        results = []
        for month_name in SCHOOL_MONTHS:
            cal_month, offset = MONTH_CALENDAR_MAP[month_name]
            cal_year = start_year if offset == 0 else end_year
            pattern  = f"{cal_year:04d}-{cal_month:02d}%"

            row_data = {"month": month_name}
            for ptype, key in (
                ("Inscription", "inscription"),
                ("Mensualité",  "mensualite"),
                ("Transport",   "transport"),
            ):
                q = (
                    "SELECT COALESCE(SUM(p.amount), 0) as total FROM payments p "
                    "JOIN students s ON p.student_id = s.id "
                    "WHERE p.annee_scolaire = ? AND p.payment_date LIKE ? "
                    "AND p.payment_type = ?"
                )
                params = [annee_scolaire, pattern, ptype]
                if classe and classe not in ("Toutes", ""):
                    q += " AND s.classe = ?"
                    params.append(classe)
                row = db.fetchone(q, params)
                row_data[key] = row["total"] if row else 0.0

            row_data["total"] = (
                row_data["inscription"] + row_data["mensualite"] + row_data["transport"]
            )
            results.append(row_data)

        return results

    @staticmethod
    def payment_status_distribution(annee_scolaire: str, classe: str = None):
        from utils.payment_constants import STATUS_PAYE, STATUS_UNPAID, STATUS_NAN
        db = DatabaseManager()
        query = (
            "SELECT ms.status, COUNT(*) as cnt FROM month_status ms "
            "JOIN students s ON ms.student_id = s.id "
            "WHERE ms.annee_scolaire = ?"
        )
        params = [annee_scolaire]
        if classe and classe not in ("Toutes", ""):
            query += " AND s.classe = ?"
            params.append(classe)
        query += " GROUP BY ms.status"

        rows = db.fetchall(query, params)
        result = {STATUS_PAYE: 0, STATUS_UNPAID: 0, STATUS_NAN: 0}
        for r in rows:
            if r["status"] in result:
                result[r["status"]] = r["cnt"]
        return result

    @staticmethod
    def income_by_class(annee_scolaire: str):
        db = DatabaseManager()
        rows = db.fetchall(
            "SELECT s.classe, COALESCE(SUM(p.amount), 0) as total "
            "FROM payments p JOIN students s ON p.student_id = s.id "
            "WHERE p.annee_scolaire = ? GROUP BY s.classe ORDER BY s.classe",
            (annee_scolaire,),
        )
        return [(r["classe"] or "N/A", r["total"]) for r in rows]
