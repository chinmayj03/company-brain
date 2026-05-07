import pytest
from companybrain.assembly.classifier import classify
from companybrain.assembly.types import TaskType


@pytest.mark.parametrize("task,expected", [
    ("what does UserCard do",                       TaskType.READ),
    ("change UserCard to show a status indicator",  TaskType.WRITE),
    ("UserCard render is failing in production",    TaskType.DEBUG),
    ("review the auth refactor PR",                 TaskType.AUDIT),
    ("explain the whole codebase to me",            TaskType.ONBOARD),
    ("",                                            TaskType.READ),  # default
])
def test_classify(task, expected):
    assert classify(task) == expected
