from datetime import date
from typing import Optional
from pydantic import BaseModel, Field


class GoalUpdate(BaseModel):
    calorie_goal: Optional[float] = None
    protein_goal: Optional[float] = None
    carb_goal: Optional[float] = None
    fat_goal: Optional[float] = None
    starting_weight_kg: Optional[float] = None
    target_weight_kg: Optional[float] = None


class WeightCreate(BaseModel):
    date: date
    weight_kg: float
    note: Optional[str] = None


class SavedFoodCreate(BaseModel):
    name: str
    serving_size: float = 1
    serving_unit: str = "serving"
    calories: float
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    notes: Optional[str] = None


class FoodLogCreate(BaseModel):
    date: date
    meal_type: str = "snack"
    food_name: str
    servings: float = 1
    calories: float
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    saved_food_id: Optional[int] = None


class AIEstimateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=2000)


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
