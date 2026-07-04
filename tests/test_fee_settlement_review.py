from decimal import Decimal

from openpyxl import Workbook

from sop_hub.fees.settlement_review import (
    REVIEW_HEADERS,
    REVIEW_SHEET_NAME,
    ShipmentReviewRow,
    add_review_sheet,
    build_settlement_units,
)


def test_build_settlement_units_respects_status_merge_and_adjustment() -> None:
    rows = [
        ShipmentReviewRow(
            row_id="r1",
            project_id="P008",
            ship_name="和谐1",
            lot="lot02",
            transport_mode="集装箱",
            shipment_date="2026-06-01",
            quantity=100,
            quantity_unit="箱",
            merge_key="m1",
        ),
        ShipmentReviewRow(
            row_id="r2",
            project_id="P008",
            ship_name="和谐1",
            lot="lot02",
            transport_mode="集装箱",
            shipment_date="2026-06-02",
            quantity=28,
            quantity_adjustment=2,
            quantity_unit="箱",
            merge_key="m1",
        ),
        ShipmentReviewRow(
            row_id="r3",
            project_id="P008",
            ship_name="和谐1",
            lot="lot02",
            transport_mode="集装箱",
            shipment_date="2026-06-03",
            quantity=10,
            quantity_unit="箱",
            settlement_status="挂起",
        ),
        ShipmentReviewRow(
            row_id="r4",
            project_id="P008",
            ship_name="和谐1",
            lot="lot02",
            transport_mode="集装箱",
            shipment_date="2026-06-04",
            quantity=10,
            quantity_unit="箱",
            settlement_status="排除",
        ),
    ]

    units = build_settlement_units(rows)

    assert len(units) == 1
    assert units[0].settlement_key == "m1"
    assert units[0].quantity == Decimal("130")


def test_add_review_sheet_is_first_sheet_with_status_validation() -> None:
    workbook = Workbook()
    rows = [
        ShipmentReviewRow(
            row_id="r1",
            project_id="P008",
            ship_name="和谐1",
            lot="lot02",
            transport_mode="集装箱",
            shipment_date="2026-06-01",
            quantity=100,
            quantity_unit="箱",
            settlement_status="结算",
            merge_key="m1",
            quantity_adjustment=2,
            info_summary="和谐1(lot2)/七道/100箱",
        )
    ]

    worksheet = add_review_sheet(workbook, rows)

    assert workbook.sheetnames[0] == REVIEW_SHEET_NAME
    assert [cell.value for cell in worksheet[1]] == REVIEW_HEADERS
    assert worksheet["O2"].value == "结算"
    assert worksheet["P2"].value == "m1"
    assert worksheet["Q2"].value == 2
    assert worksheet["R2"].value == 102
    validations = list(worksheet.data_validations.dataValidation)
    assert len(validations) == 1
    assert validations[0].formula1 == '"结算,挂起,排除"'
    assert "O2" in str(validations[0].sqref)
