from datetime import date
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bounds on every number a page or script sends: no negatives, nothing absurd, and never inf/NaN
# (JSON like 1e309 parses as infinity, which would be stored and then break every later answer).
Kcal = Annotated[float, Field(ge=0, le=50_000, allow_inf_nan=False)]
Grams = Annotated[float, Field(ge=0, le=5_000, allow_inf_nan=False)]
WeightKg = Annotated[float, Field(gt=0, le=700, allow_inf_nan=False)]
Servings = Annotated[float, Field(gt=0, le=1_000, allow_inf_nan=False)]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
ClockTime = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]      # HH:MM, 24-hour
MAX_TAGS = 12


def clean_tags(v) -> list[str]:
    """Food tags: lower case, single spaces, no commas, each 1–30 characters, at most MAX_TAGS, no repeats."""
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list):
        raise ValueError("tags must be a list of words")
    out = []
    for t in v:
        if not isinstance(t, str):
            raise ValueError("tags must be words")
        t = " ".join(t.replace(",", " ").split()).lower()
        if not t:
            continue
        if len(t) > 30:
            raise ValueError("a tag can be at most 30 characters")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    return out


class GoalUpdate(BaseModel):
    # Left out = unchanged. The four goals can't be null (an explicit null isn't a goal: 422);
    # the two weights can (null clears them).
    calorie_goal: Annotated[float, Field(ge=0, le=20_000, allow_inf_nan=False)] = None
    protein_goal: Grams = None
    carb_goal: Grams = None
    fat_goal: Grams = None
    starting_weight_kg: Optional[WeightKg] = None
    target_weight_kg: Optional[WeightKg] = None


# Names and notes are trimmed first, so a name of only spaces is refused like an empty one.
TRIMMED = ConfigDict(str_strip_whitespace=True)


class WeightCreate(BaseModel):
    model_config = TRIMMED
    date: date
    weight_kg: WeightKg
    note: Optional[str] = Field(default=None, max_length=500)


class SavedFoodCreate(BaseModel):
    model_config = TRIMMED
    name: str = Field(min_length=1, max_length=1000)        # a dish, or its ingredients
    serving_size: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)] = 1
    serving_unit: str = Field(default="serving", min_length=1, max_length=40)
    calories: Optional[Kcal] = None          # optional: a food logged without counting
    protein: Grams = 0
    carbs: Grams = 0
    fat: Grams = 0
    notes: Optional[str] = Field(default=None, max_length=2000)
    tags: list[str] = []

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v):
        return clean_tags(v)


class FoodLogCreate(BaseModel):
    """calories and macros are the totals for all the servings (what the day adds up); calories may be left out
    (null). saved_food_id, when given, must be one of the acting person's own saved foods (422 otherwise); its tags
    are used when none are given. eaten_at: when it was eaten (HH:MM); left out → now for today, else the meal's
    usual time."""
    model_config = TRIMMED
    date: date
    meal_type: MealType = "snack"
    food_name: str = Field(min_length=1, max_length=1000)   # a dish, or its ingredients ("rice, dal, ghee")
    servings: Servings = 1
    calories: Optional[Kcal] = None
    protein: Grams = 0
    carbs: Grams = 0
    fat: Grams = 0
    saved_food_id: Optional[Annotated[int, Field(ge=1, le=2**62)]] = None
    eaten_at: Optional[ClockTime] = None
    tags: Optional[list[str]] = None

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v):
        return None if v is None else clean_tags(v)


Severity = Annotated[int, Field(ge=1, le=5)]


class SymptomCreate(BaseModel):
    model_config = TRIMMED
    date: date
    time: ClockTime
    symptom: str = Field(min_length=1, max_length=60)
    severity: Severity = 3
    note: Optional[str] = Field(default=None, max_length=500)


class AIEstimateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class AIEstimate(BaseModel):
    """What "✨ Estimate with AI" hands the page: the model's answer, checked like a food entry; any other keys the
    model adds are dropped, and numbers given as text ("250") are read as numbers."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    food_name: str = Field(min_length=1, max_length=200)
    serving_size: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)] = 1
    serving_unit: str = Field(default="serving", min_length=1, max_length=40)
    calories: Kcal
    protein: Grams = 0
    carbs: Grams = 0
    fat: Grams = 0
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("food_name", "serving_unit", "note", mode="before")
    @classmethod
    def _shorten(cls, v, info):
        """A long-winded model: its words are cut to the field's length rather than the estimate refused."""
        limit = {"food_name": 200, "serving_unit": 40, "note": 500}[info.field_name]
        return v[:limit] if isinstance(v, str) else v
