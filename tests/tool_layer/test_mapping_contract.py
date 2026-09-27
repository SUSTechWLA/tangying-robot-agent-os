"""Exercise model-advertised mapping arguments through the real tool handler."""

import pytest
from tangying_robot_gateway.tool_schema import validate_value
from tangying_robot_gateway.tools.mapping import build_mapping_tools


@pytest.mark.parametrize("decision", ["reuse", "explore", "ambiguous"])
def test_schema_map_id_reaches_provider_without_changing_its_decision(decision):
    seen = []

    def provider(arguments):
        seen.append(arguments)
        return {"decision": decision, "mapId": "home-selected"}

    tool = next(tool for tool in build_mapping_tools(ensure_mapping=provider)
                if tool.name == "build_map")
    arguments = {"environment": "客厅", "mapId": "home-selected",
                 "max_travel_m": 12.5, "max_legs": 3}
    validate_value(arguments, tool.parameters_schema)
    result = tool.execute(**arguments)

    assert result.success, result
    assert seen == [{"environment": "客厅", "mapId": "home-selected",
                     "maxTravelM": 12.5, "maxLegs": 3}]
    assert result.data["decision"] == decision
    if decision == "ambiguous":
        assert result.data["requiresChoice"] is True
    else:
        assert result.data["surveyStarted"] is (decision == "explore")
        assert result.data["reuseExistingMap"] is (decision == "reuse")
