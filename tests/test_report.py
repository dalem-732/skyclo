import csv
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import report

DATA = Path(__file__).resolve().parents[1] / "data"
AS_OF = date(2026, 11, 10)
ZERO = Decimal("0.00")


def rub(value: str) -> Decimal:
    return Decimal(value)


def load(as_of: date) -> report.Report:
    return report.load_report(DATA, as_of)


class AsOfNovember10(unittest.TestCase):
    def setUp(self) -> None:
        self.report = load(AS_OF)
        self.works = {line.work_id: line for line in self.report.works}
        self.contracts = {line.contract_id: line for line in self.report.contracts}

    def test_w23_is_planned_and_p8_is_excluded(self) -> None:
        self.assertEqual(self.works["W-23"].status, "planned")
        self.assertEqual(self.contracts["D-2"].paid, rub("75000.00"))

    def test_refund_is_not_paid(self) -> None:
        self.assertEqual(self.contracts["D-1"].paid, rub("315000.00"))

    def test_queued_is_separate(self) -> None:
        contract = self.contracts["D-1"]
        self.assertEqual(contract.queued, rub("150000.00"))
        self.assertEqual(contract.paid, rub("315000.00"))

    def test_allocation(self) -> None:
        expected = {
            "W-11": ("done", "100000.00", "100000.00", "0.00", "0.00", "100000.00"),
            "W-12": ("done", "100000.00", "100000.00", "0.00", "0.00", "100000.00"),
            "W-13": ("cancelled", "0.00", "0.00", "0.00", "0.00", "0.00"),
            "W-14": ("done", "100000.00", "100000.00", "0.00", "15000.00", "85000.00"),
            "W-15": ("planned", "100000.00", "15000.00", "85000.00", "0.00", "15000.00"),
            "W-16": ("planned", "100000.00", "0.00", "100000.00", "0.00", "0.00"),
            "W-21": ("done", "20000.00", "20000.00", "0.00", "0.00", "20000.00"),
            "W-22": ("done", "30000.00", "30000.00", "0.00", "0.00", "30000.00"),
            "W-23": ("planned", "40000.00", "25000.00", "15000.00", "0.00", "25000.00"),
        }
        for work_id, (status, payable, paid, remaining, direct, advance) in expected.items():
            line = self.works[work_id]
            self.assertEqual(line.status, status, work_id)
            self.assertEqual(line.payable, rub(payable), work_id)
            self.assertEqual(line.paid, rub(paid), work_id)
            self.assertEqual(line.remaining, rub(remaining), work_id)
            self.assertEqual(line.paid_direct, rub(direct), work_id)
            self.assertEqual(line.paid_advance, rub(advance), work_id)

    def test_contract_totals(self) -> None:
        studio = self.contracts["D-1"]
        self.assertEqual(studio.signed_amount, rub("600000.00"))
        self.assertEqual(studio.payable, rub("500000.00"))
        self.assertEqual(studio.remaining, rub("185000.00"))
        self.assertEqual(studio.unallocated, ZERO)

        designer = self.contracts["D-2"]
        self.assertEqual(designer.signed_amount, rub("90000.00"))
        self.assertEqual(designer.payable, rub("90000.00"))
        self.assertEqual(designer.paid, rub("75000.00"))
        self.assertEqual(designer.remaining, rub("15000.00"))
        self.assertEqual(designer.queued, ZERO)
        self.assertEqual(designer.unallocated, ZERO)

    def test_invariants_and_decimal(self) -> None:
        self.assertEqual(self.report.warnings, [])
        for line in self.report.works:
            self.assertIsInstance(line.paid, Decimal)
            self.assertNotIsInstance(line.paid, float)
            self.assertEqual(line.paid + line.remaining, line.payable)
        for contract in self.report.contracts:
            works = [line for line in self.report.works if line.contract_id == contract.contract_id]
            paid_on_works = sum((line.paid for line in works), ZERO)
            self.assertEqual(paid_on_works + contract.unallocated, contract.paid)
            self.assertEqual(contract.payable - contract.paid, contract.remaining)
            open_remaining = sum(
                (line.remaining for line in works if line.status != "cancelled"),
                ZERO,
            )
            self.assertEqual(open_remaining, contract.remaining)


class OtherDates(unittest.TestCase):
    def test_refund_still_counts_before_return(self) -> None:
        built = load(date(2026, 9, 5))
        contracts = {line.contract_id: line for line in built.contracts}
        works = {line.work_id: line for line in built.works}
        self.assertEqual(contracts["D-1"].paid, rub("350000.00"))
        self.assertEqual(works["W-13"].status, "planned")
        self.assertEqual(works["W-13"].paid, rub("100000.00"))
        self.assertEqual(works["W-11"].status, "planned")

    def test_later_payment_included_after_its_date(self) -> None:
        built = load(date(2026, 11, 12))
        contracts = {line.contract_id: line for line in built.contracts}
        works = {line.work_id: line for line in built.works}
        self.assertEqual(works["W-23"].status, "done")
        self.assertEqual(contracts["D-2"].paid, rub("95000.00"))
        self.assertEqual(contracts["D-2"].unallocated, rub("5000.00"))
        self.assertEqual(contracts["D-2"].remaining, rub("-5000.00"))
        self.assertEqual(works["W-23"].paid, rub("40000.00"))


