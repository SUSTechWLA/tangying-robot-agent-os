import json

import pytest

from scripts.evaluate_gazebo_business import parse_physics_output


def test_native_oracle_handles_batched_messages_without_choosing_by_outcome():
    def message(sequence, code):
        return 'data: '+json.dumps(json.dumps({'schemaVersion': 'gazebo.suction.v1',
                                              'sequence': sequence, 'code': code}))
    payload = message(10, 'OK')+'\n\n'+message(11, 'GRASP_MISS')+'\n'
    assert parse_physics_output(payload)['code'] == 'GRASP_MISS'
    with pytest.raises(ValueError):
        parse_physics_output('')
    with pytest.raises(ValueError):
        parse_physics_output('data: "invalid-json"')
