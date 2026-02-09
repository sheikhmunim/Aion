"""
Agent module for Calendar App.
Provides AI agent capabilities including constraint-based scheduling with Clingo.
"""

from .asp_model import ASPModel
from .solver import ScheduleSolver

__all__ = ["ASPModel", "ScheduleSolver"]