class AllocationRules(unittest.TestCase):
    def test_done_works_are_closed_before_planned(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("300.00"), date(2026, 1, 1))
        early_id = report.Work("W-2", "D", "позже по номеру", rub("100.00"), "done", date(2026, 2, 1), None)
        later_done = report.Work("W-1", "D", "раньше принята", rub("100.00"), "done", date(2026, 1, 15), None)
        planned = report.Work("W-0", "D", "план", rub("100.00"), "planned", None, None)
        payment = report.Payment(
            "P", "D", None, "аванс", rub("150.00"), "paid", date(2026, 1, 2), date(2026, 1, 2), None
        )
        built = report.build_report([contract], [early_id, later_done, planned], [payment], date(2026, 3, 1))
        works = {line.work_id: line for line in built.works}
        self.assertEqual(works["W-1"].paid, rub("100.00"))
        self.assertEqual(works["W-2"].paid, rub("50.00"))
        self.assertEqual(works["W-0"].paid, ZERO)

    def test_cancelled_work_does_not_take_the_pool(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("100.00"), date(2026, 1, 1))
        cancelled = report.Work("W-1", "D", "отмена", rub("100.00"), "cancelled", None, date(2026, 2, 1))
        planned = report.Work("W-2", "D", "план", rub("40.00"), "planned", None, None)
        payment = report.Payment(
            "P", "D", None, "аванс", rub("50.00"), "paid", date(2026, 1, 2), date(2026, 1, 2), None
        )
        built = report.build_report([contract], [cancelled, planned], [payment], date(2026, 3, 1))
        works = {line.work_id: line for line in built.works}
        self.assertEqual(works["W-1"].payable, ZERO)
        self.assertEqual(works["W-1"].paid, ZERO)
        self.assertEqual(works["W-2"].paid, rub("40.00"))
        self.assertEqual(built.contracts[0].unallocated, rub("10.00"))

    def test_direct_payment_above_price_stays_on_the_work(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("100.00"), date(2026, 1, 1))
        work = report.Work("W-1", "D", "работа", rub("100.00"), "done", date(2026, 2, 1), None)
        other = report.Work("W-2", "D", "другая", rub("100.00"), "done", date(2026, 2, 2), None)
        payment = report.Payment(
            "P", "D", "W-1", "сверх", rub("130.00"), "paid", date(2026, 2, 3), date(2026, 2, 3), None
        )
        built = report.build_report([contract], [work, other], [payment], date(2026, 3, 1))
        works = {line.work_id: line for line in built.works}
        self.assertEqual(works["W-1"].paid, rub("130.00"))
        self.assertEqual(works["W-1"].remaining, rub("-30.00"))
        self.assertEqual(works["W-2"].paid, ZERO)
        self.assertTrue(any("больше цены" in warning for warning in built.warnings))

    def test_foreign_work_is_not_allocated_to_others(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("100.00"), date(2026, 1, 1))
        work = report.Work("W-1", "D", "работа", rub("100.00"), "planned", None, None)
        payment = report.Payment(
            "P", "D", "W-9", "чужая", rub("30.00"), "paid", date(2026, 2, 1), date(2026, 2, 1), None
        )
        built = report.build_report([contract], [work], [payment], date(2026, 3, 1))
        self.assertEqual(built.works[0].paid, ZERO)
        self.assertEqual(built.contracts[0].paid, rub("30.00"))
        self.assertEqual(built.contracts[0].unallocated, rub("30.00"))
        self.assertTrue(built.warnings)

    def test_refund_without_date_is_not_paid(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("100.00"), date(2026, 1, 1))
        work = report.Work("W-1", "D", "работа", rub("100.00"), "planned", None, None)
        payment = report.Payment(
            "P", "D", None, "ошибка", rub("40.00"), "refunded", date(2026, 2, 1), date(2026, 2, 1), None
        )
        built = report.build_report([contract], [work], [payment], date(2026, 3, 1))
        self.assertEqual(built.contracts[0].paid, ZERO)
        self.assertTrue(any("нет даты возврата" in warning for warning in built.warnings))

    def test_price_mismatch_warns_and_uses_work_prices(self) -> None:
        contract = report.Contract("D", "Подрядчик", rub("10.00"), date(2026, 1, 1))
        work = report.Work("W-1", "D", "работа", rub("40.00"), "planned", None, None)
        built = report.build_report([contract], [work], [], date(2026, 3, 1))
        self.assertEqual(built.contracts[0].signed_amount, rub("10.00"))
        self.assertEqual(built.contracts[0].payable, rub("40.00"))
        self.assertTrue(any("не равна сумме договора" in warning for warning in built.warnings))


class CsvOutput(unittest.TestCase):
    def test_files_roundtrip(self) -> None:
        built = load(AS_OF)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            works_path = out / "works.csv"
            contracts_path = out / "contracts.csv"
            report.write_works(works_path, built.works)
            report.write_contracts(contracts_path, built.contracts)
            self.assertTrue(works_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with works_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            by_id = {row["Работа"]: row for row in rows}
            self.assertEqual(by_id["W-14"]["Из них по работе"], "15000.00")
            self.assertEqual(by_id["W-14"]["Из них из аванса"], "85000.00")
            self.assertEqual(by_id["W-15"]["Осталось"], "85000.00")
            self.assertEqual(by_id["W-23"]["Статус на дату"], "запланирована")
            with contracts_path.open(encoding="utf-8-sig", newline="") as handle:
                contracts = {row["Договор"]: row for row in csv.DictReader(handle)}
            self.assertEqual(contracts["D-1"]["В очереди"], "150000.00")
            self.assertEqual(contracts["D-1"]["Осталось"], "185000.00")
            self.assertEqual(contracts["D-2"]["Выплачено"], "75000.00")


if __name__ == "__main__":
    unittest.main()
