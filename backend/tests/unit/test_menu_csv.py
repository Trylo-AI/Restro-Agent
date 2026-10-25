"""Menu CSV import parsing (docs/backend.md#csv-import): all or nothing, every error with row and column."""

from app.domain.menu import csv_import

HEAD = "name,category,price,prep_minutes,veg,description,spice,contains,free_from,available\n"


def test_template_parses_to_paise_and_allergen_states() -> None:
    p = csv_import.parse(csv_import.TEMPLATE)
    assert p.errors == []
    (row,) = p.rows
    assert (row.name, row.price_paise, row.prep_minutes, row.veg, row.available) == ("Cold Coffee", 19000, 5, True, True)
    assert row.allergens == {"milk": "contains", "gluten": "free_from", "eggs": "free_from"}


def test_decimals_bom_blank_lines_and_defaults() -> None:
    p = csv_import.parse("\N{ZERO WIDTH NO-BREAK SPACE}" + HEAD + 'Masala Chai,Tea,"₹ 1,234.50",8,no,,,,,\n\n')
    assert p.errors == [] and p.rows[0].price_paise == 123450 and p.rows[0].spice == 0 and p.rows[0].available


def test_every_bad_value_is_reported_and_nothing_is_kept() -> None:
    text = HEAD + ("Toast,Bread,0,200,maybe,,5,nuts;milk,milk,perhaps\n"
                   "toast,Bread,12.345,5,yes,,,,,\n"
                   ",Bread,100,5,yes,,,,,\n")
    p = csv_import.parse(text)
    assert p.rows == []
    found = {(e["row"], e["column"]) for e in p.errors}
    assert {(2, "price"), (2, "prep_minutes"), (2, "veg"), (2, "spice"), (2, "contains"), (2, "free_from"),
            (2, "available"), (3, "name"), (3, "price"), (4, "name")} <= found


def test_missing_and_unknown_columns() -> None:
    p = csv_import.parse("name,category,price,veg,colour\nA,B,1,yes,red\n")
    assert {(e["column"], e["message"]) for e in p.errors} == {
        ("prep_minutes", "Required column is missing."), ("colour", "Unknown column.")}


def test_unquoted_comma_is_a_row_error() -> None:
    p = csv_import.parse(HEAD + "Chai,Tea,1,234,8,no,,,,,\n")
    assert p.errors and "quotes" in str(p.errors[0]["message"])


def test_limits() -> None:
    assert csv_import.parse(HEAD).errors[0]["message"] == "The file has no rows."
    big = HEAD + "".join(f"Dish {i},Cat,100,5,yes,,,,,\n" for i in range(csv_import.MAX_ROWS + 1))
    assert any("More than" in str(e["message"]) for e in csv_import.parse(big).errors)
