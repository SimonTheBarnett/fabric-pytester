import re

from fabric_pytester.core.renderer import MappingPlaceholderProvider, ScenarioContext, render


def test_dynamic_random_placeholders_are_rendered_and_stable():
    context = ScenarioContext("orders")
    rendered = render(
        {
            "order": "ORD-{random_numbers_9}",
            "same_order": "ORD-{random_numbers_9}",
            "customer": "CUS-{random_numbers_6}",
            "sku": "SKU-{random_alpha_numeric_4}",
            "region": "{random_alpha_3}",
        },
        context,
    )

    assert re.fullmatch(r"ORD-\d{9}", rendered["order"])
    assert rendered["same_order"] == rendered["order"]
    assert re.fullmatch(r"CUS-\d{6}", rendered["customer"])
    assert re.fullmatch(r"SKU-[A-Z0-9]{4}", rendered["sku"])
    assert re.fullmatch(r"[A-Z]{3}", rendered["region"])


def test_named_generated_placeholders_are_rendered_and_stable():
    context = ScenarioContext("orders")
    rendered = render(
        {
            "order": "ORD-{generated_order_id_numbers_9}",
            "same_order": "ORD-{generated_order_id_numbers_9}",
            "customer": "CUS-{generated_customer_code_alpha_numeric_6}",
            "region": "{generated_region_alpha_3}",
        },
        context,
    )

    assert re.fullmatch(r"ORD-\d{9}", rendered["order"])
    assert rendered["same_order"] == rendered["order"]
    assert re.fullmatch(r"CUS-[A-Z0-9]{6}", rendered["customer"])
    assert re.fullmatch(r"[A-Z]{3}", rendered["region"])


def test_builtin_placeholders_are_rendered_and_stable_where_expected():
    context = ScenarioContext("orders")
    rendered = render(
        {
            "scenario": "{scenario_key}",
            "run": "{run_id}",
            "same_run": "{run_id}",
            "uuid": "{uuid}",
            "same_uuid": "{uuid}",
            "date": "{current_date}",
            "timestamp": "{current_timestamp}",
            "unknown": "{missing_value}",
        },
        context,
    )

    assert rendered["scenario"] == "orders"
    assert re.fullmatch(r"[0-9a-f]{32}", rendered["run"])
    assert rendered["same_run"] == rendered["run"]
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        rendered["uuid"],
    )
    assert rendered["same_uuid"] == rendered["uuid"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rendered["date"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*\+00:00", rendered["timestamp"])
    assert rendered["unknown"] == "{missing_value}"


def test_placeholder_precedence_allows_captures_and_providers_to_override():
    context = ScenarioContext(
        "orders",
        variables={"name": "variable"},
        providers=[lambda _: {"name": "provider", "project": "fabric"}],
    )
    context.capture("name", "capture")

    rendered = render({"name": "{name}", "project": "{project}"}, context)

    assert rendered == {"name": "provider", "project": "fabric"}


def test_mapping_placeholder_provider_adds_values():
    context = ScenarioContext(
        "orders",
        providers=[MappingPlaceholderProvider({"external_id": "EXT-1"})],
    )

    assert render("id={external_id}", context) == "id=EXT-1"


def test_random_placeholder_aliases_are_not_supported():
    context = ScenarioContext("orders")

    rendered = render(
        {
            "digits": "{random_digits_9}",
            "alphanumeric": "{random_alphanumeric_4}",
            "legacy": "{random_9_digits}",
            "zero_length": "{random_numbers_0}",
        },
        context,
    )

    assert rendered == {
        "digits": "{random_digits_9}",
        "alphanumeric": "{random_alphanumeric_4}",
        "legacy": "{random_9_digits}",
        "zero_length": "{random_numbers_0}",
    }
