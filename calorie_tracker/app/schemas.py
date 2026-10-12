from datetime import date
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field

# Bounds on every number a page or script sends: no negatives, nothing absurd, and never inf/NaN
# (JSON like 1e309 parses as infinity, which would be stored and then break every later answer).
Kcal = Annotated[float, Field(ge=0, le=50_000, allow_inf_nan=False)]
Grams = Annotated[float, Field(ge=0, le=5_000, allow_inf_nan=False)]
WeightKg = Annotated[float, Field(gt=0, le=700, allow_inf_nan=False)]
Servings = Annotated[float, Field(gt=0, le=1_000, allow_inf_nan=False)]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]


class GoalUpdate(BaseModel):
    # Left out = unchanged. The four goals can't be null (an explicit null isn't a goal: 422);
    # the two weights can (null clears them).
    calorie_goal: Annotated[float, Field(ge=0, le=20_000, allow_inf_nan=False)] = None
    protein_goal: Grams = None
    carb_goal: Grams = None
    fat_goal: Grams = None
    starting_weight_kg: Optional[WeightKg] = None
    target_weight_kg: Optional[WeightKg] = None


class WeightCreate(BaseModel):
    date: date
    weight_kg: WeightKg
    note: Optional[str] = Field(default=None, max_length=500)


class SavedFoodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    serving_size: Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)] = 1
    serving_unit: str = Field(default="serving", min_length=1, max_length=40)
    calories: Kcal
    protein: Grams = 0
    carbs: Grams = 0
    fat: Grams = 0
    notes: Optional[str] = Field(default=None, max_length=2000)


class FoodLogCreate(BaseModel):
    """calories and macros are the totals for all the servings (what the day adds up)."""
    date: date
    meal_type: MealType = "snack"
    food_name: str = Field(min_length=1, max_length=200)
    servings: Servings = 1
    calories: Kcal
    protein: Grams = 0
    carbs: Grams = 0
    fat: Grams = 0
    saved_food_id: Optional[int] = None


class AIEstimateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
