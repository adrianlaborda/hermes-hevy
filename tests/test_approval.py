"""Every write tool goes through Hermes' approval gate with a readable preview."""

from hermes_hevy.approval import MAX_MESSAGE, WriteApproval, describe_call
from hermes_hevy.runtime import Runtime

WRITES = frozenset({"hevy_log_workout", "hevy_update_workout", "hevy_save_routine", "hevy_create_routine_folder",
                    "hevy_create_exercise", "hevy_log_body_measurement"})


def _hook(settings=None):
    settings = settings or {}
    return WriteApproval(WRITES, Runtime(get_config=lambda key, default=None: settings.get(key, default),
                                         key_provider=lambda: None, store=object()))


def test_read_tools_and_other_plugins_pass_through():
    hook = _hook()
    assert hook(tool_name="hevy_workouts", args={}) is None
    assert hook(tool_name="terminal", args={"command": "ls"}) is None


def test_every_write_tool_asks_for_approval():
    hook = _hook()
    for name in WRITES:
        result = hook(tool_name=name, args={"title": "X"})
        assert result["action"] == "approve" and result["message"]


def test_confirm_writes_off_skips_the_gate():
    assert _hook({"confirm_writes": False})(tool_name="hevy_log_workout", args={}) is None
    assert _hook({"confirm_writes": "false"})(tool_name="hevy_log_workout", args={}) is None


def test_workout_preview_lists_exercises_and_sets():
    message = describe_call("hevy_log_workout", {
        "title": "Leg day", "start_time": "2026-11-09 18:00", "duration_min": 65,
        "exercises": [{"exercise": "Squat (Barbell)", "sets": [
            {"type": "warmup", "weight_kg": 60, "reps": 5}, {"weight_kg": 100, "reps": 5}]}]})
    assert message.splitlines()[0] == "Log a new workout in Hevy"
    assert "Title: Leg day" in message and "65 min" in message
    assert "- Squat (Barbell): 60 kg x 5 (warmup), 100 kg x 5" in message


def test_update_previews_say_what_is_replaced():
    message = describe_call("hevy_save_routine", {"routine_id": "r1", "exercises": [
        {"exercise": "Bench Press (Barbell)", "sets": [{"weight_lbs": 135, "rep_range": {"start": 8, "end": 12}}]}]})
    assert message.startswith("Update a routine in Hevy")
    assert "Exercises (replace all):" in message and "135 lb x 8-12" in message
    assert describe_call("hevy_save_routine", {"title": "Upper A"}).startswith("Create a routine in Hevy")


def test_measurement_preview_and_clear():
    message = describe_call("hevy_log_body_measurement", {"date": "2026-11-09", "weight_kg": 79.8, "hips": None,
                                                          "clear": ["waist"]})
    assert "Date: 2026-11-09" in message and "- weight_kg: 79.8" in message and "- remove: waist" in message
    assert "hips" not in message  # a null the model sent changes nothing


def test_long_previews_are_capped():
    sets = [{"weight_kg": 100, "reps": 5}] * 6
    message = describe_call("hevy_log_workout", {"exercises": [{"exercise": f"Exercise {i}", "sets": sets}
                                                               for i in range(80)]})
    assert len(message) <= MAX_MESSAGE and message.endswith("…")


def test_a_broken_preview_still_asks():
    result = _hook()(tool_name="hevy_log_workout", args={"exercises": "not a list", "start_time": object()})
    assert result["action"] == "approve"
