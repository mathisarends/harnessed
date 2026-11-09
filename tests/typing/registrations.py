from typing import assert_type

from harnessed.kernel import Registration, registration


def declarations(recipe: Registration) -> None:
    @registration(recipe)
    def callback(value: int, *, prefix: str = "") -> str:
        return prefix + str(value)

    assert_type(callback(1, prefix="value="), str)
    callback("wrong")  # ty: ignore[invalid-argument-type]
