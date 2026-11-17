#!/usr/bin/env python3
"""Отчёт по авансам: цена, выплачено и остаток на выбранную дату."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

STATUS_RU = {
    "planned": "запланирована",
    "done": "принята",
    "cancelled": "отменена",
}

WORK_FIELDS = [
    "Договор",
    "Подрядчик",
    "Работа",
    "Название",
    "Статус на дату",
    "Цена",
    "К оплате",
    "Выплачено",
    "Осталось",
    "Из них по работе",
    "Из них из аванса",
]

CONTRACT_FIELDS = [
    "Договор",
    "Подрядчик",
    "Сумма по подписи",
    "К оплате",
    "Выплачено",
    "Осталось",
    "В очереди",
    "Аванс не разнесён",
]


def money(value: str) -> Decimal:
    return Decimal(value.strip()).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):f}"


def parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


@dataclass(frozen=True)
class Contract:
    contract_id: str
    contractor: str
    amount: Decimal
    signed_at: date


@dataclass(frozen=True)
class Work:
    work_id: str
    contract_id: str
    title: str
    price: Decimal
    status: str
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True)
class Payment:
    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount: Decimal
    status: str
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass
class WorkLine:
    contract_id: str
    contractor: str
    work_id: str
    title: str
    status: str
    price: Decimal
    payable: Decimal
    paid_direct: Decimal
    paid_advance: Decimal
    completed_at: date | None

    @property
    def paid(self) -> Decimal:
        return self.paid_direct + self.paid_advance

    @property
    def remaining(self) -> Decimal:
        return self.payable - self.paid


@dataclass(frozen=True)
class ContractLine:
    contract_id: str
    contractor: str
    signed_amount: Decimal
    payable: Decimal
    paid: Decimal
    remaining: Decimal
    queued: Decimal
    unallocated: Decimal


@dataclass(frozen=True)
class Report:
    works: list[WorkLine]
    contracts: list[ContractLine]
    warnings: list[str]


def work_status_as_of(work: Work, as_of: date) -> str:
    if work.cancelled_at is not None and work.cancelled_at <= as_of:
        return "cancelled"
    if work.completed_at is not None and work.completed_at <= as_of:
        return "done"
    return "planned"


def payment_class(payment: Payment, as_of: date) -> str:
    """absent — ещё не заведён; paid — деньги у подрядчика; queued — в очереди; refunded — возвращён."""
    if payment.created_at > as_of:
        return "absent"
    if payment.status == "queued":
        return "queued"
    if payment.status == "paid":
        if payment.paid_at is not None and payment.paid_at <= as_of:
            return "paid"
        return "queued"
    if payment.status == "refunded":
        if payment.paid_at is None or payment.paid_at > as_of:
            return "absent"
        if payment.refunded_at is None or payment.refunded_at <= as_of:
            return "refunded"
        return "paid"
    return "unknown"


def _allocation_key(line: WorkLine) -> tuple:
    if line.status == "done":
        completed = line.completed_at or date.max
        return (0, completed.toordinal(), line.work_id)
    if line.status == "planned":
        return (1, 0, line.work_id)
    return (2, 0, line.work_id)


def build_report(
    contracts: list[Contract],
    works: list[Work],
    payments: list[Payment],
    as_of: date,
) -> Report:
    warnings: list[str] = []
    contracts_by_id = {item.contract_id: item for item in contracts}
    works_by_id = {item.work_id: item for item in works}

    for item in contracts:
        if item.signed_at > as_of:
            warnings.append(
                f"Договор {item.contract_id} подписан {item.signed_at.isoformat()}, "
                f"позже даты отчёта {as_of.isoformat()}."
            )

    works_of: dict[str, list[Work]] = {item.contract_id: [] for item in contracts}
    for work in works:
        if work.contract_id not in contracts_by_id:
            warnings.append(
                f"Работа {work.work_id} ссылается на неизвестный договор {work.contract_id}."
            )
        works_of.setdefault(work.contract_id, []).append(work)
        if work.status == "cancelled" and work.cancelled_at is None:
            warnings.append(f"У отменённой работы {work.work_id} нет даты отмены.")
        if work.status == "done" and work.completed_at is None:
            warnings.append(f"У принятой работы {work.work_id} нет даты приёмки.")

    contract_ids = [item.contract_id for item in contracts]
    for contract_id in works_of:
        if contract_id not in contracts_by_id and contract_id not in contract_ids:
            contract_ids.append(contract_id)

    lines_of: dict[str, list[WorkLine]] = {}
    for contract_id in contract_ids:
        contract = contracts_by_id.get(contract_id)
        contractor = contract.contractor if contract is not None else ""
        lines: list[WorkLine] = []
        catalog = ZERO
        for work in works_of.get(contract_id, []):
            status = work_status_as_of(work, as_of)
            payable = ZERO if status == "cancelled" else work.price
            catalog += work.price
            lines.append(
                WorkLine(
                    contract_id=contract_id,
                    contractor=contractor,
                    work_id=work.work_id,
                    title=work.title,
                    status=status,
                    price=work.price,
                    payable=payable,
                    paid_direct=ZERO,
                    paid_advance=ZERO,
                    completed_at=work.completed_at,
                )
            )
        if contract is not None and catalog != contract.amount:
            warnings.append(
                f"Сумма работ по договору {contract_id} ({fmt(catalog)}) "
                f"не равна сумме договора ({fmt(contract.amount)})."
            )
        lines_of[contract_id] = lines

    line_by_work = {
        line.work_id: line for lines in lines_of.values() for line in lines
    }

    paid_of = {contract_id: ZERO for contract_id in contract_ids}
    queued_of = {contract_id: ZERO for contract_id in contract_ids}
    pool_of = {contract_id: ZERO for contract_id in contract_ids}
    held_of = {contract_id: ZERO for contract_id in contract_ids}

    for payment in payments:
        if payment.contract_id not in paid_of:
            warnings.append(
                f"Платёж {payment.payment_id} ссылается на неизвестный договор "
                f"{payment.contract_id}."
            )
            paid_of[payment.contract_id] = ZERO
            queued_of[payment.contract_id] = ZERO
            pool_of[payment.contract_id] = ZERO
            held_of[payment.contract_id] = ZERO
            if payment.contract_id not in contract_ids:
                contract_ids.append(payment.contract_id)
                lines_of[payment.contract_id] = []

        if payment.status not in {"paid", "queued", "refunded"}:
            warnings.append(
                f"У платежа {payment.payment_id} неизвестный статус {payment.status!r}."
            )
        if (
            payment.status == "refunded"
            and payment.refunded_at is None
            and payment.created_at <= as_of
        ):
            warnings.append(
                f"У возврата {payment.payment_id} нет даты возврата. "
                f"В выплаченное он не входит."
            )
        kind = payment_class(payment, as_of)
        if kind == "queued":
            queued_of[payment.contract_id] += payment.amount
            continue
        if kind != "paid":
            continue

        paid_of[payment.contract_id] += payment.amount
        if not payment.work_id:
            pool_of[payment.contract_id] += payment.amount
            continue

        target = works_by_id.get(payment.work_id)
        if target is None:
            warnings.append(
                f"Платёж {payment.payment_id} привязан к работе {payment.work_id}, "
                f"которой нет. Сумма остаётся неразнесённой на договоре {payment.contract_id}."
            )
            held_of[payment.contract_id] += payment.amount
            continue
        if target.contract_id != payment.contract_id:
            warnings.append(
                f"Платёж {payment.payment_id} по договору {payment.contract_id} "
                f"привязан к работе {payment.work_id} договора {target.contract_id}. "
                f"Сумма остаётся неразнесённой на договоре {payment.contract_id}."
            )
            held_of[payment.contract_id] += payment.amount
            continue

        line = line_by_work[payment.work_id]
        line.paid_direct += payment.amount

    for contract_id, lines in lines_of.items():
        for line in lines:
            if line.paid_direct > line.price:
                warnings.append(
                    f"Платежи по работе {line.work_id} ({fmt(line.paid_direct)}) "
                    f"больше цены работы ({fmt(line.price)})."
                )
        pool = pool_of.get(contract_id, ZERO)
        for line in sorted(lines, key=_allocation_key):
            if line.status == "cancelled" or pool <= ZERO:
                continue
            capacity = line.payable - line.paid
            if capacity <= ZERO:
                continue
            take = capacity if capacity < pool else pool
            line.paid_advance += take
            pool -= take
        pool_of[contract_id] = pool

    work_rows: list[WorkLine] = []
    contract_rows: list[ContractLine] = []
    for contract_id in contract_ids:
        lines = lines_of.get(contract_id, [])
        work_rows.extend(lines)
        contract = contracts_by_id.get(contract_id)
        payable = sum((line.payable for line in lines), ZERO)
        paid = paid_of.get(contract_id, ZERO)
        unallocated = pool_of.get(contract_id, ZERO) + held_of.get(contract_id, ZERO)
        contract_rows.append(
            ContractLine(
                contract_id=contract_id,
                contractor=contract.contractor if contract is not None else "",
                signed_amount=contract.amount if contract is not None else ZERO,
                payable=payable,
                paid=paid,
                remaining=payable - paid,
                queued=queued_of.get(contract_id, ZERO),
                unallocated=unallocated,
            )
        )

    return Report(works=work_rows, contracts=contract_rows, warnings=warnings)


def load_contracts(path: Path) -> list[Contract]:
    rows = _read_rows(path)
    result: list[Contract] = []
    for row in rows:
        signed_at = parse_date(row["signed_at"])
        if signed_at is None:
            raise ValueError(f"{path}: у договора {row['contract_id']} нет даты подписания")
        result.append(
            Contract(
                contract_id=row["contract_id"].strip(),
                contractor=row["contractor"].strip(),
                amount=money(row["amount"]),
                signed_at=signed_at,
            )
        )
    return result


def load_works(path: Path) -> list[Work]:
    rows = _read_rows(path)
    result: list[Work] = []
    for row in rows:
        result.append(
            Work(
                work_id=row["work_id"].strip(),
                contract_id=row["contract_id"].strip(),
                title=row["title"].strip(),
                price=money(row["price"]),
                status=row["status"].strip(),
                completed_at=parse_date(row["completed_at"]),
                cancelled_at=parse_date(row["cancelled_at"]),
            )
        )
    return result


def load_payments(path: Path) -> list[Payment]:
    rows = _read_rows(path)
    result: list[Payment] = []
    for row in rows:
        created_at = parse_date(row["created_at"])
        if created_at is None:
            raise ValueError(f"{path}: у платежа {row['payment_id']} нет даты создания")
        work_id = row["work_id"].strip() or None
        result.append(
            Payment(
                payment_id=row["payment_id"].strip(),
                contract_id=row["contract_id"].strip(),
                work_id=work_id,
                purpose=row["purpose"].strip(),
                amount=money(row["amount"]),
                status=row["status"].strip(),
                created_at=created_at,
                paid_at=parse_date(row["paid_at"]),
                refunded_at=parse_date(row["refunded_at"]),
            )
        )
    return result


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_works(path: Path, lines: list[WorkLine]) -> None:
    _write_csv(
        path,
        WORK_FIELDS,
        [
            [
                line.contract_id,
                line.contractor,
                line.work_id,
                line.title,
                STATUS_RU[line.status],
                fmt(line.price),
                fmt(line.payable),
                fmt(line.paid),
                fmt(line.remaining),
                fmt(line.paid_direct),
                fmt(line.paid_advance),
            ]
            for line in lines
        ],
    )


def write_contracts(path: Path, lines: list[ContractLine]) -> None:
    _write_csv(
        path,
        CONTRACT_FIELDS,
        [
            [
                line.contract_id,
                line.contractor,
                fmt(line.signed_amount),
                fmt(line.payable),
                fmt(line.paid),
                fmt(line.remaining),
                fmt(line.queued),
                fmt(line.unallocated),
            ]
            for line in lines
        ],
    )


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def load_report(data_dir: Path, as_of: date) -> Report:
    return build_report(
        load_contracts(data_dir / "contracts.csv"),
        load_works(data_dir / "works.csv"),
        load_payments(data_dir / "payments.csv"),
        as_of,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Отчёт по авансам договора на дату. Деньги считаются до копейки."
    )
    parser.add_argument("--data", type=Path, default=Path("data"), help="Папка с CSV")
    parser.add_argument("--date", required=True, help="Дата отчёта, ГГГГ-ММ-ДД")
    parser.add_argument("--out", type=Path, required=True, help="Папка для CSV отчёта")
    args = parser.parse_args(argv)

    try:
        as_of = parse_date(args.date)
    except ValueError:
        as_of = None
    if as_of is None:
        parser.error("дата нужна в формате ГГГГ-ММ-ДД")

    report = load_report(args.data, as_of)
    args.out.mkdir(parents=True, exist_ok=True)
    works_path = args.out / f"works-{as_of.isoformat()}.csv"
    contracts_path = args.out / f"contracts-{as_of.isoformat()}.csv"
    write_works(works_path, report.works)
    write_contracts(contracts_path, report.contracts)
    for warning in report.warnings:
        print(warning, file=sys.stderr)
    print(works_path)
    print(contracts_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
