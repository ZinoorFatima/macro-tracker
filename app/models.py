from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .validation import (
    MAX_FEEDBACK_LEN,
    BurnedCalories,
    Calories,
    DateStr,
    Description,
    MacroGrams,
    OptionalDescription,
    Score,
    Steps,
    ValidationProblem,
    check_goal_consistency,
)

Gender = Literal["male", "female"]
ActivityLevel = Literal["sedentary", "light", "moderate", "active", "very_active"]
Goal = Literal["lose_fat", "gain_muscle", "maintain", "recomp"]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
ActivityType = Literal["workout", "steps", "rest"]


class ProfileIn(BaseModel):
    age: int = Field(ge=10, le=100)
    gender: Gender
    height_cm: float = Field(gt=50, lt=280)
    weight_kg: float = Field(gt=20, lt=400)
    activity_level: ActivityLevel
    goal: Goal
    timeline_weeks: int | None = Field(default=None, ge=1, le=520)
    target_weight_kg: float | None = Field(default=None, gt=20, lt=400)

    @model_validator(mode="after")
    def _goal_matches_target(self):
        check_goal_consistency(self.goal, self.weight_kg, self.target_weight_kg)
        return self


class ProfileUpdate(BaseModel):
    """Partial profile update.

    Fields are only applied when explicitly present in the request body, so
    sending ``{"timeline_weeks": null}`` clears the timeline instead of being
    silently ignored. Callers must use ``model_dump(exclude_unset=True)``.
    """

    age: int | None = Field(default=None, ge=10, le=100)
    gender: Gender | None = None
    height_cm: float | None = Field(default=None, gt=50, lt=280)
    weight_kg: float | None = Field(default=None, gt=20, lt=400)
    activity_level: ActivityLevel | None = None
    goal: Goal | None = None
    timeline_weeks: int | None = Field(default=None, ge=1, le=520)
    target_weight_kg: float | None = Field(default=None, gt=20, lt=400)

    @model_validator(mode="after")
    def _reject_empty_update(self):
        if not self.model_fields_set:
            raise ValidationProblem("No fields to update")
        return self


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class MealAnalyzeIn(BaseModel):
    meal_type: MealType
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _first_message_is_user(self):
        if self.messages[0].role != "user":
            raise ValidationProblem("The conversation must start with a user message")
        return self


class MealIn(BaseModel):
    date: DateStr
    meal_type: MealType
    description: Description
    calories: Calories
    protein_g: MacroGrams
    carbs_g: MacroGrams
    fat_g: MacroGrams
    items: list[dict] | None = Field(default=None, max_length=50)
    score: Score | None = None
    feedback: str | None = Field(default=None, max_length=MAX_FEEDBACK_LEN)


class MealUpdate(BaseModel):
    meal_type: MealType | None = None
    description: Description | None = None
    calories: Calories | None = None
    protein_g: MacroGrams | None = None
    carbs_g: MacroGrams | None = None
    fat_g: MacroGrams | None = None

    @model_validator(mode="after")
    def _reject_empty_update(self):
        if not self.model_fields_set:
            raise ValidationProblem("No fields to update")
        return self


class ActivityAnalyzeIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _first_message_is_user(self):
        if self.messages[0].role != "user":
            raise ValidationProblem("The conversation must start with a user message")
        return self


class ActivityIn(BaseModel):
    date: DateStr
    activity_type: ActivityType
    description: OptionalDescription = ""
    exercises: list[dict] | None = Field(default=None, max_length=50)
    steps: Steps | None = None
    calories_burned: BurnedCalories = 0

    @model_validator(mode="after")
    def _shape_matches_type(self):
        if self.activity_type == "steps" and self.steps is None:
            raise ValidationProblem("A steps entry needs a step count")
        if self.activity_type == "rest" and (self.calories_burned or self.steps):
            raise ValidationProblem("A rest day cannot have steps or calories burned")
        if self.activity_type == "workout" and not self.description:
            raise ValidationProblem("A workout needs a description")
        return self


class ActivityUpdate(BaseModel):
    description: OptionalDescription | None = None
    steps: Steps | None = None
    calories_burned: BurnedCalories | None = None

    @model_validator(mode="after")
    def _reject_empty_update(self):
        if not self.model_fields_set:
            raise ValidationProblem("No fields to update")
        return self


class WeightIn(BaseModel):
    date: DateStr
    weight_kg: float = Field(gt=20, lt=400)
