def epley_1rm(weight: float, reps: int) -> float:
    """Estimated one-rep max via the Epley formula. Reps of 0 or 1 return the weight as-is."""
    if reps <= 1:
        return weight
    return weight * (1 + reps / 30.0)
